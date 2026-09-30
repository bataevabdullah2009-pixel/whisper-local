# Build on the target OS. UI and console worker share a single dependency folder.
import os
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

root = Path(SPECPATH)
data = [(str(root / name), ".") for name in ("config.example.json", "model_catalog.json")]
data += [(str(root / "assets/sounds"), "assets/sounds")]
data += collect_data_files("faster_whisper") + collect_data_files("certifi")
binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")
for package in ("faster-whisper", "ctranslate2", "huggingface-hub", "tokenizers", "onnxruntime", "av"):
    data += copy_metadata(package)
if sys.platform == "win32":
    site = Path(sys.prefix) / "Lib/site-packages"
    for path in (site / "nvidia").rglob("*.dll"):
        binaries.append((str(path), str(path.parent.relative_to(site))))
    for package in ("nvidia-cublas-cu12", "nvidia-cudnn-cu12", "nvidia-cuda-nvrtc-cu12"):
        try:
            data += copy_metadata(package)
        except Exception:
            pass  # CPU-only source builds are supported.

hidden = collect_submodules("av")
common = dict(pathex=[str(root)], binaries=binaries, datas=data, hiddenimports=hidden,
              excludes=["tkinter", "matplotlib", "IPython", "pytest"])
gui = Analysis([str(root / "app.py")], **common)
worker = Analysis([str(root / "worker_entry.py")], **common)
icon = str(root / "build/app.ico") if sys.platform == "win32" else None
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="WhisperLocal",
              console=False, icon=icon, upx=False)
worker_exe = EXE(PYZ(worker.pure), worker.scripts, [], exclude_binaries=True, name="WhisperWorker",
                 console=True, upx=False)
collection = COLLECT(gui_exe, worker_exe, gui.binaries, worker.binaries, gui.datas, worker.datas,
                     name="WhisperLocal", upx=False)
if sys.platform == "darwin":
    app = BUNDLE(collection, name="Whisper Local.app", icon=str(root / "build/app.icns"),
                 bundle_identifier="com.whisperlocal.open", version="0.1.0",
                 info_plist={
                     "CFBundleDisplayName": "Whisper Local",
                     "LSMinimumSystemVersion": "13.0",
                     "LSBackgroundOnly": False,
                     "NSHighResolutionCapable": True,
                     "NSMicrophoneUsageDescription": "Whisper Local записывает голос только во время диктовки и распознаёт его на вашем Mac.",
                 })
