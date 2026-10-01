import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from download_state import load_pending, save_pending, clear_pending


class PendingDownload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_restart_retains_pinned_selection_without_network(self):
        expected = save_pending(self.root, "base", "faster-whisper", "cpu")
        with patch("model_manager.open_download", side_effect=AssertionError("network")):
            self.assertEqual(load_pending(self.root), expected)
        clear_pending(self.root)
        self.assertIsNone(load_pending(self.root))

    def test_corrupt_or_stale_state_is_ignored(self):
        path = self.root / "download-state.json"
        for value in ("{", "null", "[]", '{"backend":"other"}'):
            path.write_text(value, encoding="utf-8")
            self.assertIsNone(load_pending(self.root))
        value = save_pending(self.root, "base", "faster-whisper", "cpu")
        value["revision"] = "outdated"
        path.write_text(json.dumps(value), encoding="utf-8")
        self.assertIsNone(load_pending(self.root))

    def test_failed_atomic_replace_keeps_previous_selection(self):
        expected = save_pending(self.root, "base", "faster-whisper", "cpu")
        with patch.object(Path, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                save_pending(self.root, "small", "faster-whisper", "auto")
        self.assertEqual(load_pending(self.root), expected)
