"""Explicit, resumable model downloads. Recognition never imports this downloader."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import struct
import time
import urllib.error
import urllib.request
import certifi

CATALOG = json.loads((Path(__file__).with_name("model_catalog.json")).read_text(encoding="utf-8"))
MODELS = {model["id"]: model for model in CATALOG}
CPP_CATALOG = json.loads((Path(__file__).with_name("whispercpp_catalog.json")).read_text(encoding="utf-8"))
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
DOWNLOAD_ATTEMPTS = 3
DISK_RESERVE = 32 * 1024 * 1024


class _DownloadInterrupted(Exception):
    """Only failures that can be retried without discarding saved bytes."""

    def __init__(self, retry_after=None):
        self.retry_after = retry_after


def _transient_http(status):
    return status in (408, 429) or 500 <= status <= 599


def _retry_delay(attempt, retry_after):
    # Bound server-requested delays so an untrusted header cannot stall the UI.
    try:
        delay = int(retry_after)
    except (TypeError, ValueError):
        delay = 0
    return min(30, max(2 ** attempt, delay))


def _ensure_disk_space(directory, remaining):
    if remaining and shutil.disk_usage(directory).free < remaining + DISK_RESERVE:
        raise ValueError(f"Недостаточно места. Освободите около {remaining / 1024**3:.1f} ГБ и повторите.")


@contextmanager
def _model_download_lock(directory):
    """The OS releases this worker-owned lock even after an abrupt process exit."""
    # Keep the inode: unlinking a lock file lets another worker create and lock
    # a different file while a previous worker still owns the original lock.
    with (directory / ".download.lock").open("a+b") as lock:
        if not lock.seek(0, os.SEEK_END):
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            raise ValueError("Эта модель уже скачивается другим процессом. Дождитесь завершения и повторите.") from None
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _response_end(response, offset, size):
    """Validate the representation and every bound before touching saved data."""
    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
        raise ValueError("Сервер вернул неподдерживаемое сжатие файла. Повторите загрузку.")
    if response.status == 206:
        match = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)",
                             response.headers.get("Content-Range", "").strip())
        if match is None:
            raise ValueError("Сервер вернул неверный диапазон файла. Повторите загрузку.")
        start, end, total = map(int, match.groups())
        if start != offset or not start <= end < total or total != size:
            raise ValueError("Сервер вернул неверный диапазон файла. Повторите загрузку.")
    elif response.status == 200:
        offset, end = 0, size - 1
    elif _transient_http(response.status):
        raise _DownloadInterrupted(response.headers.get("Retry-After"))
    else:
        raise ValueError(f"Сервер вернул HTTP {response.status}.")
    length = response.headers.get("Content-Length")
    if length is not None and (not re.fullmatch(r"[0-9]+", length.strip())
                               or int(length) != end - offset + 1):
        raise ValueError("Размер загрузки не совпадает с каталогом моделей.")
    return offset, end


def catalog_for(backend):
    if backend == "whispercpp":
        return CPP_CATALOG
    if backend == "faster-whisper":
        return CATALOG
    raise ValueError("Unknown recognition backend")


def model_backend(path):
    return "whispercpp" if str(path) and Path(path).is_file() else "faster-whisper"


def model_size(model_id: str, backend="faster-whisper") -> int:
    return sum(file["size"] for model in catalog_for(backend) if model["id"] == model_id for file in model["files"])


def validate_model(path: str | Path) -> Path:
    if not str(path):
        raise ValueError("Сначала скачайте модель или выберите папку с готовой моделью.")
    directory = Path(path).expanduser().resolve()
    if directory.is_file():
        with directory.open("rb") as file:
            header = file.read(48)
        if len(header) != 48 or struct.unpack("<I", header[:4])[0] != 0x67676D6C:
            raise ValueError("Нужен файл модели whisper.cpp GGML .bin. GGUF и OpenAI .pt не поддерживаются.")
        values = struct.unpack("<11i", header[4:])
        if (not 1000 <= values[0] <= 200000 or not all(0 < v <= 16384 for v in values[1:9])
                or values[9] not in (80, 128) or not 0 <= values[10] % 1000 <= 40
                or directory.stat().st_size <= 48):
            raise ValueError("Повреждён заголовок модели whisper.cpp.")
        return directory
    for name in ("model.bin", "config.json", "tokenizer.json"):
        file = directory / name
        if not file.is_file() or not file.stat().st_size:
            raise ValueError(f"В папке нет {name}. Нужна модель в формате faster-whisper / CTranslate2.")
    for name in ("config.json", "tokenizer.json"):
        try:
            if not isinstance(json.loads((directory / name).read_text(encoding="utf-8")), dict):
                raise ValueError()
        except (ValueError, UnicodeError) as error:
            raise ValueError(f"Повреждён файл {name}. Скачайте модель заново.") from error
    return directory


def matches(path: Path, file: dict) -> bool:
    if not path.is_file() or path.stat().st_size != file["size"]:
        return False
    if "sha256" in file:
        digest = hashlib.sha256()
        expected = file["sha256"]
    else:
        digest = hashlib.sha1()
        digest.update(f"blob {file['size']}\0".encode("ascii"))
        expected = file["git_sha1"]
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected


def open_download(request, timeout):
    return urllib.request.urlopen(request, timeout=timeout,
                                  context=ssl.create_default_context(cafile=certifi.where()))


def download_file(url: str, path: Path, file: dict, progress, opener=open_download) -> None:
    """Persist each received chunk; only a verified file is committed atomically."""
    partial = path.with_name(path.name + ".part")
    if matches(partial, file):
        partial.replace(path)
        progress(file["size"])
        return
    for attempt in range(DOWNLOAD_ATTEMPTS):
        offset = partial.stat().st_size if partial.is_file() else 0
        if offset >= file["size"]:
            partial.unlink()
            offset = 0
        request = urllib.request.Request(url, headers={"User-Agent": "WhisperLocal/0.1", "Accept-Encoding": "identity"})
        if offset:
            request.add_header("Range", f"bytes={offset}-")
        retry_after = None
        try:
            try:
                response = opener(request, timeout=30)
            except urllib.error.HTTPError as error:
                status, headers = error.code, error.headers
                error.close()
                if _transient_http(status):
                    raise _DownloadInterrupted(headers.get("Retry-After") if headers else None) from error
                raise ValueError(f"Сервер вернул HTTP {status}.") from error
            except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
                raise _DownloadInterrupted() from error
            with response:
                offset, end = _response_end(response, offset, file["size"])
                with partial.open("ab" if offset else "wb") as output:
                    # A 200 response has just discarded the old partial. Check after
                    # truncation so its reclaimed bytes count toward available space.
                    _ensure_disk_space(path.parent, file["size"] - offset)
                    progress(offset)
                    def save(chunk):
                        nonlocal offset
                        if offset + len(chunk) > end + 1:
                            raise ValueError("Размер загрузки не совпадает с каталогом моделей.")
                        output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                        offset += len(chunk)
                        progress(offset)
                    while True:
                        try:
                            chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                        except http.client.IncompleteRead as error:
                            # Chunked HTTP can carry the last received bytes in the
                            # exception rather than returning them from read().
                            if error.partial:
                                save(error.partial)
                            raise _DownloadInterrupted() from error
                        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
                            raise _DownloadInterrupted() from error
                        if not chunk:
                            break
                        save(chunk)
                    if offset != end + 1 or offset < file["size"]:
                        raise _DownloadInterrupted()
            if matches(partial, file):
                partial.replace(path)
                return
            if partial.stat().st_size == file["size"]:
                partial.unlink()
            if attempt == DOWNLOAD_ATTEMPTS - 1:
                raise ValueError("Проверка целостности не пройдена. Повторите загрузку модели.")
        except _DownloadInterrupted as error:
            # Missing chunked HTTP framing can occur after all model bytes
            # arrived. The pinned checksum still proves this file is complete.
            if matches(partial, file):
                partial.replace(path)
                progress(file["size"])
                return
            retry_after = error.retry_after
            if attempt == DOWNLOAD_ATTEMPTS - 1:
                raise RuntimeError("Не удалось скачать модель. Проверьте соединение и повторите: загрузка продолжится.") from None
        time.sleep(_retry_delay(attempt, retry_after))


def download_model(model_id: str, root: Path, emit, backend="faster-whisper") -> Path:
    model = next(model for model in catalog_for(backend) if model["id"] == model_id)
    prefix = "whispercpp-" if backend == "whispercpp" else ""
    directory = root / f"{prefix}{model_id}-{model['revision'][:12]}"
    directory.mkdir(parents=True, exist_ok=True)
    with _model_download_lock(directory):
        return _download_model_files(model, directory, emit, backend)


def _download_model_files(model, directory, emit, backend):
    total = sum(file["size"] for file in model["files"])
    verified = {}
    needed = 0
    for file in model["files"]:
        emit({"type": "progress", "phase": "verify", "file": file["name"], "done": 0, "total": total})
        verified[file["name"]] = matches(directory / file["name"], file)
        if not verified[file["name"]]:
            partial = directory / (file["name"] + ".part")
            offset = partial.stat().st_size if partial.is_file() else 0
            if offset >= file["size"]:
                if matches(partial, file):
                    offset = file["size"]
                else:
                    partial.unlink()
                    offset = 0
            needed += file["size"] - offset
    _ensure_disk_space(directory, needed)
    completed = 0
    last_update = 0.0
    last_value = -1
    for file in model["files"]:
        path = directory / file["name"]
        emit({"type": "progress", "phase": "verify", "file": file["name"], "done": completed, "total": total})
        if not verified[file["name"]]:
            def progress(value):
                nonlocal last_update, last_value
                now = time.monotonic()
                absolute = completed + value
                if now - last_update > .1 or value == file["size"] or absolute < last_value:
                    emit({"type": "progress", "phase": "download", "file": file["name"],
                          "done": absolute, "total": total})
                    last_update = now
                    last_value = absolute
            url = f"https://huggingface.co/{model['repo']}/resolve/{model['revision']}/{file['name']}?download=true"
            download_file(url, path, file, progress)
        completed += file["size"]
    result = directory / model["files"][0]["name"] if backend == "whispercpp" else directory
    return validate_model(result)
