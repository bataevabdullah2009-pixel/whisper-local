import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from model_manager import CPP_CATALOG, catalog_for, model_backend, model_size, validate_model, download_model
from whispercpp_worker import load_cpp_model
from whispercpp_backend import WhisperCpp


def fixture_model(path, ftype=1):
    path.write_bytes(struct.pack("<12i", 0x67676D6C, 51864, 1500, 384, 6, 4, 448, 384, 6, 4, 80, ftype) + b"fixture")


class ModelFormats(unittest.TestCase):
    def test_ggml_fp16_and_q8_imports_and_backend_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "модель.bin"
            for ftype in (1, 2007):
                fixture_model(path, ftype)
                self.assertEqual(validate_model(path), path.resolve())
                self.assertEqual(model_backend(path), "whispercpp")
            self.assertEqual(model_backend(directory), "faster-whisper")

    def test_gguf_short_and_invalid_headers_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.bin"
            for data in (b"GGUF" + b"x" * 80, b"lmgg", struct.pack("<12i", 0x67676D6C, *([0] * 11)) + b"x"):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    validate_model(path)

    def test_pinned_cpp_catalog_is_separate_and_complete(self):
        self.assertEqual({m["id"] for m in CPP_CATALOG}, {"base", "small", "turbo"})
        for model in CPP_CATALOG:
            self.assertRegex(model["revision"], r"^[a-f0-9]{40}$")
            self.assertEqual(len(model["files"]), 1)
            file = model["files"][0]
            self.assertRegex(file["sha256"], r"^[a-f0-9]{64}$")
            self.assertEqual(model_size(model["id"], "whispercpp"), file["size"])
        with self.assertRaises(ValueError):
            catalog_for("unknown")

    def test_cpp_download_returns_file_without_overwriting_ct2_directory(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "template.bin"
            fixture_model(template)
            data = template.read_bytes()
            model = {"id": "base", "revision": "a" * 40, "repo": "test/model", "files": [{
                "name": "ggml-base.bin", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}]}
            legacy = root / ("base-" + "a" * 12)
            legacy.mkdir()
            (legacy / "model.bin").write_bytes(b"preserve")
            def download(url, path, *args):
                path.write_bytes(data)
            with patch("model_manager.CPP_CATALOG", [model]), patch("model_manager.download_file", side_effect=download):
                path = download_model("base", root, lambda event: None, "whispercpp")
            self.assertTrue(path.is_file())
            self.assertTrue(path.parent.name.startswith("whispercpp-"))
            self.assertEqual((legacy / "model.bin").read_bytes(), b"preserve")


class BackendReadiness(unittest.TestCase):
    def model(self, metal=True, failure=False):
        model = Mock(metal=metal, compute_type="float16")
        if failure:
            model.transcribe.side_effect = RuntimeError("kernel unavailable")
        return model

    def test_mac_metal_is_ready_only_after_actual_warmup(self):
        model = self.model()
        with patch("sys.platform", "darwin"), patch("platform.machine", return_value="arm64"):
            result, device, compute, fallback = load_cpp_model("model.bin", "auto", 4, lambda *a: model)
        self.assertEqual((device, compute, fallback), ("metal", "float16", False))
        self.assertIs(result, model)
        self.assertEqual(model.transcribe.call_args.args[0].shape, (16000,))

    def test_failed_gpu_kernel_closes_context_and_retries_cpu(self):
        gpu, cpu = self.model(failure=True), self.model(metal=False)
        factory = Mock(side_effect=[gpu, cpu])
        with patch("sys.platform", "darwin"):
            _, device, _, fallback = load_cpp_model("model.bin", "metal", 4, factory)
        self.assertEqual((device, fallback), ("cpu", True))
        gpu.close.assert_called_once()
        self.assertEqual([call.args[1] for call in factory.call_args_list], [True, False])

    def test_enumeration_without_active_metal_context_reports_cpu(self):
        model = self.model(metal=False)
        with patch("sys.platform", "darwin"), patch("platform.machine", return_value="arm64"):
            _, device, _, fallback = load_cpp_model("model.bin", "auto", 4, lambda *a: model)
        self.assertEqual((device, fallback), ("cpu", True))

    def test_intel_auto_prefers_cpu_but_explicit_metal_is_available(self):
        factory = Mock(return_value=self.model(metal=False))
        with patch("sys.platform", "darwin"), patch("platform.machine", return_value="x86_64"):
            _, device, _, fallback = load_cpp_model("model.bin", "auto", 4, factory)
            self.assertEqual((device, fallback), ("cpu", False))
            self.assertFalse(factory.call_args.args[1])
            load_cpp_model("model.bin", "metal", 4, factory)
            self.assertTrue(factory.call_args.args[1])

    def test_forced_cpu_never_initializes_metal(self):
        factory = Mock(return_value=self.model(metal=False))
        with patch("sys.platform", "darwin"):
            load_cpp_model("model.bin", "cpu", 4, factory)
        self.assertFalse(factory.call_args.args[1])

    def test_cpu_failure_is_fatal_and_context_is_closed(self):
        model = self.model(metal=False, failure=True)
        with self.assertRaises(RuntimeError):
            load_cpp_model("model.bin", "cpu", 4, lambda *a: model)
        model.close.assert_called_once()


class BridgeABI(unittest.TestCase):
    def library(self):
        library = Mock()
        library.wl_abi_version.return_value = 1
        library.wl_create.return_value = 42
        library.wl_uses_metal.return_value = 1
        library.wl_ftype.return_value = 7
        library.wl_transcribe.return_value = 0
        library.wl_text.return_value = b"fixture"
        return library

    def test_binding_copies_text_then_clears_native_buffer_and_closes_once(self):
        library = self.library()
        with patch("whispercpp_backend.bridge_path", return_value=Path("bridge")), patch("ctypes.CDLL", return_value=library):
            model = WhisperCpp("модель.bin", True, 4)
        self.assertEqual(model.compute_type, "q8_0")
        self.assertEqual(model.transcribe(np.zeros(8000, dtype=np.float32), "ru"), "fixture")
        library.wl_clear_text.assert_called_once_with(42)
        model.close()
        model.close()
        library.wl_free.assert_called_once_with(42)

    def test_failed_inference_still_clears_decoded_buffer(self):
        library = self.library()
        library.wl_transcribe.return_value = -2
        with patch("whispercpp_backend.bridge_path", return_value=Path("bridge")), patch("ctypes.CDLL", return_value=library):
            model = WhisperCpp("model.bin", False, 4)
        with self.assertRaises(RuntimeError):
            model.transcribe(np.zeros(8000, dtype=np.float32), "en")
        library.wl_clear_text.assert_called_once()
        model.close()

    def test_wrong_native_abi_is_rejected_before_allocating_model(self):
        library = self.library()
        library.wl_abi_version.return_value = 2
        with patch("whispercpp_backend.bridge_path", return_value=Path("bridge")), patch("ctypes.CDLL", return_value=library), self.assertRaises(RuntimeError):
            WhisperCpp("model.bin", True, 4)
        library.wl_create.assert_not_called()
