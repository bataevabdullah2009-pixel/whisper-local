"""Windowed entry point with diagnosable startup failures (including in packaged CI)."""
import argparse
from pathlib import Path
import sys
import traceback
from runtime import data_directory


def check_macos_callbacks():
    """Allocate dependency callbacks without a microphone, event tap or network."""
    if sys.platform != "darwin":
        return
    import sounddevice
    from CoreFoundation import (CFAbsoluteTimeGetCurrent, CFRunLoopTimerCreate,
                                CFRunLoopTimerInvalidate)

    # The same old-style CFFI allocator is used by PortAudio's recording callback.
    callback = sounddevice._ffi.callback("int(int)", lambda value: value + 1)
    if callback(1) != 2:
        raise RuntimeError("CFFI callback smoke failed")
    # PyObjC creates an executable callback stub; the timer is never scheduled.
    timer = CFRunLoopTimerCreate(None, CFAbsoluteTimeGetCurrent() + 3600,
                                0, 0, 0, lambda timer, info: None, None)
    if timer is None:
        raise RuntimeError("PyObjC callback smoke failed")
    CFRunLoopTimerInvalidate(timer)


def run():
    try:
        if "--smoke-test" in sys.argv:
            check_macos_callbacks()
        from app import main
        return main()
    except Exception:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--data-dir", type=Path, default=data_directory())
        options, _ = parser.parse_known_args()
        options.data_dir.mkdir(parents=True, exist_ok=True)
        report = options.data_dir / "startup.log"
        report.write_text(traceback.format_exc(), encoding="utf-8")
        if "--smoke-test" not in sys.argv:
            from PySide6.QtWidgets import QApplication, QMessageBox
            application = QApplication.instance() or QApplication([])
            QMessageBox.critical(None, "Whisper Local", f"Не удалось запустить приложение.\nПодробности: {report}")
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
