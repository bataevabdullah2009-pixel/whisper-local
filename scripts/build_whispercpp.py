"""Build the pinned local bridge on the target Mac. No package installs or model downloads."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PIN = json.loads((ROOT / "native/whispercpp-version.json").read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "build/whispercpp-upstream")
    parser.add_argument("--cpu-only", action="store_true", help="Developer CPU bridge test; Mac product builds enable Metal")
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "--branch", "v" + PIN["version"], PIN["repository"], str(source)], check=True)
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if commit != PIN["commit"] or subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True).strip():
        raise RuntimeError("whisper.cpp source must be clean and match the pinned commit")
    build = ROOT / "build/whispercpp-native"
    command = ["cmake", "-S", str(ROOT / "native"), "-B", str(build),
        "-DCMAKE_BUILD_TYPE=Release", f"-DWHISPER_SOURCE_ROOT={source}",
        "-DGGML_METAL=" + ("OFF" if args.cpu_only else "ON")]
    if sys.platform == "darwin":
        command += ["-DCMAKE_OSX_DEPLOYMENT_TARGET=14.0", "-DGGML_METAL_MACOSX_VERSION_MIN=14.0"]
    elif not args.cpu_only:
        parser.error("Build the Metal product bridge on macOS")
    subprocess.run(command, check=True)
    subprocess.run(["cmake", "--build", str(build), "--config", "Release", "--target", "whisperlocal",
        "--parallel", str(min(8, os.cpu_count() or 2))], check=True)
    name = "libwhisperlocal.dylib" if sys.platform == "darwin" else "whisperlocal.dll" if sys.platform == "win32" else "libwhisperlocal.so"
    candidates = list((build / "output").rglob(name))
    if len(candidates) != 1:
        raise RuntimeError("Bridge build did not produce exactly one library")
    destination = ROOT / "build/native"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidates[0], destination / name)
    shutil.copy2(source / "LICENSE", destination / "whispercpp-LICENSE.txt")
    shutil.copy2(ROOT / "native/whispercpp-version.json", destination / "whispercpp-version.json")
    print("Built pinned offline bridge:", name)


if __name__ == "__main__":
    main()
