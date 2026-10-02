import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtWidgets import QApplication

from app import Controller, load_config
from memory_usage import MemoryReader, sum_gpu_instances
from test_reliability import wait_until


class ModelMemory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        command = [sys.executable, "-u", "-B", str(Path(__file__).parent / "fixtures/delayed_worker.py")]
        patch("setup_service.SetupService.start").start()
        patch("app.validate_model", return_value=self.data).start()
        patch("app.worker_command", return_value=command).start()
        patch("app.set_autostart").start()
        self.addCleanup(patch.stopall)
        self.controller = Controller(self.app, self.data, background=True, no_hook=True)
        self.addCleanup(self.cleanup_controller)
        wait_until(lambda: self.controller.ready)
        self.controller.recorder.start = Mock()
        self.controller.recorder.healthy = Mock(return_value=True)
        self.controller.recorder.stop = Mock(return_value=np.ones(8000, dtype=np.float32))
        self.controller.paste_manager.paste = Mock()

    def cleanup_controller(self):
        self.controller.shutdown()
        self.controller.settings.hide()
        self.controller.overlay.hide()

    def release(self):
        self.assertTrue(self.controller.release_memory())
        wait_until(lambda: self.controller.state == "unloaded")
        self.assertIsNone(self.controller.worker)
        self.assertFalse(self.controller.ready)

    def test_button_exits_process_keeps_model_path_and_last_text(self):
        c = self.controller
        c.last_text = "fixture"
        config = c.config.copy()
        old = c.worker
        self.release()
        self.assertEqual(c.config, config)
        self.assertEqual(c.last_text, "fixture")
        self.assertFalse(c.operation_timer.isActive())
        c.worker_finished(0, 0, old)
        self.assertEqual(c.state, "unloaded")

    def test_idle_timeout_and_disabled_option(self):
        c = self.controller
        c.config["idle_unload_seconds"] = 0
        c.idle_since = time.monotonic() - 1000
        c.check_idle()
        self.assertTrue(c.ready)
        c.config["idle_unload_seconds"] = 60
        c.check_idle()
        wait_until(lambda: c.state == "unloaded")

    def test_active_operations_and_held_hotkey_cannot_unload(self):
        c = self.controller
        c.idle_since = time.monotonic() - 1000
        for state in ("recording", "waiting_model", "processing", "pasting", "loading", "canceling"):
            c.state = state
            c.check_idle()
            self.assertFalse(c.release_memory())
            self.assertIsNotNone(c.worker)
        c.state = "idle"
        c.hold_timer.start(10000)
        self.assertFalse(c.release_memory())
        c.check_idle()
        self.assertTrue(c.ready)
        c.hold_timer.stop()

    def test_cold_recording_starts_immediately_and_released_pcm_waits_for_ready(self):
        c = self.controller
        self.release()
        c.begin_recording(manual=True)
        self.assertEqual(c.state, "recording")
        self.assertFalse(c.ready)
        c.recorder.start.assert_called_once()
        c.finish_recording()
        self.assertEqual(c.state, "waiting_model")
        self.assertIsNotNone(c.pending_audio)
        wait_until(lambda: c.state == "pasting")
        self.assertIsNone(c.pending_audio)
        c.paste_manager.paste.assert_called_once()

    def test_ready_during_cold_recording_does_not_end_it(self):
        c = self.controller
        self.release()
        c.begin_recording(manual=True)
        wait_until(lambda: c.ready)
        self.assertEqual(c.state, "recording")
        c.paste_manager.paste.assert_not_called()
        c.finish_recording()
        wait_until(lambda: c.state == "pasting")

    def test_cancel_during_cold_wait_discards_pcm_and_never_pastes(self):
        c = self.controller
        self.release()
        c.begin_recording(manual=True)
        c.finish_recording()
        c.cancel()
        self.assertIsNone(c.pending_audio)
        wait_until(lambda: c.ready)
        self.assertEqual(c.state, "idle")
        c.paste_manager.paste.assert_not_called()

    def test_cold_load_timeout_discards_pending_pcm(self):
        c = self.controller
        self.release()
        c.begin_recording(manual=True)
        c.finish_recording()
        c._timeout()
        wait_until(lambda: c.worker is None)
        self.assertEqual(c.state, "error")
        self.assertIsNone(c.pending_audio)
        c.paste_manager.paste.assert_not_called()

    def test_sleep_after_release_does_not_reload_model(self):
        c = self.controller
        self.release()
        c.system_events.suspend()
        c.system_events.resume()
        self.assertEqual(c.state, "unloaded")
        self.assertIsNone(c.worker)

    def test_sleep_while_pcm_waits_discards_and_next_model_is_ready(self):
        c = self.controller
        self.release()
        c.begin_recording(manual=True)
        c.finish_recording()
        c.system_events.suspend()
        self.assertIsNone(c.pending_audio)
        wait_until(lambda: c.worker is None)
        c.system_events.resume()
        wait_until(lambda: c.ready)
        c.paste_manager.paste.assert_not_called()

    def test_precision_setting_reloads_active_model_and_persists(self):
        c = self.controller
        old = c.worker
        c.settings.precision.setCurrentIndex(c.settings.precision.findData("int8"))
        wait_until(lambda: c.ready and c.worker is not old)
        self.assertEqual(load_config(self.data)["compute_type"], "int8")

    def test_idle_setting_persists_and_unloaded_precision_does_not_reload(self):
        c = self.controller
        c.settings.idle_unload.setCurrentIndex(c.settings.idle_unload.findData(0))
        self.assertEqual(load_config(self.data)["idle_unload_seconds"], 0)
        self.release()
        c.settings.precision.setCurrentIndex(c.settings.precision.findData("int8"))
        self.assertIsNone(c.worker)
        self.assertEqual(c.state, "unloaded")

    def test_busy_precision_change_is_reverted(self):
        c = self.controller
        c.state = "recording"
        c.settings.precision.setCurrentIndex(c.settings.precision.findData("int8"))
        self.assertEqual(c.config["compute_type"], "auto")
        self.assertEqual(c.settings.precision.currentData(), "auto")
        c.state = "idle"

    def test_paste_retry_after_release_keeps_model_unloaded(self):
        c = self.controller
        self.release()
        c.last_text = "fixture"
        c.retry_paste()
        self.assertEqual(c.state, "pasting")
        c._pasted(False, "fixture")
        self.assertEqual(c.state, "unloaded")
        self.assertIsNone(c.worker)

    def test_old_memory_snapshot_cannot_describe_new_worker(self):
        c = self.controller
        with patch.object(c.settings, "set_memory_usage") as display:
            c.memory_measured((12345,), {"rss_bytes": 999})
            display.assert_not_called()
            c.memory_measured(c.memory_pids(), {"rss_bytes": 999})
            display.assert_called_once()


class MemoryMeasurements(unittest.TestCase):
    def test_gpu_counts_only_owned_pids_and_all_adapters(self):
        self.assertEqual(sum_gpu_instances([("pid_12_luid_a", 10, 0), ("pid_12_luid_b", 20, 1),
            ("pid_123_luid_a", 99, 0), ("_Total", 999, 0)], {12}), 30)

    def test_unknown_counter_is_not_reported_as_zero(self):
        self.assertIsNone(sum_gpu_instances([("pid_12_luid_a", 10, 5)], {12}))

    def test_redirector_children_are_counted_once_in_ram_and_gpu(self):
        child = Mock(pid=20)
        child.children.return_value = []
        child.memory_info.return_value.rss = 200
        parent = Mock(pid=10)
        parent.children.return_value = [child]
        parent.memory_info.return_value.rss = 10
        with patch("memory_usage.sys.platform", "darwin"), patch("memory_usage.psutil.Process", side_effect=lambda pid: {10: parent, 20: child}[pid]):
            reader = MemoryReader()
            reader.gpu = Mock()
            reader.gpu.sample.return_value = (1000, 5)
            values = reader.sample([10, 20])
            self.assertEqual(values["rss_bytes"], 210)
            reader.gpu.sample.assert_called_once_with({10, 20})

    def test_real_rss_is_positive_and_gpu_reader_closes(self):
        reader = MemoryReader()
        try:
            self.assertGreater(reader.sample([os.getpid()])["rss_bytes"], 0)
        finally:
            reader.close()

    def test_invalid_memory_settings_use_safe_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            (data / "config.json").write_text(json.dumps({"compute_type": "bogus", "idle_unload_seconds": -2}))
            config = load_config(data)
            self.assertEqual((config["compute_type"], config["idle_unload_seconds"]), ("auto", 300))
