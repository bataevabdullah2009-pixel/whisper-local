"""Real offline worker lifecycle under the Qt controller. Uses public/developer WAV, no mic."""
import argparse
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import Mock, patch

import numpy as np
import psutil
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import Controller
from memory_usage import MemoryReader
from scripts.benchmark_int8 import read_audio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda", "metal", "auto"), default="cpu")
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--audio", type=Path, default=ROOT / "build/jfk.wav")
    args = parser.parse_args()
    model = args.model or next((ROOT / "build/test-models").glob("base-*"), None)
    if not model:
        parser.error("Provide a local --model or run smoke_asr.py to explicitly download the test model")
    pcm, _ = read_audio(args.audio)
    app = QApplication.instance() or QApplication([])

    def until(predicate, timeout=300):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise RuntimeError("Memory lifecycle operation timed out")
            app.processEvents()
            time.sleep(.005)

    with tempfile.TemporaryDirectory() as directory, patch("setup_service.SetupService.start"), patch("app.set_autostart"):
        data = Path(directory)
        (data / "config.json").write_text(json.dumps({"model_path": str(model.resolve()), "device": args.device, "language": "en"}))
        if args.worker:
            patcher = patch("app.worker_command", side_effect=lambda kind, *arguments: [str(args.worker.resolve()), kind, *arguments])
        else:
            patcher = patch("app.ROOT", ROOT)
        with patcher:
            controller = Controller(app, data, background=True, no_hook=True)
            reader = MemoryReader()
            try:
                until(lambda: controller.ready or controller.state == "error")
                assert controller.ready, "Model failed to load"
                pid = int(controller.worker.processId())
                owned_children = psutil.Process(pid).children(recursive=True)
                loaded = reader.sample([pid])
                started = time.monotonic()
                controller.settings.free_memory.setEnabled(True)
                controller.settings.free_memory.click()
                until(lambda: controller.state == "unloaded")
                release_seconds = time.monotonic() - started
                assert not psutil.pid_exists(pid), "Model process remains after release"
                until(lambda: not any(child.is_running() for child in owned_children), timeout=15)
                assert controller.worker is None and not controller.ready
                released = reader.sample([pid])
                controller.recorder.start = Mock()
                controller.recorder.healthy = Mock(return_value=True)
                controller.recorder.stop = Mock(return_value=np.frombuffer(pcm, dtype="<f4").copy())
                pasted = []
                def paste(text, target):
                    pasted.append(bool(text.strip()))
                    controller._pasted(False, "Fixture verified; no native paste requested")
                controller.paste_manager.paste = paste
                started = time.monotonic()
                controller.begin_recording(manual=True)
                assert controller.state == "recording", "Cold recording did not begin"
                controller.finish_recording()
                assert controller.state == "waiting_model", "Cold speech did not wait for model"
                until(lambda: bool(pasted) or controller.state == "error")
                assert pasted == [True] and controller.ready and controller.pending_audio is None
                cold_speech_seconds = time.monotonic() - started
                controller.config["idle_unload_seconds"] = 60
                controller.idle_since = time.monotonic() - 61
                controller.check_idle()
                until(lambda: controller.state == "unloaded")
                controller.system_events.suspend()
                controller.system_events.resume()
                assert controller.state == "unloaded" and controller.worker is None
                print(json.dumps({"requested_device": args.device, "loaded_worker": loaded,
                    "released_worker": released, "release_seconds": round(release_seconds, 3),
                    "cold_speech_seconds": round(cold_speech_seconds, 3),
                    "button_idle_cold_recording_sleep": "passed"}), flush=True)
            finally:
                reader.close()
                controller.shutdown()
                controller.settings.hide()
                controller.overlay.hide()


if __name__ == "__main__":
    main()
