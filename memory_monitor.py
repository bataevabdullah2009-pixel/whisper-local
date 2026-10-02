"""Sample off the Qt UI thread so driver/performance queries cannot delay recording."""
import threading

from PySide6.QtCore import QThread, Signal

from memory_usage import MemoryReader


class MemoryMonitor(QThread):
    measured = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._pids = ()

    def set_pids(self, pids):
        with self._lock:
            self._pids = tuple(pids)

    def run(self):
        reader = MemoryReader()
        try:
            while not self._stop.is_set():
                with self._lock:
                    pids = self._pids
                self.measured.emit(pids, reader.sample(pids))
                self._stop.wait(2)
        finally:
            reader.close()

    def stop(self):
        self._stop.set()
        self.wait()
