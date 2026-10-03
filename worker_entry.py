"""Console helper, also bundled separately so the GUI never depends on Python being installed."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main():
    os.environ.update(HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
    kind = sys.argv.pop(1)
    if kind == "asr":
        if "--startup-progress" in sys.argv:
            emit({"type": "startup_progress", "stage": "dispatch"})
        from model_manager import model_backend
        index = sys.argv.index("--model")
        if model_backend(sys.argv[index + 1]) == "whispercpp":
            from whispercpp_worker import main as recognize
            return recognize()
        from asr_worker import main as recognize
        return recognize()
    if kind == "probe":
        from runtime import hardware_info
        emit({"type": "hardware", **hardware_info()})
        return 0
    if kind == "import-editor":
        from phrase_editor import validate_editor_model
        from model_manager import EDITOR_CATALOG, matches
        try:
            path = validate_editor_model(sys.argv[1])
            if not matches(path, EDITOR_CATALOG[0]["files"][0]):
                raise ValueError()
            emit({"type": "downloaded", "path": str(path), "model_id": "qwen3-1.7b"})
            return 0
        except Exception:
            emit({"type": "error", "error": "Нужна полностью скачанная модель Qwen3 1.7B Q8_0. Файл не прошёл проверку."})
            return 1
    if kind == "download":
        from model_manager import download_model
        try:
            backend = sys.argv[3] if len(sys.argv) > 3 else "faster-whisper"
            path = download_model(sys.argv[1], Path(sys.argv[2]), emit, backend)
            emit({"type": "downloaded", "path": str(path), "model_id": sys.argv[1]})
            return 0
        except Exception as error:
            emit({"type": "error", "error": str(error)})
            return 1
    raise ValueError("Unknown worker operation")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
