import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from app import Controller, load_config
from dictionary_ui import DictionaryPage
from user_dictionary import (MAX_RULES, MAX_TERM_LENGTH, UserDictionary,
                             normalize_config, validate_rule, validate_rules)


def rule(source, replacement):
    return {"source": source, "replacement": replacement}


class Replacements(unittest.TestCase):
    def test_names_companies_and_terms_preserve_requested_spelling(self):
        dictionary = UserDictionary([rule("алексей", "Алексей"), rule("опен ай", "OpenAI"),
            rule("кубер нетес", "Kubernetes")])
        self.assertEqual(dictionary.apply("АЛЕКСЕЙ работает с Опен Ай и кубер нетес."),
                         "Алексей работает с OpenAI и Kubernetes.")

    def test_word_boundaries_keep_other_words_identifiers_and_numbers(self):
        dictionary = UserDictionary([rule("ай", "AI"), rule("api", "API")])
        self.assertEqual(dictionary.apply("ай, айтишник, край; api api_key xapi api2 2api"),
                         "AI, айтишник, край; API api_key xapi api2 2api")

    def test_technical_punctuation_is_literal_and_replacement_is_not_regex(self):
        dictionary = UserDictionary([rule("c++", "C++"), rule("c#", "C#"), rule(".net", ".NET"),
            rule("node.js", "Node.js"), rule("путь", r"C:\new\1 $1")])
        self.assertEqual(dictionary.apply("c++, c#; .net node.js nodeXjs путь"),
                         "C++, C#; .NET Node.js nodeXjs C:\\new\\1 $1")

    def test_longest_phrase_wins_independently_of_rule_order(self):
        rules = [rule("альфа", "Альфа"), rule("альфа банк", "Альфа-Банк")]
        for entries in (rules, list(reversed(rules))):
            self.assertEqual(UserDictionary(entries).apply("альфа банк и альфа"), "Альфа-Банк и Альфа")

    def test_replacements_do_not_cascade_or_cycle(self):
        dictionary = UserDictionary([rule("a", "b"), rule("b", "a")])
        self.assertEqual(dictionary.apply("a b a"), "b a b")
        dictionary = UserDictionary([rule("a", "b"), rule("b", "c")])
        self.assertEqual(dictionary.apply("a b"), "b c")

    def test_phrase_accepts_horizontal_spacing_without_eating_paragraphs(self):
        dictionary = UserDictionary([rule("опен ай", "OpenAI")])
        self.assertEqual(dictionary.apply("опен  ай, опен\tай, опен\u00a0ай\nопен\nай"),
                         "OpenAI, OpenAI, OpenAI\nопен\nай")

    def test_disabled_and_empty_dictionary_return_original_text(self):
        original = "  Опен  Ай!\n\n"
        for dictionary in (UserDictionary([]), UserDictionary([rule("опен ай", "OpenAI")], False)):
            self.assertEqual(dictionary.apply(original), original)
            self.assertEqual(dictionary.apply(""), "")

    def test_no_match_does_not_clean_spacing_punctuation_or_fillers(self):
        text = "ну,  эээ... Текст!\n\n"
        self.assertEqual(UserDictionary([rule("имя", "Имя")]).apply(text), text)

    def test_exact_cyrillic_letters_do_not_conflate_yo_and_ye(self):
        self.assertEqual(UserDictionary([rule("семён", "Семён")]).apply("СЕМЁН семен"), "Семён семен")

    def test_validation_rejects_blank_controls_types_and_long_terms(self):
        for source, replacement in ((" ", "A"), ("A", ""), ("A\nB", "C"), ("A", "B\x00"),
                                    (None, "C"), ("A" * (MAX_TERM_LENGTH + 1), "C")):
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate_rule(source, replacement)

    def test_canonical_duplicates_are_rejected_and_errors_do_not_quote_terms(self):
        rules = [rule("  ОПЕН   АЙ ", "OpenAI"), rule("опен ай", "other")]
        with self.assertRaises(ValueError) as error:
            validate_rules(rules)
        self.assertNotIn("опен", str(error.exception).lower())
        self.assertEqual(validate_rule(" И\u0306ван ", " Иван "), rule("Йван", "Иван"))

    def test_rule_capacity_and_invalid_containers(self):
        with self.assertRaises(ValueError):
            validate_rules([rule(str(index), "term") for index in range(MAX_RULES + 1)])
        for value in (None, {}, [None]):
            with self.assertRaises(ValueError):
                validate_rules(value)

    def test_old_and_malformed_config_keep_valid_rules_without_logging_content(self):
        old = {}
        normalize_config(old)
        self.assertEqual(old, {"dictionary_enabled": True, "dictionary_rules": []})
        damaged = {"dictionary_enabled": "false", "dictionary_rules": [None, rule("", "A"),
            rule(" опен  ай ", "OpenAI"), rule("ОПЕН АЙ", "duplicate"), rule("термин", "Term")]}
        normalize_config(damaged)
        self.assertTrue(damaged["dictionary_enabled"])
        self.assertEqual(damaged["dictionary_rules"], [rule("опен ай", "OpenAI"), rule("термин", "Term")])
        for value in (None, {}, "private term"):
            config = {"dictionary_enabled": False, "dictionary_rules": value}
            normalize_config(config)
            self.assertEqual(config, {"dictionary_enabled": False, "dictionary_rules": []})


class SettingsAndInsertion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.config = load_config(self.data)
        self.page = DictionaryPage(self.config)
        self.addCleanup(self.page.hide)
        self.changed = Mock()
        self.page.changed.connect(self.changed)

    def add(self, source, replacement):
        self.page.new.click()
        self.page.source.setText(source)
        self.page.replacement.setText(replacement)
        self.page.save.click()

    def controller(self):
        with patch("setup_service.SetupService.start"), patch("app.set_autostart"):
            controller = Controller(self.app, self.data, background=True, no_hook=True)
        def cleanup():
            controller.shutdown()
            controller.settings.hide()
            controller.overlay.hide()
        self.addCleanup(cleanup)
        return controller

    def test_add_edit_delete_and_live_preview_use_saved_rules(self):
        self.page.test_input.setPlainText("опен ай и кубер нетес")
        self.add("опен ай", "OpenAI")
        self.add("кубер нетес", "Kubernetes")
        self.assertEqual(self.page.test_output.toPlainText(), "OpenAI и Kubernetes")
        self.page.table.selectRow(0)
        self.page.replacement.setText("Open AI")
        self.assertEqual(self.page.test_output.toPlainText(), "OpenAI и Kubernetes")
        self.page.save.click()
        self.assertEqual(self.page.test_output.toPlainText(), "Open AI и Kubernetes")
        self.page.delete.click()
        self.assertEqual(self.config["dictionary_rules"], [rule("кубер нетес", "Kubernetes")])
        self.assertEqual(self.page.test_output.toPlainText(), "опен ай и Kubernetes")
        self.assertFalse(self.page.delete.isEnabled())
        self.assertEqual(self.changed.call_count, 4)

    def test_blank_duplicate_and_edit_conflict_do_not_modify_saved_rules(self):
        self.add("опен ай", "OpenAI")
        self.add("термин", "Term")
        expected = [dict(item) for item in self.config["dictionary_rules"]]
        self.add("ОПЕН   АЙ", "duplicate")
        self.assertIn("уже есть", self.page.message.text())
        self.assertEqual(self.config["dictionary_rules"], expected)
        self.page.table.selectRow(1)
        self.page.source.setText("ОПЕН АЙ")
        self.page.save.click()
        self.assertEqual(self.config["dictionary_rules"], expected)
        self.page.source.clear()
        self.page.save.click()
        self.assertIn("Заполните", self.page.message.text())
        self.assertEqual(self.changed.call_count, 2)

    def test_disabled_dictionary_remains_editable_and_preview_is_original(self):
        self.add("опен ай", "OpenAI")
        self.page.enabled.setChecked(False)
        self.page.test_input.setPlainText("ОПЕН АЙ")
        self.assertEqual(self.page.test_output.toPlainText(), "ОПЕН АЙ")
        self.page.replacement.setText("Open AI")
        self.page.save.click()
        self.page.enabled.setChecked(True)
        self.assertEqual(self.page.test_output.toPlainText(), "Open AI")

    def test_last_rule_deletion_returns_to_empty_state(self):
        self.add("имя", "Имя")
        self.page.delete.click()
        self.assertEqual(self.page.table.rowCount(), 0)
        self.assertFalse(self.page.empty.isHidden())
        self.assertEqual(self.config["dictionary_rules"], [])

    def test_disabled_rules_persist_after_restart_but_preview_text_does_not(self):
        controller = self.controller()
        page = controller.settings.dictionary_page
        page.source.setText("опен ай")
        page.replacement.setText("OpenAI")
        with patch("app.set_autostart"):
            page.save.click()
            page.test_input.setPlainText("private preview text")
            page.enabled.setChecked(False)
        stored = (self.data / "config.json").read_text(encoding="utf-8")
        self.assertNotIn("private preview text", stored)
        reloaded = load_config(self.data)
        self.assertFalse(reloaded["dictionary_enabled"])
        self.assertEqual(reloaded["dictionary_rules"], [rule("опен ай", "OpenAI")])
        new_page = DictionaryPage(reloaded)
        self.addCleanup(new_page.hide)
        self.assertEqual(new_page.test_input.toPlainText(), "")
        self.assertEqual(UserDictionary(reloaded["dictionary_rules"], reloaded["dictionary_enabled"]).apply("опен ай"), "опен ай")

    def test_both_worker_backends_paste_and_copy_same_processed_text_without_logging_it(self):
        controller = self.controller()
        controller.config["dictionary_rules"] = [rule("опен ай", "OpenAI"), rule("OpenAI", "Next")]
        with patch("app.set_autostart"):
            controller.save_config()
        controller.target = (123, 456)
        controller.ready = True
        for backend in ("faster-whisper", "whispercpp"):
            controller.active_backend = backend
            controller.state, controller.request_id = "processing", backend
            controller.paste_manager.paste = Mock()
            with self.assertLogs("WhisperLocal", level="INFO") as logs:
                controller.handle_worker_event({"type": "result", "id": backend, "text": "опен ай private transcript", "seconds": .1})
            expected = "OpenAI private transcript"
            controller.paste_manager.paste.assert_called_once_with(expected, (123, 456))
            self.assertEqual(controller.last_text, expected)
            with patch.object(QApplication.clipboard(), "setText") as clipboard:
                controller.copy_last()
            clipboard.assert_called_once_with(expected)
            controller.state = "idle"
            with patch("app.native.focus_target", return_value=(123, 456)):
                controller.retry_paste()
            self.assertEqual(controller.paste_manager.paste.call_args.args[0], expected)
            self.assertNotIn("private transcript", " ".join(logs.output))
            self.assertNotIn("OpenAI", " ".join(logs.output))
            self.assertNotIn("private transcript", (self.data / "config.json").read_text(encoding="utf-8"))

    def test_live_disable_affects_next_result_without_model_restart(self):
        controller = self.controller()
        page = controller.settings.dictionary_page
        page.source.setText("опен ай")
        page.replacement.setText("OpenAI")
        with patch("app.set_autostart"), patch.object(controller, "restart_worker") as restart:
            page.save.click()
            page.enabled.setChecked(False)
        restart.assert_not_called()
        controller.state, controller.ready, controller.request_id = "processing", True, "current"
        controller.paste_manager.paste = Mock()
        controller.handle_worker_event({"type": "result", "id": "current", "text": "опен ай"})
        controller.paste_manager.paste.assert_called_once_with("опен ай", controller.target)

    def test_cancelled_and_empty_results_never_apply_replacements_or_paste(self):
        controller = self.controller()
        controller.dictionary = Mock()
        controller.paste_manager.paste = Mock()
        controller.state, controller.ready, controller.request_id = "idle", True, None
        controller.handle_worker_event({"type": "result", "id": "cancelled", "text": "private"})
        controller.state, controller.request_id = "processing", "current"
        controller.handle_worker_event({"type": "result", "id": "current", "text": "   "})
        controller.dictionary.apply.assert_not_called()
        controller.paste_manager.paste.assert_not_called()


if __name__ == "__main__":
    unittest.main()
