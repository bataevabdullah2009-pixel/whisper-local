import unittest

from text_cleanup import DEFAULTS, TextCleanup, normalize_config
from user_dictionary import UserDictionary


def cleaner(**options):
    return TextCleanup({"cleanup_enabled": True, **options})


class TextCleanupRules(unittest.TestCase):
    def test_missing_invalid_and_disabled_options_preserve_existing_behavior(self):
        config = {"cleanup_enabled": "true", "cleanup_spacing": 1, "cleanup_fillers": None}
        normalize_config(config)
        self.assertEqual(config, DEFAULTS)
        original = "  эээ,  привет ,мир!\r\n\t"
        for options in ({}, config, {"cleanup_enabled": False},
                        {"cleanup_enabled": True, "cleanup_spacing": False, "cleanup_fillers": False}):
            self.assertEqual(TextCleanup(options).apply(original), original)

    def test_spacing_cleans_prose_without_inventing_punctuation_or_case(self):
        self.assertEqual(cleaner().apply("Привет  ,мир!Как  дела ?"), "Привет, мир! Как дела?")
        self.assertEqual(cleaner().apply("один  два"), "один два")
        self.assertEqual(cleaner().apply("правда... да?!"), "правда... да?!")
        self.assertEqual(cleaner().apply("одно.другое:третье"), "одно.другое:третье")

    def test_paragraphs_crlf_indentation_and_tabs_are_retained(self):
        original = "два  слова\n\n  отступ  ,строка\r\n\tтаб\tмежду  словами"
        expected = "два слова\n\n  отступ, строка\r\n\tтаб\tмежду словами"
        self.assertEqual(cleaner().apply(original), expected)
        self.assertEqual(cleaner().apply("  \n\t\r\n"), "  \n\t\r\n")

    def test_urls_email_paths_versions_decimals_and_times_remain_verbatim(self):
        original = ("https://example.com/a?x=1,2 www.example.org/a,b admin@example.org "
                    r"C:\Users\um\file.txt /usr/local/um "
                    r"\\server\um\folder folder\um\file ../um/file folder/um/file "
                    "ftp://localhost/um "
                    "Node.js .NET v1.2.3 3,14 12:30 -5 5–7 C++ C#")
        self.assertEqual(cleaner(cleanup_fillers=True).apply(original), original)

    def test_quotes_code_and_code_like_lines_are_protected(self):
        samples = ['«эээ,  привет ,мир»', '"um,  hello ,world"', "‘эм  ,да’", "'uh  ,hi'",
                   "`um,  a,b`", "```python\nx =  1\num,um\n```", "~~~\num  ,uh\n~~~",
                   '  value =  "um"', 'items = [1,2]', 'print("um")', 'git commit -m "um"']
        for text in samples:
            with self.subTest(text=text):
                self.assertEqual(cleaner(cleanup_fillers=True).apply(text), text)
        self.assertEqual(cleaner(cleanup_fillers=True).apply('эм, текст «эм  ,да»  ,хорошо'),
                         'текст «эм  ,да», хорошо')

    def test_hesitations_are_opt_in_and_match_whole_tokens(self):
        text = "эээ, эм, uh, um, ну, вот, как бы, да да"
        self.assertEqual(cleaner().apply(text), text)
        self.assertEqual(cleaner(cleanup_fillers=True).apply(text), "ну, вот, как бы, да да")
        self.assertEqual(cleaner(cleanup_fillers=True).apply("ЭЭЭ, ЭММ, UHH, UMM, привет"), "привет")
        for text in ("эмаль эму эм2 2эм эм_key", "uhuru summary um2 2um um_key", "эм-эм um-um", "um's um’s um–um"):
            self.assertEqual(cleaner(cleanup_fillers=True).apply(text), text)

    def test_filler_removal_repairs_only_local_delimiters(self):
        cases = {"эээ, привет": "привет", "Привет, эм, мир": "Привет, мир",
                 "Привет эм мир": "Привет мир", "Привет, эм": "Привет",
                 "привет эээ эм мир": "привет мир", "Привет эм...": "Привет...",
                 "эм\nтекст": "\nтекст", "Привет, эм\r\nмир": "Привет\r\nмир"}
        for original, expected in cases.items():
            with self.subTest(original=original):
                self.assertEqual(cleaner(cleanup_fillers=True).apply(original), expected)

    def test_filler_only_output_is_empty(self):
        for text in ("эээ", "Эм, uh...", "эээ, эм!", "  um,\r\nuh?  "):
            self.assertEqual(cleaner(cleanup_fillers=True).apply(text), "")
        self.assertEqual(cleaner(cleanup_fillers=True).apply("..."), "...")

    def test_spacing_and_filler_options_are_independent(self):
        self.assertEqual(cleaner(cleanup_spacing=False, cleanup_fillers=True).apply("эм, привет  , мир"),
                         "привет  , мир")

    def test_dictionary_matches_take_priority_and_replacement_is_exact(self):
        dictionary = UserDictionary([{"source": "эм", "replacement": "EM"},
                                     {"source": "опен ай", "replacement": "Open  AI,inc"}])
        text = "эээ, опен  ай  , эм"
        result = cleaner(cleanup_fillers=True).apply(text, dictionary.pattern)
        self.assertEqual(dictionary.apply(result), "Open  AI,inc, EM")
        disabled = UserDictionary(dictionary.rules, False)
        self.assertEqual(cleaner(cleanup_fillers=True).apply("эм", disabled.pattern), "")

    def test_removal_between_protected_matches_keeps_word_separation(self):
        dictionary = UserDictionary([{"source": "OpenAI", "replacement": "OpenAI"},
                                     {"source": "Microsoft", "replacement": "Microsoft"}])
        self.assertEqual(cleaner(cleanup_fillers=True).apply("OpenAI эм Microsoft", dictionary.pattern),
                         "OpenAI Microsoft")

    def test_cleaning_is_idempotent_and_never_keeps_transcript_on_the_instance(self):
        cleanup = cleaner(cleanup_fillers=True)
        for text in ("эм, привет  ,мир!", "Привет, эм", "эээ, эм...", "два  слова\n\n  отступ  !",
                     "умышленно «эм»", "https://example.com/um", "Привет, эм, uh, мир"):
            once = cleanup.apply(text)
            self.assertEqual(cleanup.apply(once), once)
        self.assertEqual(vars(cleanup), {"enabled": True, "spacing": True, "fillers": True})


if __name__ == "__main__":
    unittest.main()
