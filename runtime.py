"""Paths and process setup shared by source installs and standalone bundles."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import plistlib
import sys
import subprocess

APP_ID = "WhisperLocalOpen"
ROOT = Path(__file__).resolve().parent


def data_directory() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / APP_ID
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support" / APP_ID
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP_ID


def worker_command(kind: str, *arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        name = "WhisperWorker.exe" if sys.platform == "win32" else "WhisperWorker"
        candidates = (Path(sys.executable).with_name(name), ROOT / name)
        helper = next((p for p in candidates if p.is_file()), candidates[0])
        return [str(helper), kind, *map(str, arguments)]
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    return [str(executable), "-u", "-B", str(ROOT / "worker_entry.py"), kind, *map(str, arguments)]


def launch_command(data_dir: Path) -> list[str]:
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        command = [str(executable)]
    else:
        if sys.platform == "win32":
            executable = executable.with_name("pythonw.exe")
        command = [str(executable), str(ROOT / "app.py")]
    return [*command, "--data-dir", str(data_dir), "--background"]


def set_autostart(enabled: bool, data_dir: Path) -> None:
    command = launch_command(data_dir)
    if sys.platform == "win32":
        import subprocess
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            if enabled:
                winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, subprocess.list2cmdline(command))
            else:
                try:
                    winreg.DeleteValue(key, APP_ID)
                except FileNotFoundError:
                    pass
    elif sys.platform == "darwin":
        path = Path.home() / "Library/LaunchAgents/com.whisperlocal.open.plist"
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(plistlib.dumps({"Label": "com.whisperlocal.open",
                "ProgramArguments": command, "RunAtLoad": True,
                "ProcessType": "Interactive", "WorkingDirectory": str(ROOT)}))
            temporary.replace(path)
        else:
            path.unlink(missing_ok=True)


def prepare_cuda(extra_path: str = "") -> list:
    """Keep DLL-directory handles alive for the entire worker lifetime."""
    if sys.platform != "win32":
        return []
    roots = [ROOT, Path(sys.executable).parent, Path(sys.prefix) / "Lib/site-packages"]
    directories = [Path(extra_path)] if extra_path else []
    for root in roots:
        for library in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            directories.extend((root / "nvidia" / library / "bin", root / "nvidia" / library / "lib"))
    directories = list(dict.fromkeys(p for p in directories if p.is_dir()))
    os.environ["PATH"] = os.pathsep.join(map(str, directories)) + os.pathsep + os.environ.get("PATH", "")
    return [os.add_dll_directory(str(path)) for path in directories]


def hardware_info() -> dict:
    handles = prepare_cuda()
    try:
        import ctranslate2
        gpu = ctranslate2.get_cuda_device_count() > 0 if sys.platform == "win32" else False
        if gpu:
            gpu = bool(ctranslate2.get_supported_compute_types("cuda"))
    except Exception:
        gpu = False
    finally:
        for handle in handles:
            handle.close()
    ram_gb, free_vram_mb = 0, 0
    try:
        if sys.platform == "win32":
            import ctypes
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in
                    ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                ram_gb = round(status.total / 1024**3)
            if gpu:
                result = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode == 0:
                    free_vram_mb = int(result.stdout.splitlines()[0].strip())
        elif sys.platform == "darwin":
            result = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                ram_gb = round(int(result.stdout) / 1024**3)
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        pass
    return {"platform": platform.system(), "architecture": platform.machine(),
            "cpu_threads": os.cpu_count() or 1, "cuda": gpu, "ram_gb": ram_gb,
            "free_vram_mb": free_vram_mb,
            "recommended_model": recommend_model(ram_gb, gpu, free_vram_mb)}


def recommend_model(ram_gb, cuda, free_vram_mb):
    if cuda and free_vram_mb >= 6 * 1024:
        return "turbo"
    return "base" if 0 < ram_gb <= 8 else "small"
