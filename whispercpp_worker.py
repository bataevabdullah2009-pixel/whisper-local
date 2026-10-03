"""Offline Metal/CPU recognizer using the same bounded PCM pipe protocol as CTranslate2."""
import argparse
import base64
import json
import os
import platform
import sys
import time

import numpy as np
from model_manager import validate_model
from whispercpp_backend import WhisperCpp


def load_cpp_model(path, preference, threads, factory=WhisperCpp, progress=None):
    fallback = False
    gpu_requested = preference == "metal" or (preference == "auto" and platform.machine() == "arm64")
    candidates = (True, False) if gpu_requested and sys.platform == "darwin" else (False,)
    for gpu in candidates:
        model = None
        try:
            if progress:
                progress("create_context", gpu)
            model = factory(path, gpu, threads)
            # Probe real encoder/decoder kernels before declaring Metal readiness.
            if progress:
                progress("warmup_cpp", gpu)
            model.transcribe(np.zeros(16000, dtype=np.float32), "ru")
            device = "metal" if model.metal else "cpu"
            return model, device, model.compute_type, fallback or (gpu and device != "metal")
        except Exception:
            if model:
                model.close()
            if not gpu:
                raise
            fallback = True


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "metal"), default="auto")
    parser.add_argument("--compute-type", default="auto") # file quantization defines whisper.cpp precision
    parser.add_argument("--startup-progress", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.environ.update(HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
    def offline(event, _arguments):
        if event in ("socket.connect", "socket.bind", "socket.getaddrinfo"):
            raise RuntimeError("Whisper Local runs offline")
    sys.addaudithook(offline)
    started = time.monotonic()
    model = None
    def progress(stage, gpu=None):
        if args.startup_progress:
            event = {"type": "startup_progress", "stage": stage}
            if gpu is not None:
                event["gpu"] = gpu
            emit(event)
    try:
        progress("import_vad")
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        progress("validate_model")
        validate_model(args.model)
        model, device, compute, fallback = load_cpp_model(args.model, args.device,
            max(1, min(8, (os.cpu_count() or 2) // 2)), progress=progress)
        progress("warmup_vad")
        get_speech_timestamps(np.zeros(16000, dtype=np.float32), VadOptions())
        emit({"type": "ready", "backend": "whispercpp", "device": device, "compute_type": compute,
            "fallback": fallback, "load_seconds": round(time.monotonic() - started, 2)})
    except Exception:
        if model:
            model.close()
        emit({"type": "fatal", "error": "Не удалось загрузить локальную GGML-модель или движок whisper.cpp."})
        return 1
    try:
        while True:
            line = sys.stdin.buffer.readline(24 * 1024 * 1024)
            if not line:
                break
            request = {}
            audio = speech = data = None
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    request = {}
                    raise ValueError()
                if request.get("type") == "quit":
                    break
                if request.get("type") != "transcribe":
                    raise ValueError()
                started = time.monotonic()
                data = base64.b64decode(request["audio"], validate=True)
                if len(data) > 16000 * 4 * 181 or len(data) % 4:
                    raise ValueError()
                audio = np.frombuffer(data, dtype="<f4").copy()
                if not np.isfinite(audio).all():
                    raise ValueError()
                text = ""
                if audio.size >= 3200 and float(np.max(np.abs(audio))) > .002:
                    audio *= min(5.0, max(1.0, .45 / float(np.max(np.abs(audio)))))
                    np.clip(audio, -1, 1, out=audio)
                    chunks = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=450,
                        speech_pad_ms=300, min_speech_duration_ms=120))
                    if chunks:
                        speech = np.concatenate([audio[chunk["start"]:chunk["end"]] for chunk in chunks])
                        text = model.transcribe(speech, request.get("language"))
                emit({"type": "result", "id": request["id"], "text": text,
                    "seconds": round(time.monotonic() - started, 3), "device": device})
            except Exception:
                emit({"type": "error", "id": request.get("id"), "error": "Некорректная запись или ошибка локального распознавания."})
            finally:
                line, request, audio, speech, data, text = b"", {}, None, None, None, ""
    finally:
        model.close()
    return 0
