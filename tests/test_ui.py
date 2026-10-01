import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication

from app import Controller
from download_state import load_pending
from runtime import ROOT
from ui import SettingsWindow


class FirstRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def test_empty_install_opens_model_page_without_starting_asr(self):
        with patch("setup_service.SetupService.start"), patch("app.set_autostart"):
            controller = Controller(self.app, self.data, no_hook=True)
            self.assertEqual(controller.state, "setup")
            self.assertFalse(controller.ready)
            self.assertIsNone(controller.worker)
            self.assertEqual(controller.settings.pages.currentIndex(), 1)
            controller.shutdown()
            controller.settings.hide()

    def test_download_error_leaves_existing_model_configuration(self):
        with patch("setup_service.SetupService.start"), patch("app.set_autostart"):
            controller = Controller(self.app, self.data, no_hook=True)
            before = controller.config.copy()
            controller.download_model("base", "cpu")
            self.assertTrue(controller.settings.model_page.cancel.isVisible())
            controller.setup_failed("network interrupted")
            self.assertEqual(controller.config, before)
            self.assertTrue(controller.settings.model_page.download.isEnabled())
            controller.shutdown()
            controller.settings.hide()

    def test_completed_download_during_recording_does_not_interrupt_it(self):
        with patch("setup_service.SetupService.start"), patch("app.set_autostart"):
            controller = Controller(self.app, self.data, no_hook=True)
            controller.state = "recording"
            with patch("app.validate_model", return_value=Path("downloaded-model")):
                controller.activate_model("downloaded-model", "cpu", "base")
            self.assertEqual(controller.state, "recording")
            self.assertEqual(controller.config["model_path"], "")
            controller.state = "setup"
            controller.shutdown()
            controller.settings.hide()

    def test_interrupted_download_is_offered_after_restart_without_auto_download(self):
        with patch("setup_service.SetupService.start") as start, patch("app.set_autostart"):
            controller = Controller(self.app, self.data, no_hook=True)
            controller.download_model("base", "cpu")
            controller.cancel_download()
            controller.shutdown()
            controller.settings.hide()
            start.reset_mock()
            restarted = Controller(self.app, self.data, no_hook=True)
            page = restarted.settings.model_page
            self.assertEqual(page.model.currentData(), "base")
            self.assertEqual(page.device.currentData(), "cpu")
            self.assertEqual(page.download.text(), "Продолжить загрузку")
            self.assertFalse(page.pending_notice.isHidden())
            page.set_hardware({"recommended_model": "turbo"})
            self.assertEqual(page.model.currentData(), "base")
            self.assertFalse(any(call.args[0] == "download" for call in start.call_args_list))
            page.model.setCurrentIndex(page.model.findData("small"))
            self.assertEqual(page.download.text(), "Скачать и настроить")
            page.model.setCurrentIndex(page.model.findData("base"))
            self.assertEqual(page.download.text(), "Продолжить загрузку")
            restarted.download_model("base", "cpu")
            self.assertEqual(start.call_args.args[0], "download")
            restarted.shutdown()
            restarted.settings.hide()

    def test_state_write_failure_never_starts_download(self):
        with patch("setup_service.SetupService.start") as start, patch("app.set_autostart"):
            controller = Controller(self.app, self.data, no_hook=True)
            start.reset_mock()
            with patch("app.save_pending", side_effect=OSError("disk full")):
                controller.download_model("base", "cpu")
            start.assert_not_called()
            self.assertTrue(controller.settings.model_page.download.isEnabled())
            controller.shutdown()
            controller.settings.hide()

    def test_completed_download_intent_survives_busy_dictation_until_activation(self):
        with patch("setup_service.SetupService.start"), patch("app.set_autostart"):
            controller = Controller(self.app, self.data, no_hook=True)
            controller.download_model("base", "cpu")
            event = {"type": "downloaded", "path": "downloaded-model", "model_id": "base"}
            with patch("app.validate_model", return_value=Path("downloaded-model")), patch.object(controller, "restart_worker"):
                controller.state = "recording"
                controller.setup_event(event)
                self.assertIsNotNone(load_pending(self.data))
                controller.state = "setup"
                controller.setup_event(event)
            self.assertIsNone(load_pending(self.data))
            self.assertEqual(controller.config["model_id"], "base")
            controller.shutdown()
            controller.settings.hide()

    def test_completed_download_keeps_intent_when_activation_settings_cannot_be_saved(self):
        with patch("setup_service.SetupService.start"), patch("app.set_autostart"):
            controller = Controller(self.app, self.data, no_hook=True)
            controller.download_model("base", "cpu")
            before = controller.config.copy()
            with patch("app.validate_model", return_value=Path("downloaded-model")), patch.object(Path, "replace", side_effect=OSError("disk full")), patch.object(controller, "restart_worker") as restart:
                controller.setup_event({"type": "downloaded", "path": "downloaded-model", "model_id": "base"})
            restart.assert_not_called()
            self.assertIsNotNone(load_pending(self.data))
            self.assertEqual(controller.config, before)
            self.assertIn("настройки не сохранены", controller.settings.model_page.message.text())
            controller.shutdown()
            controller.settings.hide()

    def test_practice_does_not_redirect_pending_external_dictation(self):
        with patch("setup_service.SetupService.start"), patch("app.set_autostart"):
            controller = Controller(self.app, self.data, no_hook=True)
            controller.state = "processing"
            controller.target = (123, 456)
            controller.practice_recording()
            self.assertEqual(controller.target, (123, 456))
            controller.state = "setup"
            controller.shutdown()
            controller.settings.hide()

    def test_model_page_has_cancel_retry_and_practice_states(self):
        config = json.loads((ROOT / "config.example.json").read_text())
        window = SettingsWindow(config)
        page = window.model_page
        page.set_busy(True, "downloading")
        self.assertFalse(page.download.isEnabled())
        page.update_progress({"done": 50, "total": 100, "phase": "download"})
        self.assertEqual(page.progress.value(), 50)
        page.set_ready("model", "процессор")
        self.assertTrue(page.download.isEnabled())
        self.assertFalse(page.practice.isHidden())
        window.hide()

    def test_mac_first_setup_offers_metal_and_legacy_model_choice(self):
        config = json.loads((ROOT / "config.example.json").read_text())
        with patch("setup_ui.native.IS_MAC", True):
            window = SettingsWindow(config)
            page = window.model_page
            self.assertEqual(page.backend.currentData(), "whispercpp")
            self.assertGreaterEqual(page.device.findData("metal"), 0)
            page.backend.setCurrentIndex(page.backend.findData("faster-whisper"))
            self.assertEqual(page.device.findData("metal"), -1)
            self.assertIn("папку", page.local.text())
            window.hide()

    def test_existing_mac_ct2_folder_is_not_automatically_switched(self):
        config = json.loads((ROOT / "config.example.json").read_text())
        config["model_path"] = str(self.data)
        with patch("setup_ui.native.IS_MAC", True):
            window = SettingsWindow(config)
            self.assertEqual(window.model_page.backend.currentData(), "faster-whisper")
            self.assertEqual(config["model_path"], str(self.data))
            window.hide()

    def test_mac_metal_memory_and_file_precision_do_not_claim_zero_vram_or_runtime_int8(self):
        config = json.loads((ROOT / "config.example.json").read_text())
        with patch("ui.native.IS_MAC", True):
            window = SettingsWindow(config)
            window.set_engine("metal", "whispercpp", "float16")
            window.set_memory_usage({"rss_bytes": 100, "dedicated_bytes": None})
            self.assertIn("недоступен", window.vram_usage.text())
            self.assertFalse(window.precision.isEnabled())
            self.assertIn("float16", window.precision.currentText())
            window.hide()


if __name__ == "__main__":
    unittest.main()
