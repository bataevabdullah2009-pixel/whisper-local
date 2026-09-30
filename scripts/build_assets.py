"""Render the existing application icon; no new external artwork."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from ui import app_icon

app = QApplication([])
build = ROOT / "build"
build.mkdir(exist_ok=True)
app_icon(256).pixmap(256, 256).save(str(build / "app.ico"), "ICO")
if sys.platform == "darwin":
    iconset = build / "app.iconset"
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = "@2x" if scale == 2 else ""
            app_icon(size * scale).pixmap(size * scale, size * scale).save(str(iconset / f"icon_{size}x{size}{suffix}.png"))
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(build / "app.icns")], check=True)
