"""Prepare verified llama.cpp build assets. Only packaging/development calls this script."""
import hashlib
import json
from pathlib import Path
import platform
import posixpath
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    pin = json.loads((ROOT / "editor_runtime.json").read_text(encoding="utf-8"))
    key = "Windows-x64" if sys.platform == "win32" else "macOS-arm64" if platform.machine() == "arm64" else "macOS-x64"
    if sys.platform not in ("win32", "darwin"):
        raise RuntimeError("The editor runtime supports Windows x64 and Apple Silicon/Intel Macs")
    asset = pin["platforms"][key]
    destination = ROOT / "build/native/editor"
    destination.mkdir(parents=True, exist_ok=True)
    archive = ROOT / "build" / asset["archive"]
    if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != asset["sha256"]:
        url = f"{pin['repository']}/releases/download/{pin['version']}/{asset['archive']}"
        with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
    if archive.stat().st_size != asset["size"] or hashlib.sha256(archive.read_bytes()).hexdigest() != asset["sha256"]:
        raise RuntimeError("Editor runtime archive checksum mismatch")
    # Ship only the CLI and its local libraries; server, network tools and benchmarks are excluded.
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            name = Path(info.filename).name
            if "rpc" in name.lower() and sys.platform != "darwin":
                continue
            if name not in ("llama-cli", "llama-cli.exe") and not name.endswith((".dll", ".dylib", ".metallib")):
                continue
            if not name or info.is_dir():
                continue
            path = destination / name
            # Zip symlinks must resolve to actual library bytes, never become text stubs.
            target = info
            seen = set()
            while stat.S_ISLNK(target.external_attr >> 16):
                if target.filename in seen:
                    raise RuntimeError("Cyclic runtime archive symlink")
                seen.add(target.filename)
                link = source.read(target).decode("utf-8")
                target = source.getinfo(posixpath.normpath(posixpath.join(posixpath.dirname(target.filename), link)))
            with source.open(target) as stream, path.open("wb") as output:
                shutil.copyfileobj(stream, output)
            path.chmod(0o755)
    if sys.platform == "darwin":
        # Release archives have build-relative rpaths; the bundled runtime has a flat layout.
        for path in destination.iterdir():
            if path.name == "llama-cli" or path.suffix == ".dylib":
                commands = subprocess.check_output(["otool", "-l", str(path)], text=True)
                if "path @loader_path (" not in commands:
                    subprocess.run(["install_name_tool", "-add_rpath", "@loader_path", str(path)], check=True)
                subprocess.run(["codesign", "--force", "--sign", "-", str(path)], check=True, capture_output=True)
    license_url = f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{pin['version']}/LICENSE"
    with urllib.request.urlopen(license_url, timeout=30) as response:
        (destination / "llama.cpp-LICENSE.txt").write_bytes(response.read())
    (destination / "runtime.json").write_text(json.dumps({"version": pin["version"], "platform": key}), encoding="utf-8")
    cli = destination / ("llama-cli.exe" if sys.platform == "win32" else "llama-cli")
    probe = subprocess.run([str(cli), "--version"], capture_output=True, timeout=30)
    if probe.returncode:
        raise RuntimeError("Editor runtime cannot start: " + probe.stderr.decode("utf-8", errors="replace"))
    print("Prepared verified offline editor runtime:", key, pin["version"])


if __name__ == "__main__":
    main()
