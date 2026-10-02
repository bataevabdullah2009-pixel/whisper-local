"""Owned Qt test window; the parent only observes these two fixture fields."""
import json
from queue import Queue, Empty
import sys
import threading
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLineEdit

app = QApplication([])
window = QWidget()
window.setWindowTitle("Whisper Local · field safety test")
layout = QVBoxLayout(window)
first, second = QLineEdit(), QLineEdit()
layout.addWidget(first)
layout.addWidget(second)
window.show()
window.activateWindow()
first.setFocus()
queue = Queue()
threading.Thread(target=lambda: [queue.put(line.strip()) for line in sys.stdin], daemon=True).start()


def emit(event):
    print(json.dumps(event), flush=True)


def poll():
    try:
        command = queue.get_nowait()
    except Empty:
        return
    if command in ("first", "second"):
        (first if command == "first" else second).setFocus()
        QTimer.singleShot(100, lambda: emit({"focused": command}))
    elif command == "verify":
        emit({"first_matches": first.text() == "fixture transcript", "second_empty": not second.text()})
    elif command == "quit":
        app.quit()


timer = QTimer()
timer.timeout.connect(poll)
timer.start(20)
QTimer.singleShot(500, lambda: emit({"ready": True}))
raise SystemExit(app.exec())
