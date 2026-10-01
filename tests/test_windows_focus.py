import sys
import unittest
from unittest.mock import patch

if sys.platform == "win32":
    import windows_native as native


@unittest.skipUnless(sys.platform == "win32", "Windows UI Automation boundary")
class FieldIdentity(unittest.TestCase):
    def test_two_fields_with_same_hwnd_are_different_targets(self):
        first = (123, 456, (789, (42, 1)))
        second = (123, 456, (789, (42, 2)))
        with patch.object(native, "focus_target", return_value=second):
            self.assertFalse(native.same_target(first))

    def test_same_identified_field_is_accepted(self):
        target = (123, 456, (789, (42, 1)))
        with patch.object(native, "focus_target", return_value=target):
            self.assertTrue(native.same_target(target))

    def test_unavailable_accessibility_does_not_fall_back_to_window_only(self):
        target = (123, 456, None)
        with patch.object(native, "focus_target", return_value=target):
            self.assertFalse(native.same_target(target))
            self.assertFalse(native.same_target((123, 456)))
            self.assertFalse(native.same_target((123, 0, (789, (42, 1)))))

    def test_new_process_reusing_same_window_and_runtime_id_is_refused(self):
        target = (123, 456, (789, (42, 1)))
        with patch.object(native, "focus_target", return_value=(123, 456, (790, (42, 1)))):
            self.assertFalse(native.same_target(target))
