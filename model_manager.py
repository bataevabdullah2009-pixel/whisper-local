"""Explicit, resumable model downloads. Recognition never imports this downloader."""
from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path
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
    """A killed process leaves only a .part file; a verified file is committed atomically."""
    partial = path.with_name(path.name + ".part")
    if matches(partial, file):
        partial.replace(path)
        progress(file["size"])
        return
    for attempt in range(3):
        offset = partial.stat().st_size if partial.is_file() else 0
        if offset >= file["size"]:
            partial.unlink()
            offset = 0
        request = urllib.request.Request(url, headers={"User-Agent": "WhisperLocal/0.1", "Accept-Encoding": "identity"})
        if offset:
            request.add_header("Range", f"bytes={offset}-")
        try:
            with opener(request, timeout=30) as response:
                if response.status == 206:
                    content_range = response.headers.get("Content-Range", "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        raise ValueError("Сервер вернул неверный диапазон файла. Повторите загрузку.")
                elif response.status == 200:
                    offset = 0  # A server may ignore Range. Never append a full response.
                else:
                    raise ValueError(f"Сервер вернул HTTP {response.status}.")
                with partial.open("ab" if offset else "wb") as output:
                    progress(offset)
                    while chunk := response.read(1024 * 1024):
                        if offset + len(chunk) > file["size"]:
                            raise ValueError("Размер загрузки не совпадает с каталогом моделей.")
                        output.write(chunk)
                        offset += len(chunk)
                        progress(offset)
            if matches(partial, file):
                partial.replace(path)
                return
            if partial.stat().st_size == file["size"]:
                partial.unlink()
            if attempt == 2:
                raise ValueError("Проверка целостности не пройдена. Повторите загрузку модели.")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, http.client.IncompleteRead):
            if attempt == 2:
                raise RuntimeError("Не удалось скачать модель. Проверьте соединение и повторите: загрузка продолжится.") from None
        time.sleep(attempt + 1)


def download_model(model_id: str, root: Path, emit, backend="faster-whisper") -> Path:
    model = next(model for model in catalog_for(backend) if model["id"] == model_id)
    prefix = "whispercpp-" if backend == "whispercpp" else ""
    directory = root / f"{prefix}{model_id}-{model['revision'][:12]}"
    directory.mkdir(parents=True, exist_ok=True)
    total = model_size(model_id, backend)
    verified = {}
    needed = 0
    for file in model["files"]:
        emit({"type": "progress", "phase": "verify", "file": file["name"], "done": 0, "total": total})
        verified[file["name"]] = matches(directory / file["name"], file)
        if not verified[file["name"]]:
            partial = directory / (file["name"] + ".part")
            offset = partial.stat().st_size if partial.is_file() else 0
            if offset >= file["size"]:
                offset = file["size"] if matches(partial, file) else 0
            needed += file["size"] - offset
    if shutil.disk_usage(directory).free < needed + 32 * 1024 * 1024:
        raise ValueError(f"Недостаточно места. Освободите около {total / 1024**3:.1f} ГБ и повторите.")
    completed = 0
    last_update = 0.0
    for file in model["files"]:
        path = directory / file["name"]
        emit({"type": "progress", "phase": "verify", "file": file["name"], "done": completed, "total": total})
        if not verified[file["name"]]:
            def progress(value):
                nonlocal last_update
                now = time.monotonic()
                if now - last_update > .1 or value == file["size"]:
                    emit({"type": "progress", "phase": "download", "file": file["name"],
                          "done": completed + value, "total": total})
                    last_update = now
            url = f"https://huggingface.co/{model['repo']}/resolve/{model['revision']}/{file['name']}?download=true"
            download_file(url, path, file, progress)
        completed += file["size"]
    result = directory / model["files"][0]["name"] if backend == "whispercpp" else directory
    return validate_model(result)
