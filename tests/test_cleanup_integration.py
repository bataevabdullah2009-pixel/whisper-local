import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from app import Controller, load_config
from cleanup_ui import CleanupPage
from text_cleanup import TextCleanup


class CleanupSettingsAndResults(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)

    def controller(self):
        with patch("setup_service.SetupService.start"), patch("app.set_autostart"):
            controller = Controller(self.app, self.data, background=True, no_hook=True)
        def cleanup():
            controller.shutdown()
            controller.settings.hide()
            controller.overlay.hide()
        self.addCleanup(cleanup)
        controller.ready = True
        controller.target = (123, 456)
        controller.paste_manager.paste = Mock()
        return controller

    def result(self, controller, text, identifier="current"):
        controller.state, controller.request_id = "processing", identifier
        controller.handle_worker_event({"type": "result", "id": identifier, "text": text, "seconds": .1})

    def enable(self, controller):
        page = controller.settings.cleanup_page
        with patch("app.set_autostart"):
            page.enabled.setChecked(True)
            page.fillers.setChecked(True)
        return page

    def test_controls_preview_and_saved_options_keep_text_ephemeral(self):
        controller = self.controller()
        page = controller.settings.cleanup_page
        self.assertFalse(page.enabled.isChecked())
        self.assertFalse(page.spacing.isEnabled())
        self.assertFalse(page.fillers.isEnabled())
        page.test_input.setPlainText("эээ,  private preview  ,text")
        self.assertEqual(page.test_output.toPlainText(), "эээ,  private preview  ,text")
        self.enable(controller)
        self.assertEqual(page.test_output.toPlainText(), "private preview, text")
        with patch("app.set_autostart"):
            page.enabled.setChecked(False)
        self.assertFalse(page.fillers.isEnabled())
        self.assertTrue(page.fillers.isChecked())
        stored = (self.data / "config.json").read_text(encoding="utf-8")
        self.assertNotIn("private preview", stored)
        config = load_config(self.data)
        self.assertFalse(config["cleanup_enabled"])
        self.assertTrue(config["cleanup_fillers"])
        reloaded = CleanupPage(config)
        self.addCleanup(reloaded.hide)
        self.assertEqual(reloaded.test_input.toPlainText(), "")
        self.assertEqual(reloaded.test_output.toPlainText(), "")

    def test_combined_preview_refreshes_after_dictionary_changes(self):
        controller = self.controller()
        page = self.enable(controller)
        page.test_input.setPlainText("эээ, опен  ай , эм")
        dictionary = controller.settings.dictionary_page
        with patch("app.set_autostart"):
            dictionary.source.setText("опен ай")
            dictionary.replacement.setText("Open  AI,inc")
            dictionary.save.click()
        self.assertEqual(page.test_output.toPlainText(), "Open  AI,inc")
        with patch("app.set_autostart"):
            dictionary.enabled.setChecked(False)
        self.assertEqual(page.test_output.toPlainText(), "опен ай")

    def test_both_backends_share_preview_paste_copy_and_retry_without_reprocessing(self):
        controller = self.controller()
        page = self.enable(controller)
        dictionary = controller.settings.dictionary_page
        with patch("app.set_autostart"):
            dictionary.source.setText("опен ай")
            dictionary.replacement.setText("Open  AI,inc")
            dictionary.save.click()
        raw = "эээ, опен  ай  ,private transcript"
        page.test_input.setPlainText(raw)
        expected = "Open  AI,inc, private transcript"
        self.assertEqual(page.test_output.toPlainText(), expected)
        for backend in ("faster-whisper", "whispercpp"):
            controller.active_backend = backend
            controller.paste_manager.paste.reset_mock()
            with self.assertLogs("WhisperLocal", level="INFO") as logs:
                self.result(controller, raw, backend)
            controller.paste_manager.paste.assert_called_once_with(expected, controller.target)
            self.assertEqual(controller.last_text, expected)
            with patch.object(controller.cleanup, "apply") as cleanup, patch.object(controller.dictionary, "apply") as replace:
                with patch.object(QApplication.clipboard(), "setText") as clipboard:
                    controller.copy_last()
                clipboard.assert_called_once_with(expected)
                controller.state = "idle"
                with patch("app.native.focus_target", return_value=controller.target):
                    controller.retry_paste()
                cleanup.assert_not_called()
                replace.assert_not_called()
            self.assertEqual(controller.paste_manager.paste.call_args.args[0], expected)
            self.assertNotIn("private transcript", " ".join(logs.output))
            self.assertNotIn("Open", " ".join(logs.output))
            self.assertNotIn("private transcript", (self.data / "config.json").read_text(encoding="utf-8"))

    def test_changes_apply_to_next_result_without_restart_or_rewriting_previous_result(self):
        controller = self.controller()
        controller.last_text = "previous text"
        controller.state, controller.request_id = "processing", "current"
        with patch.object(controller, "restart_worker") as restart:
            page = self.enable(controller)
        restart.assert_not_called()
        self.assertEqual(controller.last_text, "previous text")
        controller.handle_worker_event({"type": "result", "id": "current", "text": "эм, привет  ,мир"})
        controller.paste_manager.paste.assert_called_once_with("привет, мир", controller.target)
        controller.paste_manager.paste.reset_mock()
        with patch("app.set_autostart"):
            page.enabled.setChecked(False)
        self.result(controller, "эм, привет  ,мир", "next")
        controller.paste_manager.paste.assert_called_once_with("эм, привет  ,мир", controller.target)

    def test_cancelled_stale_empty_and_error_results_skip_cleanup_and_dictionary(self):
        controller = self.controller()
        controller.cleanup, controller.dictionary = Mock(), Mock()
        controller.state, controller.request_id = "idle", None
        controller.handle_worker_event({"type": "result", "id": "cancelled", "text": "private"})
        controller.state, controller.request_id = "processing", "current"
        controller.handle_worker_event({"type": "result", "id": "stale", "text": "private"})
        self.assertEqual(controller.request_id, "current")
        self.result(controller, "  ")
        controller.state, controller.request_id = "processing", "error"
        with self.assertLogs("WhisperLocal", level="ERROR"):
            controller.handle_worker_event({"type": "error", "id": "error", "error": "synthetic failure"})
        controller.cleanup.apply.assert_not_called()
        controller.dictionary.apply.assert_not_called()
        controller.paste_manager.paste.assert_not_called()

    def test_filler_only_result_does_not_paste_empty_or_replace_previous_result(self):
        controller = self.controller()
        self.enable(controller)
        controller.last_text = "previous text"
        with patch.object(controller.dictionary, "apply") as dictionary:
            self.result(controller, "эм, эээ...")
        dictionary.assert_not_called()
        controller.paste_manager.paste.assert_not_called()
        self.assertEqual(controller.last_text, "previous text")
        self.assertEqual(controller.state, "idle")
        self.assertIsNone(controller.request_id)
        self.assertEqual(controller.overlay.mode, "empty")

    def test_old_and_invalid_stored_config_does_not_enable_cleanup(self):
        (self.data / "config.json").write_text('{"cleanup_enabled": "yes", "cleanup_fillers": 1}', encoding="utf-8")
        config = load_config(self.data)
        self.assertFalse(config["cleanup_enabled"])
        self.assertFalse(config["cleanup_fillers"])
        self.assertEqual(TextCleanup(config).apply("эм, текст  ,пример"), "эм, текст  ,пример")


if __name__ == "__main__":
    unittest.main()
