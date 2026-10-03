import hashlib
import sys
import unittest

from PySide6.QtCore import QCoreApplication, QEventLoop, QProcess, QTimer
from editor_transport import PromptPipe


class PrivatePromptTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QCoreApplication.instance() or QCoreApplication([])

    def test_pipe_transfers_utf8_without_a_regular_file_and_is_removed(self):
        source = "synthetic Russian: проверка фразы\r\nsecond line"
        pipe = PromptPipe(source)
        process = QProcess()
        loop = QEventLoop()
        process.finished.connect(loop.quit)
        reader = """import hashlib,os,sys
fd = os.open(sys.argv[1], os.O_RDONLY | getattr(os, 'O_BINARY', 0))
blocks = []
while True:
    try:
        block = os.read(fd, 65536)
    except OSError as error:
        if sys.platform == 'win32' and blocks and error.errno == 22:
            break
        raise
    if not block:
        break
    blocks.append(block)
os.close(fd)
print(hashlib.sha256(b''.join(blocks)).hexdigest())
"""
        process.start(sys.executable, ["-c", reader, pipe.path])
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        try:
            self.assertEqual(process.state(), QProcess.ProcessState.NotRunning)
            self.assertEqual(process.exitCode(), 0, bytes(process.readAllStandardError()).decode())
            self.assertEqual(bytes(process.readAllStandardOutput()).decode().strip(), hashlib.sha256(source.encode()).hexdigest())
            self.assertEqual(pipe.payload, b"")
        finally:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(1000)
            pipe.stop()
        self.assertIsNone(pipe.directory)
        self.assertIsNone(pipe.server)

    def test_cancel_before_reader_connects_destroys_untransferred_text(self):
        pipe = PromptPipe("private synthetic prompt")
        pipe.stop()
        self.assertEqual(pipe.payload, b"")
        self.assertFalse(pipe.timer.isActive())


if __name__ == "__main__":
    unittest.main()
