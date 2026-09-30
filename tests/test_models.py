import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model_manager import CATALOG, download_file, matches, validate_model


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status, self.headers = status, headers or {}


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
