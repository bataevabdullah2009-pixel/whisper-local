"""Exercise actual Windows field identity, paste and clipboard in an owned Qt window."""
import json
from pathlib import Path
from queue import Queue
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QMimeData, QByteArray
from PySide6.QtWidgets import QApplication
from app import PasteManager
import windows_native as native

app = QApplication([])
clipboard = app.clipboard()
previous = {name: QByteArray(clipboard.mimeData().data(name)) for name in clipboard.mimeData().formats()}
queue = Queue()
process = subprocess.Popen([sys.executable, "-u", "-B", str(ROOT / "tests/fixtures/focus_window.py")],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
threading.Thread(target=lambda: [queue.put(json.loads(line)) for line in process.stdout], daemon=True).start()


def command(text):
    process.stdin.write(text + "\n")
    process.stdin.flush()


def wait_until(predicate, seconds=5):
    end = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError("Native paste did not finish")
        app.processEvents()
        time.sleep(.01)


manager = PasteManager()
results = []
manager.finished.connect(lambda *args: results.append(args))


def receive(seconds=5):
    # The parent owns the clipboard: pump Qt while the child requests its MIME data.
    wait_until(lambda: not queue.empty(), seconds)
    return queue.get_nowait()


def fixture_target():
    identified = []
    def ready():
        target = native.focus_target()
        if target[2] is not None and target[2][0] == process.pid:
            identified[:] = [target]
            return True
        return False
    # Accessibility providers can initialize after the window's first paint.
    # Only the owned fixture process may participate in this native paste test.
    wait_until(ready)
    return identified[0]


try:
    assert receive(10)["ready"]
    first = fixture_target()
    assert first[2] is not None, "Qt field not identified by UI Automation"
    assert native.same_target(first)
    command("second")
    assert receive()["focused"] == "second"
    second = fixture_target()
    assert first[:2] == second[:2], "Fixture must exercise fields sharing HWNDs"
    assert first[2] != second[2], "Focused fields must have different UIA runtime IDs"
    assert not native.same_target(first)
    clipboard.setText("fixture original clipboard")
    manager.paste("fixture transcript", first)
    assert results and not results[-1][0], "Changed field must refuse paste"
    assert clipboard.text() == "fixture original clipboard"
    command("first")
    assert receive()["focused"] == "first"
    manager.paste("fixture transcript", fixture_target())
    wait_until(lambda: len(results) == 2)
    assert results[-1][0], results[-1]
    command("verify")
    verified = receive()
    assert verified["first_matches"] and verified["second_empty"], verified
    wait_until(lambda: clipboard.text() == "fixture original clipboard")
    print("Windows native focus: shared-HWND field change refused; exact paste and clipboard restore passed")
finally:
    manager.cancel()
    if clipboard.text() == "fixture original clipboard":
        old = QMimeData()
        for name, value in previous.items():
            old.setData(name, value)
        clipboard.setMimeData(old)
    if process.poll() is None:
        command("quit")
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
