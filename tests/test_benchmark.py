import unittest
from scripts.benchmark_int8 import distance, normalize


class QualityMetrics(unittest.TestCase):
    def test_wer_counts_substitutions_insertions_deletions(self):
        self.assertEqual(distance("one two three".split(), "one four extra three".split()), 2)
        self.assertEqual(distance(["one"], []), 1)
        self.assertEqual(distance([], ["one"]), 1)

    def test_case_punctuation_and_yo_do_not_count_as_errors(self):
        self.assertEqual(normalize("Ёж, снова здесь!"), normalize("еж снова здесь"))
