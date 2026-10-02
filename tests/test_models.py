import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from model_manager import CATALOG, DISK_RESERVE, download_file, download_model, matches, validate_model


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status, self.headers = status, headers or {}


class IncompleteResponse(Response):
    def read(self, size=-1):
        raise http.client.IncompleteRead(self.getvalue(), 100)


class Downloads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "model.bin"
        self.data = b"complete model contents"
        self.file = {"name": "model.bin", "size": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest()}

    def test_resume_range(self):
        self.path.with_suffix(".bin.part").write_bytes(self.data[:8])
        def open_range(request, timeout):
            self.assertEqual(request.get_header("Range"), "bytes=8-")
            return Response(self.data[8:], 206, {"Content-Range": f"bytes 8-{len(self.data)-1}/{len(self.data)}"})
        download_file("https://example.invalid/model", self.path, self.file, lambda n: None, open_range)
        self.assertEqual(self.path.read_bytes(), self.data)
        self.assertFalse(self.path.with_suffix(".bin.part").exists())

    def test_server_ignoring_range_replaces_partial(self):
        self.path.with_suffix(".bin.part").write_bytes(b"old")
        download_file("https://example.invalid/model", self.path, self.file, lambda n: None,
                      lambda *a, **kw: Response(self.data))
        self.assertTrue(matches(self.path, self.file))

    def test_corrupt_download_never_replaces_existing_file(self):
        self.path.write_bytes(b"previous good model")
        with patch("model_manager.time.sleep"), self.assertRaises(ValueError):
            download_file("https://example.invalid/model", self.path, self.file, lambda n: None,
                          lambda *a, **kw: Response(b"x" * len(self.data)))
        self.assertEqual(self.path.read_bytes(), b"previous good model")

    def test_verified_partial_finishes_without_network(self):
        self.path.with_suffix(".bin.part").write_bytes(self.data)
        download_file("https://example.invalid/model", self.path, self.file, lambda n: None,
                      lambda *a, **kw: self.fail("must not contact server"))
        self.assertTrue(matches(self.path, self.file))

    def test_wrong_range_is_rejected(self):
        self.path.with_suffix(".bin.part").write_bytes(self.data[:8])
        with self.assertRaises(ValueError):
            download_file("https://example.invalid/model", self.path, self.file, lambda n: None,
                          lambda *a, **kw: Response(self.data, 206, {"Content-Range": "bytes 0-22/23"}))
        self.assertFalse(self.path.exists())

    def test_all_range_bounds_are_validated_before_partial_is_changed(self):
        partial = self.path.with_suffix(".bin.part")
        for content_range in ("bytes 8-7/23", "bytes 8-23/23", "bytes 8-22/24",
                              "bytes 8-22/*", "bytes 8-22/23 extra", "items 8-22/23"):
            with self.subTest(content_range=content_range):
                partial.write_bytes(self.data[:8])
                with self.assertRaisesRegex(ValueError, "диапазон"):
                    download_file("https://example.invalid/model", self.path, self.file, lambda n: None,
                                  lambda *a, **kw: Response(self.data[8:], 206, {"Content-Range": content_range}))
                self.assertEqual(partial.read_bytes(), self.data[:8])
                self.assertFalse(self.path.exists())

    def test_response_body_cannot_exceed_its_declared_range(self):
        partial = self.path.with_suffix(".bin.part")
        partial.write_bytes(self.data[:8])
        with self.assertRaisesRegex(ValueError, "Размер"):
            download_file("https://example.invalid/model", self.path, self.file, lambda n: None,
                          lambda *a, **kw: Response(self.data[8:], 206, {"Content-Range": "bytes 8-12/23"}))
        self.assertEqual(partial.read_bytes(), self.data[:8])

    def test_content_length_is_validated_before_ignored_range_reset(self):
        partial = self.path.with_suffix(".bin.part")
        partial.write_bytes(self.data[:8])
        with self.assertRaisesRegex(ValueError, "Размер"):
            download_file("https://example.invalid/model", self.path, self.file, lambda n: None,
                          lambda *a, **kw: Response(self.data, headers={"Content-Length": "24"}))
        self.assertEqual(partial.read_bytes(), self.data[:8])

    def test_short_valid_range_continues_at_next_byte(self):
        self.path.with_suffix(".bin.part").write_bytes(self.data[:8])
        ranges = []
        def open_range(request, timeout):
            ranges.append(request.get_header("Range"))
            if len(ranges) == 1:
                return Response(self.data[8:13], 206, {"Content-Range": "bytes 8-12/23"})
            return Response(self.data[13:], 206, {"Content-Range": "bytes 13-22/23"})
        with patch("model_manager.time.sleep"):
            download_file("https://example.invalid/model", self.path, self.file, lambda n: None, open_range)
        self.assertEqual(ranges, ["bytes=8-", "bytes=13-"])
        self.assertTrue(matches(self.path, self.file))

    def test_incomplete_read_bytes_are_persisted_before_retry(self):
        ranges = []
        progress = []
        def open_range(request, timeout):
            ranges.append(request.get_header("Range"))
            if len(ranges) == 1:
                return IncompleteResponse(self.data[:8])
            return Response(self.data[8:], 206, {"Content-Range": "bytes 8-22/23"})
        with patch("model_manager.time.sleep"), patch("model_manager.os.fsync", wraps=os.fsync) as fsync:
            download_file("https://example.invalid/model", self.path, self.file, progress.append, open_range)
        self.assertEqual(ranges, [None, "bytes=8-"])
        self.assertIn(8, progress)
        self.assertGreaterEqual(fsync.call_count, 2)
        self.assertTrue(matches(self.path, self.file))

    def test_complete_payload_with_interrupted_framing_promotes_without_redownload(self):
        requests = []
        def interrupted(request, timeout):
            requests.append(request)
            return IncompleteResponse(self.data)
        with patch("model_manager.time.sleep") as sleep:
            download_file("https://example.invalid/model", self.path, self.file, lambda n: None, interrupted)
        self.assertEqual(len(requests), 1)
        sleep.assert_not_called()
        self.assertTrue(matches(self.path, self.file))

    def test_exhausted_network_failures_keep_partial_for_next_run(self):
        requests = []
        def interrupted(request, timeout):
            requests.append(request.get_header("Range"))
            if len(requests) == 1:
                return IncompleteResponse(self.data[:8])
            raise urllib.error.URLError("connection interrupted")
        with patch("model_manager.time.sleep"), self.assertRaisesRegex(RuntimeError, "продолжится"):
            download_file("https://example.invalid/model", self.path, self.file, lambda n: None, interrupted)
        self.assertEqual(requests, [None, "bytes=8-", "bytes=8-"])
        self.assertEqual(self.path.with_suffix(".bin.part").read_bytes(), self.data[:8])
        self.assertFalse(self.path.exists())

    def test_transient_http_errors_retry_with_bounded_backoff(self):
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                if self.path.exists():
                    self.path.unlink()
                requests = []
                def transient(request, timeout):
                    requests.append(request)
                    if len(requests) == 1:
                        raise urllib.error.HTTPError(request.full_url, status, "transient", {"Retry-After": "999999"}, None)
                    return Response(self.data)
                with patch("model_manager.time.sleep") as sleep:
                    download_file("https://example.invalid/model", self.path, self.file, lambda n: None, transient)
                self.assertEqual(len(requests), 2)
                sleep.assert_called_once_with(30)
                self.assertTrue(matches(self.path, self.file))

    def test_permanent_http_errors_do_not_retry_or_discard_partial(self):
        partial = self.path.with_suffix(".bin.part")
        for status in (400, 401, 403, 404, 410, 416):
            with self.subTest(status=status):
                partial.write_bytes(self.data[:8])
                def permanent(request, timeout):
                    raise urllib.error.HTTPError(request.full_url, status, "permanent", {}, None)
                with patch("model_manager.time.sleep") as sleep, self.assertRaisesRegex(ValueError, f"HTTP {status}"):
                    download_file("https://example.invalid/model", self.path, self.file, lambda n: None, permanent)
                sleep.assert_not_called()
                self.assertEqual(partial.read_bytes(), self.data[:8])

    def test_transient_http_retries_stop_after_three_attempts(self):
        requests = []
        def unavailable(request, timeout):
            requests.append(request)
            raise urllib.error.HTTPError(request.full_url, 503, "unavailable", {}, None)
        with patch("model_manager.time.sleep") as sleep, self.assertRaises(RuntimeError):
            download_file("https://example.invalid/model", self.path, self.file, lambda n: None, unavailable)
        self.assertEqual(len(requests), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    def test_disk_check_counts_reclaimed_partial_when_range_is_ignored(self):
        partial = self.path.with_suffix(".bin.part")
        partial.write_bytes(self.data[:8])
        def disk_usage(directory):
            # The original eight bytes are occupying the only available space.
            saved = partial.stat().st_size if partial.exists() else 0
            return SimpleNamespace(free=DISK_RESERVE + len(self.data) - saved)
        with patch("model_manager.shutil.disk_usage", side_effect=disk_usage):
            download_file("https://example.invalid/model", self.path, self.file, lambda n: None,
                          lambda *a, **kw: Response(self.data))
        self.assertTrue(matches(self.path, self.file))

    def test_disk_full_keeps_resumable_partial_and_existing_model(self):
        partial = self.path.with_suffix(".bin.part")
        partial.write_bytes(self.data[:8])
        self.path.write_bytes(b"existing model")
        with patch("model_manager.shutil.disk_usage", return_value=SimpleNamespace(free=0)), self.assertRaisesRegex(ValueError, "места"):
            download_file("https://example.invalid/model", self.path, self.file, lambda n: None,
                          lambda *a, **kw: Response(self.data[8:], 206, {"Content-Range": "bytes 8-22/23"}))
        self.assertEqual(partial.read_bytes(), self.data[:8])
        self.assertEqual(self.path.read_bytes(), b"existing model")

    def model_fixture(self):
        payloads = {"model.bin": self.data, "config.json": b"{}", "tokenizer.json": b"{}"}
        model = {"id": "local-test", "revision": "a" * 40, "repo": "local/test", "files": [
            {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in payloads.items()]}
        directory = Path(self.tmp.name) / "local-test-aaaaaaaaaaaa"
        directory.mkdir()
        return model, directory, payloads

    def test_model_preflight_reclaims_full_corrupt_partial_before_disk_check(self):
        model, directory, payloads = self.model_fixture()
        (directory / "model.bin.part").write_bytes(b"x" * len(self.data))
        total = sum(map(len, payloads.values()))
        def disk_usage(path):
            used = sum(file.stat().st_size for file in directory.iterdir() if file.name != ".download.lock")
            return SimpleNamespace(free=DISK_RESERVE + total - used)
        def download(url, path, file, progress):
            download_file(url, path, file, progress, lambda *a, **kw: Response(payloads[file["name"]]))
        with patch("model_manager.catalog_for", return_value=[model]), patch("model_manager.download_file", side_effect=download), patch("model_manager.shutil.disk_usage", side_effect=disk_usage):
            result = download_model(model["id"], Path(self.tmp.name), lambda event: None)
        self.assertEqual(result, directory.resolve())
        self.assertFalse((directory / "model.bin.part").exists())

    def test_verified_model_partials_promote_offline_without_free_space(self):
        model, directory, payloads = self.model_fixture()
        for name, data in payloads.items():
            (directory / (name + ".part")).write_bytes(data)
        with patch("model_manager.catalog_for", return_value=[model]), patch("model_manager.shutil.disk_usage", return_value=SimpleNamespace(free=0)), patch("model_manager.urllib.request.urlopen", side_effect=AssertionError("must stay offline")):
            result = download_model(model["id"], Path(self.tmp.name), lambda event: None)
        self.assertEqual(result, directory.resolve())
        self.assertFalse(list(directory.glob("*.part")))

    def test_model_lock_rejects_concurrent_writer_and_releases_after_process_kill(self):
        model, directory, payloads = self.model_fixture()
        partial = directory / "model.bin.part"
        partial.write_bytes(self.data[:8])
        checkpoint = Path(self.tmp.name) / "lock-held"
        script = """
from pathlib import Path
import sys
import time
from model_manager import _model_download_lock
with _model_download_lock(Path(sys.argv[1])):
    Path(sys.argv[2]).write_text('held', encoding='ascii')
    time.sleep(120)
"""
        child = subprocess.Popen([sys.executable, "-c", script, str(directory), str(checkpoint)],
                                 cwd=Path(__file__).parents[1], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 15
            while not checkpoint.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(checkpoint.exists(), "subprocess did not acquire the model lock")
            with patch("model_manager.catalog_for", return_value=[model]), patch("model_manager.download_file") as downloader, self.assertRaisesRegex(ValueError, "другим процессом"):
                download_model(model["id"], Path(self.tmp.name), lambda event: self.fail("verification must not start"))
            downloader.assert_not_called()
            self.assertEqual(partial.read_bytes(), self.data[:8])
            child.terminate()
            child.wait(timeout=10)
            def download(url, path, file, progress):
                def open_range(request, timeout):
                    offset = int(request.get_header("Range")[6:-1]) if request.get_header("Range") else 0
                    data = payloads[file["name"]]
                    return Response(data[offset:], 206 if offset else 200,
                                    {"Content-Range": f"bytes {offset}-{len(data)-1}/{len(data)}"} if offset else {})
                download_file(url, path, file, progress, open_range)
            with patch("model_manager.catalog_for", return_value=[model]), patch("model_manager.download_file", side_effect=download):
                result = download_model(model["id"], Path(self.tmp.name), lambda event: None)
            self.assertEqual(result, directory.resolve())
            self.assertTrue((directory / ".download.lock").exists())
            self.assertFalse(partial.exists())
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)

    def local_server(self, respond):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.headers.get("Range"))
                try:
                    respond(self, requests[-1], len(requests))
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.addCleanup(cleanup)
        return f"http://127.0.0.1:{server.server_port}/model", requests

    def test_real_http_disconnect_resumes_received_bytes(self):
        def respond(handler, requested_range, attempt):
            offset = int(requested_range[6:-1]) if requested_range else 0
            handler.send_response(206 if offset else 200)
            handler.send_header("Content-Length", str(len(self.data) - offset))
            if offset:
                handler.send_header("Content-Range", f"bytes {offset}-22/23")
            handler.end_headers()
            handler.wfile.write(self.data[:8] if attempt == 1 else self.data[offset:])
            handler.wfile.flush()
            handler.close_connection = True
        url, requests = self.local_server(respond)
        with patch("model_manager.time.sleep"):
            download_file(url, self.path, self.file, lambda n: None, urllib.request.urlopen)
        self.assertEqual(requests, [None, "bytes=8-"])
        self.assertTrue(matches(self.path, self.file))

    def test_real_chunked_disconnect_preserves_incomplete_read_payload(self):
        def respond(handler, requested_range, attempt):
            if attempt == 1:
                handler.send_response(200)
                handler.send_header("Transfer-Encoding", "chunked")
                handler.end_headers()
                handler.wfile.write(b"8\r\n" + self.data[:8] + b"\r\n")
            else:
                handler.send_response(206)
                handler.send_header("Content-Length", "15")
                handler.send_header("Content-Range", "bytes 8-22/23")
                handler.end_headers()
                handler.wfile.write(self.data[8:])
            handler.wfile.flush()
            handler.close_connection = True
        url, requests = self.local_server(respond)
        with patch("model_manager.time.sleep"):
            download_file(url, self.path, self.file, lambda n: None, urllib.request.urlopen)
        self.assertEqual(requests, [None, "bytes=8-"])
        self.assertTrue(matches(self.path, self.file))

    def test_real_http_transient_error_then_range_ignored_safely_restarts(self):
        self.path.with_suffix(".bin.part").write_bytes(self.data[:8])
        def respond(handler, requested_range, attempt):
            if attempt == 1:
                handler.send_response(503)
                handler.send_header("Content-Length", "0")
                handler.end_headers()
            else:
                handler.send_response(200)
                handler.send_header("Content-Length", str(len(self.data)))
                handler.end_headers()
                handler.wfile.write(self.data)
        url, requests = self.local_server(respond)
        with patch("model_manager.time.sleep"):
            download_file(url, self.path, self.file, lambda n: None, urllib.request.urlopen)
        self.assertEqual(requests, ["bytes=8-", "bytes=8-"])
        self.assertTrue(matches(self.path, self.file))

    def test_killed_process_resumes_durable_partial_in_new_process(self):
        data = bytes(range(256)) * (3 * 4096)
        file = {"name": "model.bin", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        def respond(handler, requested_range, attempt):
            offset = int(requested_range[6:-1]) if requested_range else 0
            handler.send_response(206 if offset else 200)
            handler.send_header("Content-Length", str(len(data) - offset))
            if offset:
                handler.send_header("Content-Range", f"bytes {offset}-{len(data)-1}/{len(data)}")
            handler.end_headers()
            handler.wfile.write(data[offset:])
        url, requests = self.local_server(respond)
        checkpoint = Path(self.tmp.name) / "saved-offset"
        script = """
import json
from pathlib import Path
import sys
import time
import urllib.request
from model_manager import download_file
url, destination, metadata, checkpoint = sys.argv[1:]
def progress(value):
    if value and checkpoint:
        # Publish only a fully written checkpoint. The parent can terminate us
        # as soon as it sees the marker, including on a busy Windows runner.
        marker = Path(checkpoint)
        temporary = marker.with_suffix('.tmp')
        temporary.write_text(str(value), encoding='ascii')
        temporary.replace(marker)
        time.sleep(120)
download_file(url, Path(destination), json.loads(metadata), progress, urllib.request.urlopen)
"""
        arguments = [sys.executable, "-c", script, url, str(self.path), json.dumps(file)]
        child = subprocess.Popen(arguments + [str(checkpoint)], cwd=Path(__file__).parents[1],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 15
            while not checkpoint.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(checkpoint.exists(), "download subprocess did not reach its durable checkpoint")
            child.terminate()
            child.wait(timeout=10)
            offset = int(checkpoint.read_text(encoding="ascii"))
            self.assertEqual(offset, 1024 * 1024)
            partial = self.path.with_suffix(".bin.part")
            self.assertEqual(partial.read_bytes(), data[:offset])
            self.assertFalse(self.path.exists())
            restarted = subprocess.run(arguments + [""], cwd=Path(__file__).parents[1],
                                       capture_output=True, text=True, timeout=20)
            self.assertEqual(restarted.returncode, 0, restarted.stderr)
            self.assertEqual(requests, [None, f"bytes={offset}-"])
            self.assertTrue(matches(self.path, file))
            self.assertFalse(partial.exists())
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)

    def test_git_blob_hash_for_small_files(self):
        self.path.write_bytes(self.data)
        blob = hashlib.sha1(f"blob {len(self.data)}\0".encode() + self.data).hexdigest()
        self.assertTrue(matches(self.path, {"size": len(self.data), "git_sha1": blob}))

    def test_catalog_is_pinned_with_checksums(self):
        for model in CATALOG:
            self.assertRegex(model["revision"], r"^[a-f0-9]{40}$")
            names = {file["name"] for file in model["files"]}
            self.assertTrue({"model.bin", "config.json", "tokenizer.json"} <= names)
            for file in model["files"]:
                self.assertEqual(Path(file["name"]).name, file["name"])
                self.assertGreater(file["size"], 0)
                self.assertTrue(file.get("sha256") or file.get("git_sha1"))

    def test_import_rejects_incomplete_model(self):
        self.path.write_bytes(self.data)
        with self.assertRaisesRegex(ValueError, "config.json"):
            validate_model(self.path.parent)


if __name__ == "__main__":
    unittest.main()
