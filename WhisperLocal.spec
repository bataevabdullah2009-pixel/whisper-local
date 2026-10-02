# Build on the target OS. UI and console worker share a single dependency folder.
import os
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

root = Path(SPECPATH)
mac_signing = {}
gui_mac_signing = {}
if sys.platform == "darwin":
    # CI preview builds remain ad-hoc. Release credentials live only in the
    # manual signing workflow; PyInstaller signs every collected native library.
    identity = os.environ.get("WHISPERLOCAL_MACOS_SIGNING_IDENTITY")
    if identity:
        mac_signing = dict(codesign_identity=identity)
        gui_mac_signing = dict(**mac_signing,
                               entitlements_file=str(root / "packaging/macos-entitlements.plist"))
data = [(str(root / name), ".") for name in ("config.example.json", "model_catalog.json", "whispercpp_catalog.json")]
data += [(str(root / "assets/sounds"), "assets/sounds")]
data += collect_data_files("faster_whisper") + collect_data_files("certifi")
binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")
if sys.platform == "darwin":
    bridge = root / "build/native/libwhisperlocal.dylib"
    if not bridge.is_file():
        raise RuntimeError("Run scripts/build_whispercpp.py before packaging a Mac build")
    binaries += [(str(bridge), "native")]
    data += [(str(root / "build/native/whispercpp-LICENSE.txt"), "native"),
             (str(root / "build/native/whispercpp-version.json"), "native")]
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
gui = Analysis([str(root / "gui_entry.py")], **common)
worker = Analysis([str(root / "worker_entry.py")], **common)
if sys.platform == "win32":
    # Qt links the Windows ICU API. A private ICU found on the build host's PATH
    # (e.g. Poppler) exports versioned symbols and breaks QtCore at startup.
    system_icu = {"icu.dll", "icuuc.dll", "icuin.dll", "icudt.dll"}
    for analysis in (gui, worker):
        analysis.binaries = [entry for entry in analysis.binaries
                             if Path(entry[0]).name.lower() not in system_icu]
icon = str(root / "build/app.ico") if sys.platform == "win32" else None
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="WhisperLocal",
              console=False, icon=icon, upx=False, **gui_mac_signing)
worker_exe = EXE(PYZ(worker.pure), worker.scripts, [], exclude_binaries=True, name="WhisperWorker",
                 console=True, upx=False, **mac_signing)
collection = COLLECT(gui_exe, worker_exe, gui.binaries, worker.binaries, gui.datas, worker.datas,
                     name="WhisperLocal", upx=False)
if sys.platform == "darwin":
    app = BUNDLE(collection, name="Whisper Local.app", icon=str(root / "build/app.icns"),
                 bundle_identifier="com.whisperlocal.open", version="0.1.0",
                 info_plist={
                     "CFBundleDisplayName": "Whisper Local",
                     "LSMinimumSystemVersion": "14.0",
                     "LSBackgroundOnly": False,
                     "NSHighResolutionCapable": True,
                     "NSMicrophoneUsageDescription": "Whisper Local записывает голос только во время диктовки и распознаёт его на вашем Mac.",
                 })
