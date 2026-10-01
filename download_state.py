"""Local download intent. Reading it never starts a network operation."""
import json
import os
from pathlib import Path

from model_manager import catalog_for


def load_pending(data_dir: Path):
    try:
        value = json.loads((data_dir / "download-state.json").read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return None
        model = next(model for model in catalog_for(value["backend"]) if model["id"] == value["model_id"])
        devices = ("auto", "cpu", "metal") if value["backend"] == "whispercpp" else ("auto", "cpu", "cuda")
        if value["revision"] != model["revision"] or value["device"] not in devices:
            return None
        return {key: value[key] for key in ("model_id", "backend", "device", "revision")}
    except (OSError, ValueError, KeyError, StopIteration, TypeError):
        return None


def save_pending(data_dir: Path, model_id: str, backend: str, device: str):
    model = next(model for model in catalog_for(backend) if model["id"] == model_id)
    value = {"model_id": model_id, "backend": backend, "device": device, "revision": model["revision"]}
    data_dir.mkdir(parents=True, exist_ok=True)
    temporary = data_dir / "download-state.json.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(data_dir / "download-state.json")
    return value


def clear_pending(data_dir: Path):
    (data_dir / "download-state.json").unlink(missing_ok=True)
