import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

import numpy as np
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from app import Controller, PasteManager
from audio_capture import Recorder
from system_events import SystemEvents


def application():
    return QApplication.instance() or QApplication([])


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Qt operation did not complete")
        application().processEvents()
        time.sleep(.005)


class ControllerReliability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.probe = patch("setup_service.SetupService.start").start()
        self.addCleanup(patch.stopall)

    def controller(self):
        controller = Controller(self.app, self.data, background=True, no_hook=True)
        def cleanup():
            controller.shutdown()
            controller.settings.hide()
            controller.overlay.hide()
        self.addCleanup(cleanup)
        return controller

    def running_worker(self):
        command = [sys.executable, "-u", "-B", str(Path(__file__).parent / "fixtures/busy_worker.py")]
        with patch("app.validate_model", return_value=self.data), patch("app.worker_command", return_value=command):
            controller = self.controller()
        # Keep replacement workers pointed at the same fixture.
        patch("app.validate_model", return_value=self.data).start()
        patch("app.worker_command", return_value=command).start()
        wait_until(lambda: controller.ready)
        controller.paste_manager.paste = Mock()
        return controller

    def make_busy(self, controller):
        seen = []
        handler = controller.handle_worker_event
        def receive(event):
            seen.append(event["type"])
            handler(event)
        controller.handle_worker_event = receive
        controller.state, controller.request_id = "processing", "old"
        controller.operation_timer.start(10000)
        controller.worker.write(b'{"type":"transcribe","id":"old","audio":"hang"}\n')
        wait_until(lambda: "busy" in seen)

    def test_cancel_kills_computing_process_and_new_dictation_works(self):
        controller = self.running_worker()
        self.make_busy(controller)
        old = controller.worker
        exited = []
        old.finished.connect(lambda code, status: exited.append((code, status)))
        start = time.monotonic()
        controller.cancel()
        self.assertFalse(controller.ready)
        self.assertIsNone(controller.request_id)
        wait_until(lambda: bool(exited) and controller.ready and controller.worker is not old)
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(exited[0][1], QProcess.ExitStatus.CrashExit)
        controller.state, controller.request_id = "processing", "new"
        controller.worker.write(b'{"type":"transcribe","id":"new","audio":"ok"}\n')
        wait_until(lambda: controller.state == "pasting")
        controller.paste_manager.paste.assert_called_once_with("fixture transcript", controller.target)

    def test_timeout_also_kills_computation_and_leaves_retry_available(self):
        controller = self.running_worker()
        self.make_busy(controller)
        exited = []
        controller.worker.finished.connect(lambda *args: exited.append(True))
        controller._timeout()
        wait_until(lambda: bool(exited) and controller.worker is None)
        self.assertEqual(controller.state, "error")
        self.assertFalse(controller.ready)
        self.assertFalse(controller.operation_timer.isActive())

    def test_stale_result_does_not_stop_current_timeout_or_paste(self):
        controller = self.controller()
        controller.state, controller.ready, controller.request_id = "processing", True, "current"
        controller.operation_timer.start(10000)
        controller.paste_manager.paste = Mock()
        controller.handle_worker_event({"type": "result", "id": "old", "text": "obsolete"})
        self.assertTrue(controller.operation_timer.isActive())
        self.assertEqual(controller.request_id, "current")
        self.assertEqual(controller.last_text, "")
        controller.paste_manager.paste.assert_not_called()

    def test_esc_during_model_warmup_only_dismisses_capsule(self):
        controller = self.controller()
        controller.state = "loading"
        controller.operation_timer.start(10000)
        worker = Mock()
        controller.worker = worker
        controller.cancel()
        self.assertEqual(controller.state, "loading")
        self.assertTrue(controller.operation_timer.isActive())
        worker.kill.assert_not_called()
        controller.worker = None

    def test_duplicate_result_is_not_pasted_twice(self):
        controller = self.controller()
        controller.state, controller.ready, controller.request_id = "processing", True, "current"
        controller.paste_manager.paste = Mock()
        event = {"type": "result", "id": "current", "text": "fixture transcript"}
        controller.handle_worker_event(event)
        controller.handle_worker_event(event)
        controller.paste_manager.paste.assert_called_once()

    def test_events_from_retired_process_cannot_change_replacement(self):
        controller = self.controller()
        replacement, old = Mock(), Mock()
        controller.worker = replacement
        controller.state, controller.ready = "loading", False
        controller.operation_timer.start(10000)
        controller.read_worker(old)
        controller.worker_error(QProcess.ProcessError.Crashed, old)
        controller.worker_finished(1, QProcess.ExitStatus.CrashExit, old)
        self.assertIs(controller.worker, replacement)
        self.assertEqual(controller.state, "loading")
        self.assertTrue(controller.operation_timer.isActive())
        replacement.readAllStandardOutput.assert_not_called()
        controller.worker = None

    def test_suspend_kills_pending_work_and_waits_for_wake_to_reload(self):
        controller = self.running_worker()
        self.make_busy(controller)
        controller.target = (123, 456)
        controller.system_events.suspend()
        wait_until(lambda: controller.worker is None)
        self.assertEqual(controller.state, "suspended")
        self.assertIsNone(controller.target)
        self.assertFalse(controller.ready)
        controller.handle_worker_event({"type": "result", "id": "old", "text": "late result"})
        controller.paste_manager.paste.assert_not_called()
        controller.system_events.resume()
        wait_until(lambda: controller.ready)
        self.assertEqual(controller.state, "idle")

    def test_shutdown_kills_computation_without_restarting(self):
        controller = self.running_worker()
        self.make_busy(controller)
        exited = []
        controller.worker.finished.connect(lambda *args: exited.append(True))
        with patch.object(controller, "start_worker") as restart:
            controller.shutdown()
            self.assertTrue(exited)
            restart.assert_not_called()

    def test_microphone_stop_failure_discards_recording_and_recovers(self):
        controller = self.controller()
        controller.state, controller.ready = "recording", True
        controller.recorder.healthy = Mock(return_value=True)
        controller.recorder.stop = Mock(side_effect=RuntimeError("device removed"))
        controller.finish_recording()
        self.assertEqual(controller.state, "idle")
        self.assertIsNone(controller.request_id)
        self.assertFalse(controller.meter_timer.isActive())
        self.assertEqual(controller.overlay.mode, "error")

    def test_stalled_microphone_is_cancelled_instead_of_transcribed(self):
        controller = self.controller()
        controller.state, controller.ready = "recording", True
        controller.recorder.healthy = Mock(return_value=False)
        controller.meter()
        self.assertEqual(controller.state, "idle")
        self.assertEqual(controller.overlay.mode, "error")
        self.assertIsNone(controller.request_id)


class MicrophoneReliability(unittest.TestCase):
    def setUp(self):
        self.stream = Mock(active=True)
        self.input_stream = patch("audio_capture.sd.InputStream", return_value=self.stream).start()
        self.addCleanup(patch.stopall)
        self.recorder = Recorder()
        self.addCleanup(self.recorder.cancel)
        self.status = Mock(input_underflow=False, input_overflow=False)

    def receive(self, values=None, callback=None):
        (callback or self.input_stream.call_args.kwargs["callback"])(
            np.ones((320, 1), dtype=np.float32) if values is None else values,
            320, None, self.status)

    def test_disconnect_during_stop_always_closes_and_forgets_pcm(self):
        self.recorder.start()
        self.receive()
        self.stream.stop.side_effect = RuntimeError("unplugged")
        with self.assertRaises(RuntimeError):
            self.recorder.stop()
        self.stream.close.assert_called_once()
        self.assertIsNone(self.recorder.stream)
        self.assertEqual(self.recorder.chunks, [])
        self.assertEqual(self.recorder.samples, 0)

    def test_cancel_is_safe_even_if_abort_and_close_fail(self):
        self.recorder.start()
        self.receive()
        self.stream.abort.side_effect = RuntimeError("unplugged")
        self.stream.close.side_effect = RuntimeError("unplugged")
        with patch("audio_capture.np.concatenate", side_effect=AssertionError("cancel must discard")):
            self.recorder.cancel()
        self.assertIsNone(self.recorder.stream)
        self.assertEqual(self.recorder.chunks, [])
        self.assertEqual(self.recorder.samples, 0)

    def test_start_failure_closes_stream_and_next_recording_can_start(self):
        self.stream.start.side_effect = RuntimeError("permission denied")
        with self.assertRaises(RuntimeError):
            self.recorder.start()
        self.stream.close.assert_called_once()
        self.assertIsNone(self.recorder.stream)
        self.stream.start.side_effect = None
        self.recorder.start()
        self.assertTrue(self.recorder.healthy())

    def test_old_callback_cannot_contaminate_next_recording(self):
        self.recorder.start()
        callback = self.input_stream.call_args.kwargs["callback"]
        self.recorder.cancel()
        self.recorder.start()
        self.receive(callback=callback)
        self.assertEqual(self.recorder.samples, 0)
        self.receive()
        self.assertEqual(self.recorder.samples, 320)

    def test_watchdog_catches_active_stream_without_callbacks(self):
        self.recorder.start()
        self.recorder.last_callback = time.monotonic() - 3
        self.assertFalse(self.recorder.healthy())

    def test_invalid_samples_abort_without_retaining_them(self):
        import sounddevice as sd
        self.recorder.start()
        with self.assertRaises(sd.CallbackAbort):
            self.receive(np.full((320, 1), np.nan, dtype=np.float32))
        self.assertFalse(self.recorder.healthy())
        self.assertEqual(self.recorder.chunks, [])

    def test_device_reordering_uses_name_and_never_falls_back_to_wrong_mic(self):
        devices = [{"name": "Other", "max_input_channels": 1},
                   {"name": "Chosen", "max_input_channels": 1}]
        with patch("audio_capture.sd.query_devices", return_value=devices):
            self.recorder.start(0, "Chosen")
            self.assertEqual(self.input_stream.call_args.kwargs["device"], 1)
            with self.assertRaisesRegex(RuntimeError, "отключён"):
                self.recorder.start(0, "Disconnected")
        self.assertIsNone(self.recorder.stream)


class PasteReliability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.queue, self.results = [], []
        self.clipboard = self.app.clipboard()
        self.clipboard.setText("original clipboard")
        patch("app.QTimer.singleShot", side_effect=lambda delay, callback: self.queue.append(callback)).start()
        patch("app.native.clipboard_sequence", side_effect=lambda: self.clipboard.text()).start()
        self.target = patch("app.native.same_target", return_value=True).start()
        self.modifiers = patch("app.native.modifiers_down", return_value=False).start()
        self.send = patch("app.native.paste_shortcut").start()
        self.addCleanup(patch.stopall)
        self.manager = PasteManager()
        self.manager.finished.connect(lambda *args: self.results.append(args))
        self.addCleanup(self.manager.cancel)

    def test_changed_field_between_clipboard_and_send_restores_without_paste(self):
        self.manager.paste("transcript", "field A")
        self.target.return_value = False
        self.queue.pop(0)()
        self.send.assert_not_called()
        self.assertEqual(self.clipboard.text(), "original clipboard")
        self.assertFalse(self.results[0][0])

    def test_changed_target_before_paste_does_not_touch_clipboard(self):
        self.target.return_value = False
        self.manager.paste("transcript", "field A")
        self.send.assert_not_called()
        self.assertEqual(self.clipboard.text(), "original clipboard")

    def test_cancel_between_clipboard_and_send_restores_immediately(self):
        self.manager.paste("transcript", "field A")
        self.manager.cancel()
        self.assertEqual(self.clipboard.text(), "original clipboard")
        self.queue.pop(0)()
        self.send.assert_not_called()
        self.assertEqual(self.results, [])

    def test_user_copy_is_preserved_when_cancelled(self):
        self.manager.paste("transcript", "field A")
        self.clipboard.setText("user copy")
        self.manager.cancel()
        self.queue.pop(0)()
        self.assertEqual(self.clipboard.text(), "user copy")
        self.send.assert_not_called()

    def test_next_paste_does_not_save_previous_transcript_as_original_clipboard(self):
        self.manager.paste("first", "field A")
        self.manager.paste("second", "field B")
        self.queue.pop(0)()  # Old generation must not paste/restore the new one.
        self.queue.pop(0)()
        self.queue.pop(0)()  # Restore after successful second paste.
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.clipboard.text(), "original clipboard")

    def test_sleep_boundary_prevents_already_queued_paste(self):
        self.manager.paste("transcript", "field A")
        self.manager.allowed = lambda: False
        self.queue.pop(0)()
        self.send.assert_not_called()
        self.assertEqual(self.clipboard.text(), "original clipboard")

    def test_late_send_is_not_replayed_after_sleep(self):
        with patch("app.time.monotonic", return_value=10):
            self.manager.paste("transcript", "field A")
        with patch("app.time.monotonic", return_value=14):
            self.queue.pop(0)()
        self.send.assert_not_called()
        self.assertEqual(self.clipboard.text(), "original clipboard")


class SuspendReliability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        with patch("system_events.sys.platform", "test"):
            self.monitor = SystemEvents(self.app)
        self.addCleanup(self.monitor.close)
        self.events = []
        self.monitor.interrupted.connect(lambda: self.events.append("suspend"))
        self.monitor.resumed.connect(lambda: self.events.append("resume"))

    def test_native_suspend_and_duplicate_resume_broadcasts(self):
        self.monitor.power_event(4)
        self.monitor.power_event(4)
        self.assertFalse(self.monitor.check())
        self.monitor.power_event(18)
        self.monitor.power_event(7)
        self.assertEqual(self.events, ["suspend", "resume"])

    def test_clock_gap_invalidates_work_before_queued_result(self):
        self.monitor.last_tick = (100, 100)
        with patch("system_events.time.monotonic", return_value=110), patch("system_events.time.time", return_value=110):
            self.assertFalse(self.monitor.check())
            self.assertTrue(self.monitor.check())
        self.assertEqual(self.events, ["suspend", "resume"])

    def test_wall_clock_detects_sleep_when_monotonic_clock_pauses(self):
        self.monitor.last_tick = (100, 100)
        with patch("system_events.time.monotonic", return_value=101), patch("system_events.time.time", return_value=110):
            self.assertFalse(self.monitor.check())
        self.assertEqual(self.events, ["suspend", "resume"])

    def test_mac_observer_blocks_and_tokens_live_until_unsubscribe(self):
        appkit, foundation, center = Mock(), Mock(), Mock()
        appkit.NSWorkspace.sharedWorkspace.return_value.notificationCenter.return_value = center
        with patch("system_events.sys.platform", "darwin"), patch.dict(
                "sys.modules", {"AppKit": appkit, "Foundation": foundation}):
            monitor = SystemEvents(self.app)
        self.addCleanup(monitor.close)
        self.assertEqual(center.addObserverForName_object_queue_usingBlock_.call_count, 2)
        monitor.blocks[0](None)
        self.assertTrue(monitor.suspended)
        monitor.blocks[1](None)
        self.assertFalse(monitor.suspended)
        monitor.close()
        self.assertEqual(center.removeObserver_.call_count, 2)
        self.assertEqual(monitor.blocks, [])


if __name__ == "__main__":
    unittest.main()
