"""Exercise source or bundled worker with a real model, without opening a microphone."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from queue import Queue, Empty

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import worker_command

parser = argparse.ArgumentParser()
parser.add_argument("--worker", type=Path)
parser.add_argument("--models", type=Path, default=ROOT / "build/test-models")
parser.add_argument("--device", default="cpu")
parser.add_argument("--audio", type=Path)
args = parser.parse_args()
environment = os.environ.copy()
if args.worker:
    for key in ("PYTHONHOME", "PYTHONPATH"):
        environment.pop(key, None)
base_command = [str(args.worker.resolve())] if args.worker else worker_command("probe")[:-1]
flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
download = subprocess.run([*base_command, "download", "base", str(args.models.resolve())],
    capture_output=True, text=True, encoding="utf-8", timeout=900, creationflags=flags, env=environment)
if download.returncode:
    raise RuntimeError(download.stdout[-3000:] + download.stderr[-3000:])
events = [json.loads(line) for line in download.stdout.splitlines()]
model = next(event["path"] for event in events if event.get("type") == "downloaded")
process = subprocess.Popen([*base_command, "asr", "--model", model, "--device", args.device],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8", creationflags=flags, env=environment)
queue = Queue()
threading.Thread(target=lambda: [queue.put(line) for line in process.stdout], daemon=True).start()
errors = []
threading.Thread(target=lambda: errors.extend(process.stderr), daemon=True).start()

def receive():
    try:
        line = queue.get(timeout=180)
    except Empty:
        raise RuntimeError("Worker did not respond. " + "".join(errors)[-2000:]) from None
    event = json.loads(line)
    if event.get("type") in ("error", "fatal"):
        raise RuntimeError(event)
    return event

try:
    ready = receive()
    assert ready["type"] == "ready", ready
    audio = b"\0" * (16000 * 4)
    if args.audio:
        import wave
        import numpy as np
        with wave.open(str(args.audio)) as wav:
            assert (wav.getframerate(), wav.getsampwidth(), wav.getnchannels()) == (16000, 2, 1)
            audio = (np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype("<f4") / 32768).tobytes()
    request = {"type": "transcribe", "id": "smoke", "language": "en", "audio": base64.b64encode(audio).decode()}
    process.stdin.write(json.dumps(request) + "\n")
    process.stdin.flush()
    result = receive()
    assert result["type"] == "result" and result["id"] == "smoke", result
    if args.audio:
        assert result["text"].strip(), "Speech fixture produced no text"
    else:
        assert result["text"] == "", "Silence produced a hallucinated transcript"
    print(json.dumps({"ready": ready, "result": result}, ensure_ascii=False))
    process.stdin.write('{"type":"quit"}\n')
    process.stdin.flush()
    assert process.wait(timeout=15) == 0
finally:
    if process.poll() is None:
        process.kill()
        process.wait(timeout=10)
