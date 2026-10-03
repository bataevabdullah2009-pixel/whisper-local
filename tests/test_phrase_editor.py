import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from app import Controller, load_config
from editor_service import PhraseEditorService, editor_command
from phrase_editor import accept_edit, normalize_config, protect_text, validate_editor_model
from user_dictionary import UserDictionary


class EditorContracts(unittest.TestCase):
    def test_whole_phrase_corrects_grammar_punctuation_and_accidental_repetition(self):
        for source, candidate in (("я хочу хочу домой", "Я хочу домой."),
                                  ("привет привет", "Привет!"),
                                  ("я хочю чтобы ты пришол завтра", "Я хочу, чтобы ты пришёл завтра."),
                                  ("он сказал что завтра придет", "Он сказал, что завтра придёт.")):
            with self.subTest(source=source):
                self.assertEqual(accept_edit(source, candidate, []), candidate)

    def test_preserves_urls_numbers_quotes_code_newlines_and_dictionary_terms(self):
        dictionary = UserDictionary([{"source": "опен ай", "replacement": "Open  AI"}])
        raw = 'опен ай стоимость 1250,50 в 10:30\r\n  https://test.example/a "нет"\napi_key = 3'
        protected, spans = protect_text(raw, dictionary.pattern)
        self.assertNotIn("1250", protected)
        self.assertNotIn("https", protected)
        self.assertNotIn("api_key", protected)
        self.assertNotIn("опен ай", protected)
        self.assertEqual(accept_edit(protected, protected, spans), raw)
        self.assertIsNone(accept_edit(protected, protected.replace("ZXQ0QXZ", "OpenAI"), spans))
        self.assertIsNone(accept_edit(protected, protected + " ZXQ0QXZ", spans))

    def test_rejects_instruction_answers_new_facts_negation_loss_and_truncation(self):
        for source, candidate in (("не отправляй документ завтра", "Отправляй документ завтра."),
                                  ("скажи сколько будет два плюс два", "Четыре."),
                                  ("встреча завтра", "Встреча завтра в 20."),
                                  ("привет мир", "<think>ответ</think>Привет, мир!"),
                                  ("очень длинный текст который нельзя сокращать", "Текст."),
                                  ("сохрани предыдущую фразу", "Вот исправленный текст: сохрани предыдущую фразу.")):
            with self.subTest(source=source):
                self.assertIsNone(accept_edit(source, candidate, []))

    def test_model_cannot_add_paragraphs_or_change_spacing_around_line_breaks(self):
        source, spans = protect_text("первая строка  \r\n  вторая строка")
        candidate = source.replace("ZXQ0QXZ", " ZXQ0QXZ ")
        self.assertEqual(accept_edit(source, candidate, spans), "первая строка  \r\n  вторая строка")
        self.assertIsNone(accept_edit("первая строка вторая строка", "Первая строка\nвторая строка.", []))

    def test_old_configs_disable_editor_and_do_not_accept_incomplete_files(self):
        config = {"editor_enabled": "yes", "editor_model_path": 42}
        normalize_config(config)
        self.assertEqual(config, {"editor_enabled": False, "editor_model_path": ""})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.gguf"
            path.write_bytes(b"GGUF" + struct.pack("<I", 3))
            with self.assertRaises(ValueError):
                validate_editor_model(path)

    def test_command_has_no_network_cache_or_transcript_arguments(self):
        command = editor_command("/local/editor.gguf")
        self.assertIn("--no-display-prompt", command)
        self.assertIn("--log-verbosity", command)
        self.assertNotIn("--log-file", command)
        self.assertNotIn("--hf-repo", command)
        self.assertNotIn("--prompt-cache", command)
        self.assertIn("0", command[command.index("--gpu-layers") + 1:command.index("--gpu-layers") + 2])


class EditorControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        with patch("setup_service.SetupService.start"), patch("app.set_autostart"):
            self.controller = Controller(self.qt, self.data, background=True, no_hook=True)
        self.controller.ready = True
        self.controller.target = (123, 456)
        self.controller.paste_manager.paste = Mock()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.controller.shutdown()
        self.controller.settings.hide()
        self.controller.overlay.hide()

    def result(self, text):
        self.controller.config["editor_enabled"] = True
        self.controller.state, self.controller.request_id = "processing", "recognition"
        self.controller.handle_worker_event({"type": "result", "id": "recognition", "text": text})

    def test_both_asr_backends_edit_once_then_share_copy_retry_and_original(self):
        c = self.controller
        for backend in ("faster-whisper", "whispercpp"):
            with self.subTest(backend=backend), patch.object(c.editor, "edit") as edit:
                c.active_backend = backend
                c.paste_manager.paste.reset_mock()
                self.result("я хочу хочу домой")
                edit.assert_called_once()
                c.paste_manager.paste.assert_not_called()
                self.assertEqual(c.state, "processing")
                c.editor_finished(c.editing_id, "Я хочу домой.", True, "")
                c.paste_manager.paste.assert_called_once_with("Я хочу домой.", c.target)
                with patch.object(QApplication.clipboard(), "setText") as clipboard:
                    c.copy_original()
                clipboard.assert_called_once_with("я хочу хочу домой")
                c.state = "idle"
                with patch("app.native.focus_target", return_value=c.target):
                    c.retry_paste()
                self.assertEqual(edit.call_count, 1)
                self.assertEqual(c.paste_manager.paste.call_args.args[0], "Я хочу домой.")

    def test_cancel_and_late_completions_do_not_paste_or_replace_previous_result(self):
        c = self.controller
        c.last_text = c.last_original_text = "previous"
        with patch.object(c.editor, "edit"), patch.object(c, "_stop_worker") as stop_asr:
            self.result("private unfinished phrase")
            identifier = c.editing_id
            c.cancel()
            c.editor_finished(identifier, "Private unfinished phrase.", True, "")
        stop_asr.assert_not_called()
        self.assertEqual(c.state, "idle")
        self.assertEqual(c.last_text, "previous")
        self.assertEqual(c.last_original_text, "previous")
        c.paste_manager.paste.assert_not_called()
        self.assertEqual(c.editor_original, "")

    def test_unavailable_editor_falls_back_without_persistence_or_content_logs(self):
        c = self.controller
        with self.assertLogs("WhisperLocal", "INFO") as logs:
            self.result("private transcript phrase")
        c.paste_manager.paste.assert_called_once_with("private transcript phrase", c.target)
        with patch("app.set_autostart"):
            c.save_config()
        self.assertNotIn("private transcript phrase", (self.data / "config.json").read_text())
        self.assertNotIn("private transcript phrase", " ".join(logs.output))

    def test_dictionary_snapshot_has_priority_over_model_and_later_settings(self):
        c = self.controller
        c.dictionary = UserDictionary([{"source": "опен ай", "replacement": "Open  AI"}])
        with patch.object(c.editor, "edit"):
            self.result("опен ай работает работает")
            c.dictionary = UserDictionary([])
            c.editor_finished(c.editing_id, "опен ай работает.", True, "")
        self.assertEqual(c.last_text, "Open  AI работает.")

    def test_preview_is_explicit_and_editing_settings_drops_stale_preview(self):
        c = self.controller
        c.state = "setup"
        page = c.settings.cleanup_page
        with patch.object(c.editor_preview, "edit") as edit:
            page.test_input.setPlainText("я хочу хочу домой")
            edit.assert_not_called()
            c.preview_editor(page.test_input.toPlainText())
            edit.assert_called_once()
        c.editor_preview.original = "private preview"
        page.test_input.setPlainText("new input")
        self.assertEqual(c.editor_preview.original, "")

    def test_service_releases_buffers_and_rejects_oversized_output(self):
        service = PhraseEditorService()
        outputs = []
        service.finished.connect(lambda *event: outputs.append(event))
        service.identifier, service.original = "test", "private raw"
        service.complete(None, "fallback")
        self.assertEqual(outputs, [("test", "private raw", False, "fallback")])
        self.assertEqual(service.original, "")
        self.assertEqual(service.output, b"")


if __name__ == "__main__":
    unittest.main()
