"""Check bundled/source Mac backend with public speech; report metrics, never decoded text."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import worker_command
from scripts.benchmark_int8 import Worker, read_audio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--audio", type=Path, default=ROOT / "build/jfk.wav")
    parser.add_argument("--output", type=Path, default=ROOT / "build/whispercpp-smoke.json")
    parser.add_argument("--require-metal", action="store_true", help="Use on a physical Mac with a supported GPU")
    args = parser.parse_args()
    base = [str(args.worker.resolve())] if args.worker else worker_command("probe")[:-1]
    model = args.model
    if model is None:
        # Explicit test-model setup, entirely separate from the offline ASR process.
        result = subprocess.run([*base, "download", "base", str(ROOT / "build/test-models"), "whispercpp"],
            text=True, encoding="utf-8", capture_output=True, timeout=900)
        if result.returncode:
            raise RuntimeError("Verified GGML test-model download failed")
        model = Path(next(json.loads(line)["path"] for line in result.stdout.splitlines()
            if json.loads(line).get("type") == "downloaded"))
    pcm, duration = read_audio(args.audio)
    reports = []
    for device in ("cpu", "auto"):
        worker = Worker(model, device, "auto", args.worker)
        try:
            ready = worker.receive()
            assert ready["type"] == "ready" and ready.get("backend") == "whispercpp"
            if device == "cpu":
                assert ready["device"] == "cpu" and not ready["fallback"]
            elif args.require_metal:
                assert ready["device"] == "metal" and not ready["fallback"], "Metal context/kernels did not become ready"
            timings = []
            for repeat in range(3):
                started = time.monotonic()
                result = worker.transcribe(pcm, "en", f"speech-{repeat}")
                timings.append(time.monotonic() - started)
                assert "country" in result["text"].lower(), "Public speech fixture was not recognized"
                del result
            quiet = (np.frombuffer(pcm, dtype="<f4") * .05).astype("<f4").tobytes()
            for name, samples in (("silence", b"\0" * 64000), ("quiet-speech", quiet)):
                result = worker.transcribe(samples, "en", name)
                assert bool(result["text"].strip()) == (name != "silence")
                del result
            reports.append({"ready": ready, "speech_seconds": duration,
                "median_seconds": round(statistics.median(timings), 3), "speech_silence": "passed"})
        finally:
            worker.close()
    # Exercise the same controller cancellation/reload and cold-recording paths as other backends.
    for script in ("smoke_reliability.py", "smoke_memory.py"):
        command = [sys.executable, str(ROOT / "scripts" / script), "--model", str(model), "--device", "auto", "--audio", str(args.audio)]
        if args.worker:
            command += ["--worker", str(args.worker.resolve())]
        subprocess.run(command, check=True, timeout=900)
    report = {"modes": reports, "controller_cancel_reload_idle_cold_recording": "passed",
        "metal_status": "verified" if reports[1]["ready"]["device"] == "metal" else "unavailable_on_runner"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
