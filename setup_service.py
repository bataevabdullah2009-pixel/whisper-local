"""Qt process boundary for hardware detection and cancellable downloads."""
import json
from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal
from runtime import ROOT, worker_command


class SetupService(QObject):
    event = Signal(dict)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.buffer = b""
        self.completed = False

    def start(self, kind, *arguments):
        self.stop()
        self.buffer = b""
        self.completed = False
        process = self.process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        environment.insert("PYTHONUTF8", "1")
        process.setProcessEnvironment(environment)
        process.setWorkingDirectory(str(ROOT))
        command = worker_command(kind, *arguments)
        process.setProgram(command[0])
        process.setArguments(command[1:])
        process.readyReadStandardOutput.connect(self._read)
        process.readyReadStandardError.connect(lambda: process.readAllStandardError())
        process.errorOccurred.connect(lambda error: self._error("Не удалось запустить подготовку. Переустановите приложение."))
        process.finished.connect(self._finished)
        process.start()

    def _error(self, message):
        if not self.completed:
            self.completed = True
            self.failed.emit(message)

    def _read(self):
        if not self.process:
            return
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                self._error("Не удалось прочитать ответ подготовки. Повторите попытку.")
                continue
            if event.get("type") == "error":
                self._error(event["error"])
            else:
                if event.get("type") in ("hardware", "downloaded"):
                    self.completed = True
                self.event.emit(event)

    def _finished(self, code, status):
        self._read()
        if not self.completed:
            self._error("Подготовка прервалась. Повторите попытку; скачанные данные сохранятся.")

    def stop(self):
        process, self.process = self.process, None
        if process:
            process.readyReadStandardOutput.disconnect()
            process.errorOccurred.disconnect()
            process.finished.disconnect()
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(2000)
            process.deleteLater()
