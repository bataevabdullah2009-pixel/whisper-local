"""Real offline ASR cancellation/reload using a public speech fixture; never opens a microphone."""
import argparse
import base64
import json
from pathlib import Path
import os
import sys
import tempfile
import time
import wave
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from PySide6.QtWidgets import QApplication
from app import Controller
from runtime import worker_command

parser = argparse.ArgumentParser()
parser.add_argument("--model", type=Path)
parser.add_argument("--worker", type=Path)
parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
parser.add_argument("--cuda", default="")
parser.add_argument("--audio", type=Path, required=True)
args = parser.parse_args()
if args.worker:
    for key in ("PYTHONHOME", "PYTHONPATH"):
        os.environ.pop(key, None)
model = args.model or next(path for path in (ROOT / "build/test-models").glob("base-*")
                          if (path / "model.bin").is_file())
with wave.open(str(args.audio)) as wav:
    assert (wav.getframerate(), wav.getsampwidth(), wav.getnchannels()) == (16000, 2, 1)
    audio = (np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype("<f4") / 32768)

app = QApplication([])
app.setQuitOnLastWindowClosed(False)


def wait_until(predicate, timeout=180):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Real ASR operation timed out")
        app.processEvents()
        time.sleep(.01)


def command(kind, *arguments):
    base = [str(args.worker.resolve()), kind, *map(str, arguments)] if args.worker else worker_command(kind, *arguments)
    return [*base, "--cuda", args.cuda] if args.cuda else base


with tempfile.TemporaryDirectory() as temporary, patch("app.worker_command", side_effect=command):
    data = Path(temporary)
    config = json.loads((ROOT / "config.example.json").read_text())
    config.update(model_path=str(model.resolve()), device=args.device, sound_enabled=False)
    (data / "config.json").write_text(json.dumps(config), encoding="utf-8")
    controller = Controller(app, data, background=True, no_hook=True)
    # Observe the output without sending input to any application.
    pasted = []
    controller.paste_manager.paste = lambda text, target: pasted.append(bool(text.strip()))

    def transcribe(samples, request_id):
        controller.state, controller.request_id = "processing", request_id
        request = {"type": "transcribe", "id": request_id, "language": "en",
                   "audio": base64.b64encode(samples.astype("<f4").tobytes()).decode()}
        controller.worker.write((json.dumps(request) + "\n").encode())
        controller.operation_timer.start(180000)

    try:
        wait_until(lambda: controller.ready or controller.state == "error")
        assert controller.ready, "Real model did not load"
        old = controller.worker
        exited = []
        old.finished.connect(lambda *event: exited.append(time.monotonic()))
        transcribe(np.tile(audio, max(1, int(170 * 16000 / len(audio)))), "cancel-me")
        wait_until(lambda: old.bytesToWrite() == 0, 30)
        warm = time.monotonic() + .15
        wait_until(lambda: time.monotonic() >= warm, 5)
        assert controller.state == "processing", "Long speech must still be computing when cancelled"
        cancelled = time.monotonic()
        controller.cancel()
        wait_until(lambda: bool(exited), 5)
        latency = exited[0] - cancelled
        wait_until(lambda: controller.ready or controller.state == "error")
        assert controller.ready and controller.worker is not old
        assert not pasted, "Cancelled speech must never be pasted"
        transcribe(audio, "after-cancel")
        wait_until(lambda: bool(pasted) or controller.state == "error")
        assert pasted == [True], "Next speech did not produce a transcript"
        assert "country" in controller.last_text.lower(), "Public JFK fixture did not match expected speech"
        controller._pasted(True, "")
        transcribe(np.zeros(16000, dtype=np.float32), "silence")
        wait_until(lambda: controller.state == "idle" or controller.state == "error")
        assert controller.state == "idle" and pasted == [True], "Silence produced a transcript"
        print(json.dumps({"device": controller.device_description,
                          "cancel_exit_seconds": round(latency, 3),
                          "model_reload": True, "next_speech": True, "silence_empty": True}))
    finally:
        controller.shutdown()
        controller.settings.hide()
        controller.overlay.hide()
