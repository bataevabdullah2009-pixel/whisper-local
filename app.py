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

from PySide6.QtCore import (QObject, Signal, QTimer, QProcess, QProcessEnvironment,
                           QMimeData, QByteArray)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QFileDialog
from PySide6.QtGui import QAction

from audio_capture import Recorder
from sound_cues import SoundCues
from ui import Overlay, SettingsWindow, app_icon, prepare_fonts
import platform_native as native
from runtime import APP_ID, data_directory, set_autostart, worker_command
from model_manager import validate_model
from setup_service import SetupService
from system_events import SystemEvents
from memory_monitor import MemoryMonitor

ROOT = Path(__file__).resolve().parent
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
    if default.get("compute_type") not in ("auto", "int8"):
        default["compute_type"] = "auto"
    if default.get("idle_unload_seconds") not in (0, 60, 300, 600, 1800):
        default["idle_unload_seconds"] = 300
    return default


class PasteManager(QObject):
    finished = Signal(bool, str)

    def __init__(self, parent=None, allowed=None):
        super().__init__(parent)
        self.generation = 0
        self.allowed = allowed or (lambda: True)
        self.restore_pending = None

    def cancel(self):
        self.generation += 1
        if self.restore_pending:
            self.restore_pending()

    def paste(self, text, target):
        self.cancel()
        generation = self.generation
        deadline = time.monotonic() + 3.0

        def attempt():
            if not self.allowed() or generation != self.generation:
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
            sequence = native.clipboard_sequence()

            def restore():
                if self.restore_pending is restore:
                    self.restore_pending = None
                if native.clipboard_sequence() != sequence:
                    return  # Never overwrite something the user copied in the meantime.
                old = QMimeData()
                for name, value in previous.items():
                    old.setData(name, value)
                clipboard.setMimeData(old)

            self.restore_pending = restore

            def send():
                if not self.allowed() or generation != self.generation:
                    restore()
                    return
                if time.monotonic() >= deadline:
                    restore()
                    self.finished.emit(False, "Вставка задержалась. Текст доступен для копирования.")
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
        self.suspended = False
        self.worker_restart = False
        self.request_id = None
        self.last_text = ""
        self.target = None
        self.record_started = 0.0
        self.stdout_buffer = b""
        self.stderr_tail = b""
        self.worker = None
        self.device_description = "процессор"
        self.pending_device = "auto"
        self.overlay = Overlay()
        self.overlay.set_wave_color(self.config.get("wave_color", "green"))
        self.overlay.set_style(self.config.get("bar_style", "flow"))
        self.overlay.set_anchor(self.config.get("bar_position"))
        self.settings = SettingsWindow(self.config)
        self.sounds = SoundCues(data_dir, self.config)
        self.recorder = Recorder(self.config["max_recording_seconds"])
        self.paste_manager = PasteManager(self, self.can_paste)
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
        self.settings.freeMemoryRequested.connect(self.release_memory)
        self.settings.precisionRequested.connect(self.change_precision)
        self.settings.copyRequested.connect(self.copy_last)
        self.settings.recordRequested.connect(self.practice_recording)
        self.settings.permissionsRequested.connect(self.request_permissions)
        self.settings.model_page.downloadRequested.connect(self.download_model)
        self.settings.model_page.importRequested.connect(self.import_model)
        self.settings.model_page.cancelRequested.connect(self.cancel_download)
        self.setup = SetupService(self)
        self.setup.event.connect(self.setup_event)
        self.setup.failed.connect(self.setup_failed)
        self.probe = SetupService(self)
        self.probe.event.connect(self.settings.model_page.set_hardware)
        self.probe.failed.connect(lambda message: self.settings.model_page.hardware.setText(
            "Автоматическая проверка недоступна. Начните с модели «Быстрая» и процессора."))
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
        self.pending_audio = None
        self.memory_released = False
        self.wake_unloaded = False
        self.idle_since = time.monotonic()
        self.idle_timer = QTimer(self)
        self.idle_timer.setInterval(1000)
        self.idle_timer.timeout.connect(self.check_idle)
        self.idle_timer.start()
        self.memory_monitor = MemoryMonitor(self)
        self.memory_monitor.measured.connect(self.memory_measured)
        self.memory_monitor.set_pids(self.memory_pids())
        self.memory_monitor.start()
        # Ensure a native window exists for Windows power broadcasts, even in tray mode.
        self.settings.winId()
        self.system_events = SystemEvents(app, self)
        self.system_events.interrupted.connect(self.system_interrupted)
        self.system_events.resumed.connect(self.system_resumed)
        self.hook = None
        if not no_hook:
            self.start_hook()
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(f"Whisper Local · удерживайте {native.HOTKEY_NAME}")
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
        self.probe.start("probe")

    def start_hook(self):
        if self.hook:
            self.hook.stop()
        self.hook = native.KeyboardHook(self.hotkey.emit, self.config["hold_ms"] / 1000)
        self.hook.start()
        self.hook.ready.wait(3)
        warning = ""
        if self.hook.error or not self.hook.ready.is_set():
            warning = str(self.hook.error or "Не удалось подключить горячую клавишу. Перезапустите приложение.")
            LOG.error("Hotkey hook failed: %s", self.hook.error)
        self.settings.permission_note.setText(warning)
        self.settings.permission_note.setVisible(bool(warning))

    def request_permissions(self):
        native.request_permissions()
        self.start_hook()

    def download_model(self, model_id, device):
        if self.state in ("recording", "waiting_model", "processing", "canceling", "pasting", "loading", "unloading"):
            self.settings.model_page.message.setText("Дождитесь завершения текущей операции и повторите.")
            return
        self.pending_device = device
        self.settings.model_page.user_selected = True
        self.settings.model_page.set_busy(True, "Подготавливаем загрузку…")
        self.setup.start("download", model_id, str(self.data_dir / "models"))

    def cancel_download(self):
        self.setup.stop()
        self.settings.model_page.set_busy(False, "Загрузка остановлена. Нажмите «Скачать и настроить», чтобы продолжить.")

    def setup_failed(self, message):
        self.settings.model_page.set_busy(False, message)

    def setup_event(self, event):
        if event.get("type") == "progress":
            self.settings.model_page.update_progress(event)
        elif event.get("type") == "downloaded":
            self.activate_model(event["path"], self.pending_device, event["model_id"])

    def import_model(self, device):
        if self.state in ("recording", "waiting_model", "processing", "canceling", "pasting", "loading", "unloading"):
            self.settings.model_page.message.setText("Дождитесь завершения текущей операции и повторите.")
            return
        path = QFileDialog.getExistingDirectory(self.settings, "Папка модели faster-whisper")
        if path:
            self.activate_model(path, device, "local")

    def activate_model(self, path, device, model_id):
        try:
            path = validate_model(path)
        except (ValueError, OSError) as error:
            self.setup_failed(str(error))
            return
        # A download may finish while the user is dictating with the old model.
        if self.state in ("recording", "waiting_model", "processing", "canceling", "pasting", "unloading"):
            self.settings.model_page.set_busy(False, "Модель скачана. Завершите диктовку и нажмите «Скачать и настроить» ещё раз.")
            return
        self.config.update(model_path=str(path), model_id=model_id, device=device)
        self.save_config()
        self.settings.model_page.set_busy(False, "Проверяем модель. Первый запуск может занять несколько минут…")
        self.restart_worker()

    def practice_recording(self):
        if self.state == "recording":
            self.settings.scratch.setFocus()
            self.finish_recording()
            return
        if self.state in ("waiting_model", "processing", "canceling", "pasting", "unloading"):
            return
        self.settings.scratch.setFocus()
        self.target = native.focus_target()
        self.begin_recording(manual=True)

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

    def memory_pids(self):
        pid = int(self.worker.processId()) if self.worker else 0
        return (os.getpid(), pid) if pid else (os.getpid(),)

    def memory_measured(self, pids, values):
        if not self.shutting_down and tuple(pids) == self.memory_pids():
            self.settings.set_memory_usage(values)

    def check_idle(self):
        if self.shutting_down:
            return
        self.memory_monitor.set_pids(self.memory_pids())
        available = (self.state == "idle" and self.ready and not self.suspended
                     and not self.hold_timer.isActive() and not (self.hook and self.hook.state.held))
        self.settings.free_memory.setEnabled(available)
        self.settings.precision.setEnabled(self.state in ("idle", "unloaded", "setup", "error"))
        seconds = self.config.get("idle_unload_seconds", 300)
        if available and seconds and time.monotonic() - self.idle_since >= seconds:
            self.release_memory()

    def release_memory(self):
        if (self.shutting_down or self.suspended or self.state != "idle" or not self.ready
                or self.hold_timer.isActive() or (self.hook and self.hook.state.held)):
            return False
        self.memory_released = True
        self.state = "unloading"
        self.settings.free_memory.setEnabled(False)
        self.settings.set_status("Освобождаем память модели…")
        self._stop_worker()
        if not self.worker:
            self.model_unloaded()
        return True

    def model_unloaded(self):
        self.state, self.ready = "unloaded", False
        self.settings.set_status("Память модели освобождена. Можно начинать диктовку.")
        self.settings.engine_label.setText("Модель выгружена. Она загрузится при следующей диктовке.")
        self.settings.model_page.message.setText("Модель выгружена из памяти. Начните диктовку — загрузка произойдёт автоматически.")
        self.settings.free_memory.setEnabled(False)
        self.memory_monitor.set_pids(self.memory_pids())

    def change_precision(self, precision):
        if (precision not in ("auto", "int8") or self.state not in ("idle", "unloaded", "setup", "error")
                or self.shutting_down or self.suspended or self.hold_timer.isActive()
                or (self.hook and self.hook.state.held)):
            self.settings.precision.blockSignals(True)
            self.settings.precision.setCurrentIndex(max(0, self.settings.precision.findData(self.config["compute_type"])))
            self.settings.precision.blockSignals(False)
            return
        self.config["compute_type"] = precision
        self.save_config()
        if self.state == "idle":
            self.restart_worker()

    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_settings()

    def set_paused(self, paused):
        if paused:
            self.cancel()
        if self.hook:
            self.hook.enabled = not paused
        self.tray.setToolTip("Whisper Local · пауза" if paused else f"Whisper Local · удерживайте {native.HOTKEY_NAME}")
        self.settings.set_status("Диктовка приостановлена через меню в трее." if paused
                                 else f"Готов к диктовке · {self.device_description}" if self.ready else "Сначала подготовьте модель")

    def preview(self):
        if self.state == "recording":
            return
        self.overlay.preview()

    def can_paste(self):
        return (not self.shutting_down and not self.suspended
                and self.system_events.check())

    def system_interrupted(self):
        self.suspended = True
        self.wake_unloaded = self.memory_released and self.state in ("unloaded", "unloading")
        active = self.state in ("recording", "waiting_model", "processing", "pasting")
        self.cancel(restart_asr=False)
        self.target = None
        self.state, self.ready = "suspended", False
        self._stop_worker()
        if self.hook:
            self.hook.state.held = False
            self.hook.state.canceled = True
        if active:
            self.overlay.present("canceled", "Диктовка отменена при переходе в сон.", timeout=3)

    def system_resumed(self):
        if self.shutting_down or not self.suspended:
            return
        self.suspended = False
        if self.hook:
            self.start_hook()
        if self.wake_unloaded:
            self.state = "unloading"
            if not self.worker:
                self.model_unloaded()
        else:
            self.restart_worker()

    def start_worker(self):
        if self.shutting_down or self.suspended or self.worker:
            return
        self.memory_released = False
        self.state, self.ready = "loading", False
        self.settings.model_page.practice.hide()
        self.settings.set_status("Подготавливаем локальное распознавание…")
        self.stdout_buffer = b""
        self.stderr_tail = b""
        try:
            validate_model(self.config.get("model_path", ""))
        except (OSError, ValueError) as error:
            self.state = "setup"
            self.settings.set_status("Выберите модель для первого запуска")
            self.settings.model_page.message.setText(str(error))
            self.settings.show_page(1)
            self.show_settings()
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
        worker.readyReadStandardOutput.connect(lambda: self.read_worker(worker))
        worker.readyReadStandardError.connect(lambda: self.read_stderr(worker))
        worker.finished.connect(lambda code, status: self.worker_finished(code, status, worker))
        worker.errorOccurred.connect(lambda error: self.worker_error(error, worker))
        command = worker_command("asr", "--model", self.config["model_path"],
                                 "--device", self.config.get("device", "auto"),
                                 "--compute-type", self.config.get("compute_type", "auto"))
        worker.setProgram(command[0])
        worker.setArguments(command[1:])
        worker.started.connect(lambda: self.memory_monitor.set_pids(self.memory_pids()))
        worker.start()
        self.operation_timer.start(300000)

    def read_stderr(self, source=None):
        if self.worker and (source is None or source is self.worker):
            data = bytes(self.worker.readAllStandardError())
            self.stderr_tail = (self.stderr_tail + data)[-5000:]

    def read_worker(self, source=None):
        if not self.worker or (source is not None and source is not self.worker):
            return
        if self.worker_restart or self.state in ("canceling", "suspended", "unloading") or self.shutting_down:
            self.worker.readAllStandardOutput()
            return
        self.stdout_buffer += bytes(self.worker.readAllStandardOutput())
        while b"\n" in self.stdout_buffer:
            line, self.stdout_buffer = self.stdout_buffer.split(b"\n", 1)
            try:
                event = json.loads(line)
            except ValueError:
                LOG.warning("Worker returned an invalid event")
                continue
            if source is not None and source is not self.worker:
                break
            self.handle_worker_event(event)

    def handle_worker_event(self, event):
        if (not isinstance(event, dict) or self.shutting_down or self.worker_restart
                or not self.system_events.check() or self.suspended):
            return
        kind = event.get("type")
        if kind == "ready":
            if self.state not in ("loading", "recording", "waiting_model"):
                return
            self.operation_timer.stop()
            waiting = self.state == "waiting_model"
            self.ready = True
            if self.state == "loading":
                self.state = "idle"
            self.idle_since = time.monotonic()
            LOG.info("%s worker ready in %s seconds", event.get("device"), event.get("load_seconds"))
            self.device_description = "видеокарта NVIDIA" if event.get("device") == "cuda" else "процессор"
            self.settings.set_status(f"Готов к диктовке · {self.device_description}")
            self.settings.engine_label.setText(f"Whisper · {self.device_description} · {event.get('compute_type', 'auto')}\nМодель загружена в память.")
            self.settings.model_page.set_ready(self.config["model_path"], self.device_description)
            if event.get("fallback"):
                self.settings.model_page.message.setText("Ускорение NVIDIA недоступно. Модель готова и работает на процессоре.")
            if waiting:
                audio, self.pending_audio = self.pending_audio, None
                self.submit_audio(audio)
            elif self.state == "idle" and self.overlay.isVisible() and self.overlay.mode == "loading":
                self.overlay.present("ready", timeout=2)
        elif kind == "fatal":
            LOG.error("ASR initialization: %s", event.get("error"))
            self.fail("Модель не запустилась. Попробуйте модель «Быстрая» или повторите её загрузку.")
        elif kind in ("result", "error"):
            if (self.state != "processing" or self.request_id is None
                    or event.get("id") != self.request_id):
                return
            self.operation_timer.stop()
            self.request_id = None
            if kind == "error":
                LOG.error("ASR operation: %s", event.get("error"))
                self.state = "idle"
                self.idle_since = time.monotonic()
                self.overlay.present("error", "Не удалось распознать речь. Попробуйте ещё раз.", timeout=6)
                self._escape(False)
                return
            text = event.get("text", "").strip()
            LOG.info("Transcribed: %.3fs, %d characters", event.get("seconds", 0), len(text))
            if not text:
                self.state = "idle"
                self.idle_since = time.monotonic()
                self._escape(False)
                self.overlay.present("empty", "Попробуйте говорить ближе к микрофону.", timeout=3)
                return
            self.last_text = text
            self.copy_action.setEnabled(True)
            self.settings.copy.setEnabled(True)
            self.settings.latest_label.setText("Последнее распознавание готово. Его можно скопировать кнопкой ниже.")
            self.state = "pasting"
            self.paste_manager.paste(text, self.target)

    def worker_error(self, error, source=None):
        if source is not None and source is not self.worker:
            return
        if not self.shutting_down and not self.worker_restart and self.state not in ("error", "suspended", "unloading"):
            LOG.error("Worker process error: %s", error)
            self.fail("Не удалось запустить локальный Whisper. Откройте настройки.")

    def worker_finished(self, code, status, source=None):
        if source is not None and source is not self.worker:
            return
        restart = self.worker_restart
        self.worker_restart = False
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        if self.shutting_down or self.suspended:
            return
        if restart:
            self.start_worker()
        elif self.state == "unloading":
            self.model_unloaded()
        elif self.state != "error":
            LOG.error("Worker exited: %s", code)
            self.fail("Процесс Whisper остановился. Перезапустите его в настройках.")

    def restart_worker(self):
        if self.shutting_down or self.suspended or self.state == "unloading":
            return
        self.cancel(restart_asr=False)
        self.state, self.ready = "loading", False
        self._stop_worker(restart=True)

    def _stop_worker(self, restart=False):
        """Kill native inference; reload only after process exit, without blocking Qt."""
        self.operation_timer.stop()
        self.request_id = None
        self.pending_audio = None
        self.ready = False
        self.worker_restart = restart
        if self.worker:
            if self.worker.state() == QProcess.ProcessState.NotRunning:
                self.worker_finished(0, QProcess.ExitStatus.NormalExit, self.worker)
            else:
                self.worker.kill()
        else:
            self.worker_restart = False
            if restart:
                self.start_worker()

    def _escape(self, enabled):
        if self.hook:
            self.hook.escape_enabled = enabled

    def on_hotkey(self, event):
        if self.shutting_down or self.suspended or not self.system_events.check():
            return
        if event == "down":
            # A second Alt press must not redirect a pending transcript to a new window.
            if self.state not in ("recording", "waiting_model", "processing", "canceling", "pasting"):
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

    def begin_recording(self, manual=False):
        if self.shutting_down or self.suspended or not self.system_events.check():
            return
        if not manual and self.hook and (not self.hook.state.held or self.hook.state.canceled):
            return
        if self.pause_action.isChecked():
            return
        if self.state == "unloaded":
            self.start_worker()
        loading = self.state == "loading" and self.worker is not None and not self.worker_restart
        if not self.ready and not loading:
            if self.state == "setup":
                self.settings.show_page(1)
                self.show_settings()
            elif self.state == "error":
                self.overlay.present("error", "Whisper не готов. Откройте настройки.", timeout=5)
            else:
                self.overlay.present("loading", timeout=5)
            return
        if self.state != "idle" and not loading:
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
        self.settings.record_button.setText("Завершить проверку")
        self._escape(True)
        self.overlay.present("recording")
        self.sounds.play("start")
        self.meter_timer.start()

    def meter(self):
        if self.state != "recording":
            return
        if not self.system_events.check():
            return
        duration = self.recorder.samples / 16000
        self.overlay.sample(self.recorder.level, duration)
        if not self.recorder.healthy():
            self.cancel()
            self.overlay.present("error", "Микрофон отключился. Подключите его и повторите запись.", timeout=6)
            return
        if (self.recorder.samples >= self.recorder.limit
                or time.monotonic() - self.record_started > self.config["max_recording_seconds"] + 1):
            self.finish_recording()

    def finish_recording(self):
        if self.state != "recording" or not self.system_events.check():
            return
        self.settings.record_button.setText("Начать проверку микрофона")
        self.meter_timer.stop()
        try:
            if not self.recorder.healthy():
                raise RuntimeError("Microphone stopped delivering audio")
            audio = self.recorder.stop()
        except Exception:
            self.cancel()
            self.overlay.present("error", "Микрофон отключился. Подключите его и повторите запись.", timeout=6)
            return
        if self.recorder.overflow:
            LOG.warning("Microphone reported dropped frames")
        if audio.size < 4800:
            self.state = "idle" if self.ready else "loading"
            self.idle_since = time.monotonic()
            self._escape(False)
            self.overlay.hide()
            return
        pcm = audio.astype("<f4", copy=False).tobytes()
        if not self.ready:
            self.pending_audio = pcm
            self.state = "waiting_model"
            self.overlay.present("loading", title="Подготавливаем модель…")
            return
        self.submit_audio(pcm)

    def submit_audio(self, pcm):
        if pcm is None:
            self.fail("Запись недоступна. Начните диктовку заново.")
            return
        self.request_id = uuid.uuid4().hex
        self.state = "processing"
        self.overlay.present("processing")
        request = {"type": "transcribe", "id": self.request_id,
                   "language": self.config.get("language"),
                   "audio": base64.b64encode(pcm).decode("ascii")}
        if not self.worker or self.worker.state() != QProcess.ProcessState.Running:
            self.fail("Whisper остановился. Перезапустите его в настройках.")
            return
        self.worker.write((json.dumps(request) + "\n").encode("utf-8"))
        self.operation_timer.start(600000)

    def finish_if_recording(self):
        if self.state == "recording":
            self.finish_recording()

    def _pasted(self, success, reason):
        if self.state != "pasting":
            return
        self.state = "idle" if self.ready else "unloaded"
        self.idle_since = time.monotonic()
        self._escape(False)
        if success:
            self.sounds.play("insert")
            self.overlay.present("result", timeout=.6)
            self.settings.set_status(f"Готов к следующей диктовке · {self.device_description}")
        else:
            self.overlay.present("manual", self.last_text, timeout=12, title="Не удалось вставить · выберите поле")
            self.settings.set_status(reason)

    def retry_paste(self):
        if self.state not in ("idle", "unloaded") or not self.last_text or not self.can_paste():
            return
        self.target = native.focus_target()
        self.state = "pasting"
        self.overlay.present("processing", title="Вставляю…")
        self.paste_manager.paste(self.last_text, self.target)

    def copy_last(self):
        if self.last_text:
            QApplication.clipboard().setText(self.last_text)
            self.overlay.present("copied", timeout=1.6)

    def cancel(self, *, restart_asr=True):
        self.settings.record_button.setText("Начать проверку микрофона")
        self.hold_timer.stop()
        self.paste_manager.cancel()
        was_active = self.state in ("recording", "waiting_model", "processing", "pasting")
        self.pending_audio = None
        if self.state == "recording":
            self.meter_timer.stop()
            self.recorder.cancel()
            self.state = "idle" if self.ready else "loading"
        elif self.state == "waiting_model":
            self.state = "loading"
        elif self.state == "processing":
            self.state = "canceling"
            self.request_id = None
            self.operation_timer.stop()
            self.ready = False
        elif self.state == "pasting":
            self.state = "idle" if self.ready else "unloaded"
        if self.state == "idle":
            self.idle_since = time.monotonic()
        self._escape(False)
        if was_active:
            self.overlay.present("canceled", timeout=.9)
        elif self.state == "loading":
            # Dismiss the loading capsule without aborting model preparation.
            self.overlay.hide()
        if was_active and self.state == "canceling" and restart_asr:
            self.settings.set_status("Диктовка отменена. Подготавливаем модель для следующей записи…")
            self._stop_worker(restart=True)

    def fail(self, message):
        self.cancel(restart_asr=False)
        self.operation_timer.stop()
        self.state, self.ready = "error", False
        self._stop_worker()
        self.settings.set_status(message, True)
        self.overlay.present("error", message, timeout=7)

    def _timeout(self):
        LOG.error("Worker timeout in state %s", self.state)
        self.fail("Whisper отвечает слишком долго. Перезапустите распознавание в настройках.")

    def shutdown(self):
        if self.shutting_down:
            return
        self.shutting_down = True
        self.system_events.close()
        self.setup.stop()
        self.probe.stop()
        self.hold_timer.stop()
        self.meter_timer.stop()
        self.operation_timer.stop()
        self.idle_timer.stop()
        self.memory_monitor.stop()
        self.recorder.cancel()
        self.paste_manager.cancel()
        if self.hook:
            self.hook.stop()
        worker = self.worker
        self._stop_worker()
        if worker and worker.state() != QProcess.ProcessState.NotRunning:
            worker.waitForFinished(2000)
        self.tray.hide()
        LOG.info("Stopped")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=data_directory())
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--quit", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--no-hook", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-screenshot", type=Path, help=argparse.SUPPRESS)
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
    if args.smoke_test:
        settings = SettingsWindow(load_config(args.data_dir))
        settings.show_page(1)
        settings.show()
        app.processEvents()
        if args.smoke_screenshot:
            settings.grab().save(str(args.smoke_screenshot))
        settings.hide()
        return 0
    server_name = APP_ID + "-" + hashlib.sha256(str(args.data_dir.resolve()).encode()).hexdigest()[:16]
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
    controller = Controller(app, args.data_dir, args.background, args.no_hook)
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
