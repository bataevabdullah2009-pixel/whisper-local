import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from asr_worker import load_model
import runtime


class Model:
    def transcribe(self, *args, **kwargs):
        return iter(()), None


class Compute(unittest.TestCase):
    def test_recommendation_uses_memory_and_free_gpu_capacity(self):
        self.assertEqual(runtime.recommend_model(8, False, 0), "base")
        self.assertEqual(runtime.recommend_model(16, False, 0), "small")
        self.assertEqual(runtime.recommend_model(16, True, 8000), "turbo")
        self.assertEqual(runtime.recommend_model(16, True, 2000), "small")

    def test_failed_cuda_warmup_falls_back_to_cpu(self):
        devices = []
        class BrokenCUDA(Model):
            def transcribe(self, *a, **kw):
                raise RuntimeError("CUDA kernel missing")
        def factory(path, **options):
            devices.append((options["device"], options["compute_type"]))
            return BrokenCUDA() if options["device"] == "cuda" else Model()
        with patch("sys.platform", "win32"):
            _, device, precision, fallback = load_model("model", "auto", factory, lambda d: {"int8", "float16"}, 4)
        self.assertEqual(devices, [("cuda", "float16"), ("cpu", "int8")])
        self.assertEqual((device, precision, fallback), ("cpu", "int8", True))

    def test_mac_never_tries_cuda(self):
        devices = []
        def factory(path, **options):
            devices.append(options["device"])
            return Model()
        with patch("sys.platform", "darwin"):
            load_model("model", "auto", factory, lambda d: {"int8"}, 4)
        self.assertEqual(devices, ["cpu"])

    def test_cpu_failure_is_not_reported_as_ready(self):
        def fail(*a, **kw):
            raise RuntimeError("model damaged")
        with self.assertRaisesRegex(RuntimeError, "model damaged"):
            load_model("model", "cpu", fail, lambda d: {"int8"}, 2)


class Paths(unittest.TestCase):
    def test_source_worker_is_current_python_not_personal_install(self):
        command = runtime.worker_command("asr", "--model", "test")
        self.assertEqual(Path(command[0]).parent, Path(sys.executable).parent)
        self.assertIn(str(runtime.ROOT / "worker_entry.py"), command)

    def test_frozen_worker_is_bundled_helper(self):
        with patch.object(sys, "frozen", True, create=True), patch("sys.executable", str(Path.cwd() / "WhisperLocal.exe")):
            command = runtime.worker_command("probe")
        self.assertTrue(Path(command[0]).name.startswith("WhisperWorker"))
        self.assertEqual(command[1:], ["probe"])

    def test_defaults_have_no_machine_paths(self):
        config = json.loads((runtime.ROOT / "config.example.json").read_text())
        self.assertEqual(config["model_path"], "")
        self.assertNotIn("asr_python", config)
        self.assertNotIn("cuda_path", config)
        self.assertFalse(config["autostart"])

    def test_mac_autostart_has_argument_array_and_can_be_removed(self):
        import plistlib
        with tempfile.TemporaryDirectory() as directory, patch("sys.platform", "darwin"), patch.object(Path, "home", return_value=Path(directory)):
            runtime.set_autostart(True, Path(directory) / "user data")
            path = Path(directory) / "Library/LaunchAgents/com.whisperlocal.open.plist"
            data = plistlib.loads(path.read_bytes())
            self.assertIn(str(Path(directory) / "user data"), data["ProgramArguments"])
            runtime.set_autostart(False, Path(directory))
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
