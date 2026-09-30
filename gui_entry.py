"""Windowed entry point with diagnosable startup failures (including in packaged CI)."""
import argparse
from pathlib import Path
import sys
import traceback
from runtime import data_directory


def run():
    try:
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
