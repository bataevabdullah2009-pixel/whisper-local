"""Whisper Local: hold Left Alt, speak, release to paste into the original field."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import time
import uuid
import winreg

from PySide6.QtCore import (QObject, Signal, QTimer, QProcess, QProcessEnvironment,
                           QMimeData, QByteArray)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QAction

from audio_capture import Recorder
from sound_cues import SoundCues
from ui import Overlay, SettingsWindow, app_icon, prepare_fonts
import windows_native as native

ROOT = Path(__file__).resolve().parent
DEFAULT_INSTALL = Path.home() / "Documents" / "WhisperLocal"
LOG = logging.getLogger("WhisperLocal")


def load_config(data_dir):
    default = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    path = data_dir / "config.json"
    if path.is_file():
        try:
            default.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            LOG.warning("Invalid config; using defaults")
    default["hold_ms"] = max(120, min(700, int(default["hold_ms"])))
    default["max_recording_seconds"] = max(10, min(180, int(default["max_recording_seconds"])))
    default["sound_volume"] = max(0, min(100, int(default.get("sound_volume", 65))))
    return default


def set_autostart(enabled, data_dir):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            command = f'"{pythonw}" "{ROOT / "app.py"}" --data-dir "{data_dir}" --background'
            winreg.SetValueEx(key, "WhisperLocal", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "WhisperLocal")
            except FileNotFoundError:
                pass


class PasteManager(QObject):
    finished = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.generation = 0

    def cancel(self):
        self.generation += 1

    def paste(self, text, target):
        self.generation += 1
        generation = self.generation
        deadline = time.monotonic() + 3.0

        def attempt():
            if generation != self.generation:
                return
            if not native.same_target(target):
                self.finished.emit(False, "Поле ввода изменилось. Нажмите «Копировать» и вставьте текст.")
                return
            if native.modifiers_down():
                if time.monotonic() < deadline:
                    QTimer.singleShot(60, attempt)
                else:
                    self.finished.emit(False, "Отпустите клавиши. Текст можно скопировать из панели.")
                return
            clipboard = QApplication.clipboard()
            previous = {}
            mime = clipboard.mimeData()
            if mime:
                for format_name in mime.formats():
                    previous[format_name] = QByteArray(mime.data(format_name))
            clipboard.setText(text)
            sequence = native.user32.GetClipboardSequenceNumber()

            def restore():
                if native.user32.GetClipboardSequenceNumber() != sequence:
                    return  # Never overwrite something the user copied in the meantime.
                old = QMimeData()
                for name, value in previous.items():
                    old.setData(name, value)
                clipboard.setMimeData(old)

            def send():
                if generation != self.generation:
                    restore()
                    return
                if not native.same_target(target):
                    restore()
                    self.finished.emit(False, "Поле ввода изменилось. Текст доступен для копирования.")
                    return
                if native.modifiers_down():
                    restore()
                    QTimer.singleShot(60, attempt)
                    return
                try:
                    native.paste_shortcut()
                except Exception as error:
                    # Keep the transcript on the clipboard if Windows blocked the paste.
                    self.finished.emit(False, str(error))
                    return
                QTimer.singleShot(1600, restore)
                self.finished.emit(True, "")

            QTimer.singleShot(60, send)
        attempt()


class Controller(QObject):
    hotkey = Signal(str)

    def __init__(self, app, data_dir, background=False, no_hook=False):
        super().__init__()
        self.app, self.data_dir = app, data_dir
        self.config = load_config(data_dir)
        self.state = "loading"
        self.ready = False
        self.shutting_down = False
        self.request_id = None
        self.last_text = ""
        self.target = None
        self.record_started = 0.0
        self.stdout_buffer = b""
        self.stderr_tail = b""
        self.worker = None
        self.overlay = Overlay()
        self.overlay.set_wave_color(self.config.get("wave_color", "green"))
        self.overlay.set_style(self.config.get("bar_style", "flow"))
        self.overlay.set_anchor(self.config.get("bar_position"))
        self.settings = SettingsWindow(self.config)
        self.sounds = SoundCues(data_dir, self.config)
        self.recorder = Recorder(self.config["max_recording_seconds"])
        self.paste_manager = PasteManager(self)
        self.paste_manager.finished.connect(self._pasted)
        self.overlay.cancelRequested.connect(self.cancel)
        self.overlay.finishRequested.connect(self.finish_if_recording)
        self.overlay.copyRequested.connect(self.copy_last)
        self.overlay.retryPasteRequested.connect(self.retry_paste)
        self.overlay.settingsRequested.connect(self.show_settings)
        self.overlay.anchorChanged.connect(self.save_bar_position)
        self.settings.changed.connect(self.save_config)
        self.settings.previewRequested.connect(self.preview)
        self.settings.previewSoundRequested.connect(lambda kind: self.sounds.play(kind, force=True))
        self.settings.resetPositionRequested.connect(self.reset_bar_position)
        self.settings.retryRequested.connect(self.restart_worker)
        self.settings.copyRequested.connect(self.copy_last)
        self.hotkey.connect(self.on_hotkey)
        self.hold_timer = QTimer(self)
        self.hold_timer.setSingleShot(True)
        self.hold_timer.setInterval(self.config["hold_ms"])
        self.hold_timer.timeout.connect(self.begin_recording)
        self.meter_timer = QTimer(self)
        self.meter_timer.setInterval(33)
        self.meter_timer.timeout.connect(self.meter)
        self.operation_timer = QTimer(self)
        self.operation_timer.setSingleShot(True)
        self.operation_timer.timeout.connect(self._timeout)
        self.hook = None
        if not no_hook:
            self.hook = native.KeyboardHook(self.hotkey.emit, self.config["hold_ms"] / 1000)
            self.hook.start()
            self.hook.ready.wait(3)
            if self.hook.error or not self.hook.ready.is_set():
                self.settings.set_status("Не удалось подключить Alt. Перезапустите программу.", True)
                LOG.error("Hotkey hook failed: %s", self.hook.error)
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("Whisper Local · удерживайте левый Alt")
        menu = QMenu()
        menu.setStyleSheet("QMenu { padding:6px; } QMenu::item { padding:7px 24px; }")
        menu.addAction("Открыть Whisper Local", self.show_settings)
        menu.addAction("Посмотреть панель", self.preview)
        self.pause_action = QAction("Приостановить диктовку", menu)
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self.set_paused)
        menu.addAction(self.pause_action)
        self.copy_action = menu.addAction("Скопировать последний текст", self.copy_last)
        self.copy_action.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Выйти", app.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.show()
        app.aboutToQuit.connect(self.shutdown)
        if not background:
            self.show_settings()
        self.start_worker()

    def save_config(self):
        self.overlay.set_wave_color(self.config.get("wave_color", "green"))
        self.overlay.set_style(self.config.get("bar_style", "flow"))
        self.sounds.prepare()
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temp = self.data_dir / "config.json.tmp"
            temp.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.data_dir / "config.json")
            set_autostart(self.config["autostart"], self.data_dir)
        except Exception:
            LOG.exception("Cannot save settings")
            self.settings.set_status("Не удалось сохранить настройки. Проверьте доступ к папке программы.", True)

    def save_bar_position(self, anchor):
        self.config["bar_position"] = anchor
        self.save_config()

    def reset_bar_position(self):
        self.config["bar_position"] = None
        self.overlay.set_anchor(None)
        self.save_config()
        self.preview()

    def show_settings(self):
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_settings()

    def set_paused(self, paused):
        if paused:
            self.cancel()
        if self.hook:
            self.hook.enabled = not paused
        self.tray.setToolTip("Whisper Local · пауза" if paused else "Whisper Local · удерживайте левый Alt")
        self.settings.set_status("Диктовка приостановлена через меню в трее." if paused
                                 else "Готов к диктовке · модель работает на видеокарте")

    def preview(self):
        if self.state == "recording":
            return
        self.overlay.preview()

    def start_worker(self):
        self.state, self.ready = "loading", False
        self.settings.set_status("Подготавливаю Whisper на видеокарте…")
        self.stdout_buffer = b""
        self.stderr_tail = b""
        for name in ("asr_python", "model_path", "cuda_path"):
            if not Path(self.config[name]).exists():
                self.fail("Не найден компонент Whisper. Проверьте пути в data/config.json.")
                return
        worker = QProcess(self)
        self.worker = worker
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        environment.insert("PYTHONUTF8", "1")
        environment.insert("HF_HUB_OFFLINE", "1")
        environment.insert("HF_HUB_DISABLE_TELEMETRY", "1")
        worker.setProcessEnvironment(environment)
        worker.setWorkingDirectory(str(ROOT))
        worker.readyReadStandardOutput.connect(self.read_worker)
        worker.readyReadStandardError.connect(self.read_stderr)
        worker.finished.connect(self.worker_finished)
        worker.errorOccurred.connect(self.worker_error)
        worker.setProgram(self.config["asr_python"])
        worker.setArguments(["-u", "-B", str(ROOT / "asr_worker.py"), "--model",
                             self.config["model_path"], "--cuda", self.config["cuda_path"]])
        worker.start()
        self.operation_timer.start(90000)

    def read_stderr(self):
        if self.worker:
            data = bytes(self.worker.readAllStandardError())
            self.stderr_tail = (self.stderr_tail + data)[-5000:]

    def read_worker(self):
        if not self.worker:
            return
        self.stdout_buffer += bytes(self.worker.readAllStandardOutput())
        while b"\n" in self.stdout_buffer:
            line, self.stdout_buffer = self.stdout_buffer.split(b"\n", 1)
            try:
                event = json.loads(line)
            except ValueError:
                LOG.warning("Worker returned an invalid event")
                continue
            self.handle_worker_event(event)

    def handle_worker_event(self, event):
        kind = event.get("type")
        if kind == "ready":
            self.operation_timer.stop()
            self.state, self.ready = "idle", True
            LOG.info("CUDA worker ready in %s seconds", event.get("load_seconds"))
            self.settings.set_status("Готов к диктовке · модель работает на видеокарте")
            if self.overlay.isVisible() and self.overlay.mode == "loading":
                self.overlay.present("ready", timeout=2)
        elif kind == "fatal":
            LOG.error("ASR initialization: %s", event.get("error"))
            self.fail("Whisper не запустился. Откройте настройки и перезапустите распознавание.")
        elif kind in ("result", "error"):
            self.operation_timer.stop()
            if self.state == "canceling":
                self.state = "idle"
                return
            if event.get("id") != self.request_id:
                return
            self.request_id = None
            if kind == "error":
                LOG.error("ASR operation: %s", event.get("error"))
                self.state = "idle"
                self.overlay.present("error", "Не удалось распознать речь. Попробуйте ещё раз.", timeout=6)
                self._escape(False)
                return
            text = event.get("text", "").strip()
            LOG.info("Transcribed: %.3fs, %d characters", event.get("seconds", 0), len(text))
            if not text:
                self.state = "idle"
                self._escape(False)
                self.overlay.present("empty", "Попробуйте говорить ближе к микрофону.", timeout=3)
                return
            self.last_text = text
            self.copy_action.setEnabled(True)
            self.settings.copy.setEnabled(True)
            self.settings.latest_label.setText("Последнее распознавание готово. Его можно скопировать кнопкой ниже.")
            self.state = "pasting"
            self.paste_manager.paste(text, self.target)

    def worker_error(self, error):
        if not self.shutting_down:
            LOG.error("Worker process error: %s", error)
            self.fail("Не удалось запустить локальный Whisper. Откройте настройки.")

    def worker_finished(self, code, status):
        if not self.shutting_down and self.state != "error":
            LOG.error("Worker exited: %s; %s", code, self.stderr_tail.decode("utf-8", "replace"))
            self.fail("Процесс Whisper остановился. Перезапустите его в настройках.")

    def restart_worker(self):
        self.cancel()
        self.operation_timer.stop()
        if self.worker:
            self.worker.finished.disconnect()
            self.worker.errorOccurred.disconnect()
            self.worker.kill()
            self.worker.waitForFinished(2000)
            self.worker.deleteLater()
        self.start_worker()

    def _escape(self, enabled):
        if self.hook:
            self.hook.escape_enabled = enabled

    def on_hotkey(self, event):
        if event == "down":
            # A second Alt press must not redirect a pending transcript to a new window.
            if self.state not in ("recording", "processing", "canceling", "pasting"):
                self.target = native.focus_target()
            self.hold_timer.start()
        elif event == "up":
            # A release can race the 180 ms Qt timer under load; never leave a recording running.
            self.hold_timer.stop()
            if self.state == "recording":
                self.finish_recording()
        elif event == "cancel":
            self.cancel()
        elif event == "hook_error":
            LOG.error("Hotkey callback failed")
            self.cancel()

    def begin_recording(self):
        if self.hook and (not self.hook.state.held or self.hook.state.canceled):
            return
        if self.pause_action.isChecked():
            return
        if not self.ready:
            if self.state == "error":
                self.overlay.present("error", "Whisper не готов. Откройте настройки.", timeout=5)
            else:
                self.overlay.present("loading", timeout=5)
            return
        if self.state in ("processing", "canceling", "pasting"):
            return
        self.overlay.demo = False
        try:
            self.recorder.start(self.config.get("microphone"), self.config.get("microphone_name"))
        except Exception as error:
            LOG.error("Microphone: %s", error)
            self.overlay.present("error", "Микрофон недоступен. Выберите другой в настройках.", timeout=6)
            self.settings.set_status("Микрофон недоступен. Выберите устройство и попробуйте снова.", True)
            return
        self.record_started = time.monotonic()
        self.state = "recording"
        self._escape(True)
        self.overlay.present("recording")
        self.sounds.play("start")
        self.meter_timer.start()

    def meter(self):
        if self.state != "recording":
            return
        duration = self.recorder.samples / 16000
        self.overlay.sample(self.recorder.level, duration)
        if self.recorder.stream and not self.recorder.stream.active:
            self.cancel()
            self.overlay.present("error", "Микрофон отключился. Подключите его и повторите запись.", timeout=6)
            return
        if (self.recorder.samples >= self.recorder.limit
                or time.monotonic() - self.record_started > self.config["max_recording_seconds"] + 1):
            self.finish_recording()

    def finish_recording(self):
        self.meter_timer.stop()
        audio = self.recorder.stop()
        if self.recorder.overflow:
            LOG.warning("Microphone reported dropped frames")
        if audio.size < 4800:
            self.state = "idle"
            self._escape(False)
            self.overlay.hide()
            return
        self.request_id = uuid.uuid4().hex
        self.state = "processing"
        self.overlay.present("processing")
        request = {"type": "transcribe", "id": self.request_id,
                   "language": self.config.get("language"),
                   "audio": base64.b64encode(audio.astype("<f4", copy=False).tobytes()).decode("ascii")}
        if not self.worker or self.worker.state() != QProcess.ProcessState.Running:
            self.fail("Whisper остановился. Перезапустите его в настройках.")
            return
        self.worker.write((json.dumps(request) + "\n").encode("utf-8"))
        self.operation_timer.start(120000)

    def finish_if_recording(self):
        if self.state == "recording":
            self.finish_recording()

    def _pasted(self, success, reason):
        if self.state != "pasting":
            return
        self.state = "idle"
        self._escape(False)
        if success:
            self.sounds.play("insert")
            self.overlay.present("result", timeout=.6)
            self.settings.set_status("Готов к следующей диктовке · CUDA")
        else:
            self.overlay.present("manual", self.last_text, timeout=12, title="Не удалось вставить · выберите поле")
            self.settings.set_status(reason)

    def retry_paste(self):
        if self.state != "idle" or not self.last_text:
            return
        self.target = native.focus_target()
        self.state = "pasting"
        self.overlay.present("processing", title="Вставляю…")
        self.paste_manager.paste(self.last_text, self.target)

    def copy_last(self):
        if self.last_text:
            QApplication.clipboard().setText(self.last_text)
            self.overlay.present("copied", timeout=1.6)

    def cancel(self):
        self.hold_timer.stop()
        self.paste_manager.cancel()
        was_active = self.state in ("recording", "processing", "pasting")
        if self.state == "recording":
            self.meter_timer.stop()
            self.recorder.cancel()
            self.state = "idle"
        elif self.state == "processing":
            self.state = "canceling"
            self.request_id = None
        elif self.state == "pasting":
            self.state = "idle"
        self._escape(False)
        if was_active:
            self.overlay.present("canceled", timeout=.9)
        elif self.state == "loading":
            # Dismiss the loading capsule without aborting model preparation.
            self.overlay.hide()

    def fail(self, message):
        self.cancel()
        self.operation_timer.stop()
        self.state, self.ready = "error", False
        self.settings.set_status(message, True)
        self.overlay.present("error", message, timeout=7)

    def _timeout(self):
        LOG.error("Worker timeout in state %s", self.state)
        self.fail("Whisper отвечает слишком долго. Перезапустите распознавание в настройках.")

    def shutdown(self):
        if self.shutting_down:
            return
        self.shutting_down = True
        self.hold_timer.stop()
        self.meter_timer.stop()
        self.operation_timer.stop()
        self.recorder.cancel()
        self.paste_manager.cancel()
        if self.hook:
            self.hook.stop()
        if self.worker and self.worker.state() != QProcess.ProcessState.NotRunning:
            self.worker.write(b'{"type":"quit"}\n')
            self.worker.closeWriteChannel()
            if not self.worker.waitForFinished(1200):
                self.worker.kill()
                self.worker.waitForFinished(2000)
        self.tray.hide()
        LOG.info("Stopped")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_INSTALL / "data")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--quit", action="store_true")
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(args.data_dir / "app.log", maxBytes=300000, backupCount=2, encoding="utf-8")
    logging.basicConfig(handlers=[handler], level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = QApplication(sys.argv[:1])
    prepare_fonts()
    app.setApplicationName("Whisper Local")
    app.setOrganizationName("WhisperLocal")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    server_name = "WhisperLocal-" + hashlib.sha256(str(Path.home()).encode()).hexdigest()[:16]
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    if socket.waitForConnected(500):
        command = b"quit" if args.quit else b"preview" if args.preview else b"show"
        socket.write(command)
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return 0
    if args.quit:
        return 0
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(server_name):
        LOG.error("Could not acquire single-instance server")
        return 1
    controller = Controller(app, args.data_dir, args.background)
    clients = []

    def accept():
        while server.hasPendingConnections():
            client = server.nextPendingConnection()
            clients.append(client)

            def receive(client=client):
                command = bytes(client.readAll())
                if command == b"quit":
                    app.quit()
                elif command == b"preview":
                    controller.preview()
                elif command == b"show":
                    controller.show_settings()
                client.disconnectFromServer()
            client.readyRead.connect(receive)
            client.disconnected.connect(lambda client=client: clients.remove(client) if client in clients else None)
            if client.bytesAvailable():
                receive()

    server.newConnection.connect(accept)

    def exception_hook(exc_type, value, traceback):
        LOG.error("Unhandled error", exc_info=(exc_type, value, traceback))
        controller.fail("Произошла ошибка. Перезапустите Whisper Local через меню в трее.")
    sys.excepthook = exception_hook
    LOG.info("Started")
    result = app.exec()
    server.close()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
