import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication

from app import Controller
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


if __name__ == "__main__":
    unittest.main()
