"""Offline CUDA recognizer. PCM travels through pipes, never via files or sockets."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import sys
import time


def emit(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--cuda", required=True)
    args = parser.parse_args()
    os.environ.update(HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
    os.environ["PATH"] = args.cuda + os.pathsep + os.environ.get("PATH", "")
    dll_handle = os.add_dll_directory(args.cuda)
    # Enforce offline operation even if a dependency changes its defaults.
    def offline(event, _args):
        if event in ("socket.connect", "socket.bind", "socket.getaddrinfo"):
            raise RuntimeError("Whisper Local runs offline")
    sys.addaudithook(offline)
    import numpy as np
    from faster_whisper import WhisperModel
    from faster_whisper.vad import get_speech_timestamps, VadOptions

    started = time.monotonic()
    try:
        if not (Path(args.model) / "model.bin").is_file():
            raise FileNotFoundError("Не найдена локальная модель Whisper")
        model = WhisperModel(args.model, device="cuda", compute_type="float16",
                             local_files_only=True, num_workers=1, cpu_threads=4)
        # The first CUDA kernels are compiled before the first user dictation.
        segments, _ = model.transcribe(np.zeros(8000, dtype=np.float32), language="ru",
                                      beam_size=1, vad_filter=False, condition_on_previous_text=False)
        list(segments)
        get_speech_timestamps(np.zeros(16000, dtype=np.float32), VadOptions())
        emit({"type": "ready", "device": "cuda", "compute_type": "float16",
              "load_seconds": round(time.monotonic() - started, 2)})
    except Exception as error:
        emit({"type": "fatal", "error": str(error)})
        return 1

    while True:
        line = sys.stdin.buffer.readline(24 * 1024 * 1024)
        if not line:
            break
        request = {}
        try:
            request = json.loads(line)
            if request.get("type") == "quit":
                break
            if request.get("type") != "transcribe":
                raise ValueError("Unknown request")
            started = time.monotonic()
            data = base64.b64decode(request["audio"], validate=True)
            if len(data) > 16000 * 4 * 181 or len(data) % 4:
                raise ValueError("Invalid audio size")
            audio = np.frombuffer(data, dtype="<f4").copy()
            if not np.isfinite(audio).all():
                raise ValueError("Invalid samples")
            text = ""
            if audio.size >= 3200 and float(np.max(np.abs(audio))) > 0.002:
                # A low, bounded gain helps quiet USB microphones without amplifying silence.
                peak = float(np.max(np.abs(audio)))
                audio *= min(5.0, max(1.0, 0.45 / peak))
                np.clip(audio, -1.0, 1.0, out=audio)
                segments, info = model.transcribe(
                    audio, language=request.get("language") or None, task="transcribe",
                    beam_size=5, temperature=0.0, condition_on_previous_text=False,
                    vad_filter=True, vad_parameters={"min_silence_duration_ms": 450,
                        "speech_pad_ms": 300, "min_speech_duration_ms": 120},
                    word_timestamps=False,
                )
                text = " ".join(s.text.strip() for s in segments).strip()
            emit({"type": "result", "id": request["id"], "text": text,
                  "seconds": round(time.monotonic() - started, 3), "device": "cuda"})
        except Exception as error:
            emit({"type": "error", "id": request.get("id"), "error": str(error)})
        # Do not keep dictation PCM in idle worker memory.
        line = b""
        request = {}
        if "audio" in locals():
            del audio
        if "data" in locals():
            del data
    dll_handle.close()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
