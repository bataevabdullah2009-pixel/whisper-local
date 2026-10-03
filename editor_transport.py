"""Ephemeral prompt pipe: a Windows named pipe or a private Unix FIFO, never a text file."""
import errno
import os
from pathlib import Path
import sys
import tempfile
import threading
import uuid

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer


class PromptPipe(QObject):
    flushed = Signal()

    def __init__(self, prompt, parent=None):
        super().__init__(parent)
        self.payload = prompt.encode("utf-8")
        self.offset = 0
        self.server = self.socket = None
        self.fd = None
        self.directory = None
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self.write_fifo)
        self.flushed.connect(self.close_windows)
        if sys.platform == "win32":
            self.server = QLocalServer(self)
            self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
            if not self.server.listen("WhisperLocal-Editor-" + uuid.uuid4().hex):
                raise OSError("Cannot prepare private editor pipe")
            self.path = self.server.fullServerName()
            self.server.newConnection.connect(self.write_windows)
        else:
            self.directory = Path(tempfile.mkdtemp(prefix="whisperlocal-editor-"))
            self.path = str(self.directory / "prompt")
            os.mkfifo(self.path, 0o600)
            self.timer.start()

    def write_windows(self):
        self.socket = self.server.nextPendingConnection()
        self.server.close()
        self.socket.write(self.payload)
        self.socket.flush()
        self.payload = b""
        # DisconnectNamedPipe discards unread bytes. Wait for the client to consume them.
        handle = int(self.socket.socketDescriptor())
        def drain():
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.FlushFileBuffers.argtypes = [ctypes.c_void_p]
            kernel.FlushFileBuffers.restype = ctypes.c_int
            kernel.FlushFileBuffers(handle)
            try:
                self.flushed.emit()
            except RuntimeError:
                pass  # The request was cancelled and its Qt object already destroyed.
        threading.Thread(target=drain, name="WhisperEditorPipeFlush", daemon=True).start()

    def close_windows(self):
        if self.socket:
            self.socket.disconnectFromServer()

    def write_fifo(self):
        try:
            if self.fd is None:
                self.fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
            self.offset += os.write(self.fd, self.payload[self.offset:])
            if self.offset == len(self.payload):
                os.close(self.fd)
                self.fd = None
                self.timer.stop()
                self.payload = b""
        except OSError as error:
            if error.errno not in (errno.ENXIO, errno.EAGAIN, errno.EWOULDBLOCK):
                self.stop()

    def stop(self):
        self.timer.stop()
        self.payload = b""
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.socket:
            self.socket.abort()
            self.socket.deleteLater()
            self.socket = None
        if self.server:
            self.server.close()
            self.server.deleteLater()
            self.server = None
        if self.directory:
            Path(self.path).unlink(missing_ok=True)
            self.directory.rmdir()
            self.directory = None
