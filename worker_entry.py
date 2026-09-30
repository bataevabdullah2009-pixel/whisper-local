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
        from asr_worker import main as recognize
        return recognize()
    if kind == "probe":
        from runtime import hardware_info
        emit({"type": "hardware", **hardware_info()})
        return 0
    if kind == "download":
        from model_manager import download_model
        try:
            path = download_model(sys.argv[1], Path(sys.argv[2]), emit)
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
