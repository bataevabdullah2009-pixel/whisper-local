"""Small platform boundary; importing the macOS app never loads Win32 libraries."""
import sys

IS_MAC = sys.platform == "darwin"
if sys.platform == "win32":
    from windows_native import KeyboardHook, check_shortcut, focus_target, same_target, target_known, modifiers_down, no_activate, paste_shortcut, user32
    HOTKEY_NAME = "левый Alt"
    HOTKEY_SHORT = "Alt"
    SYSTEM_NAME = "Windows"

    def clipboard_sequence():
        return user32.GetClipboardSequenceNumber()

    def request_permissions():
        pass
elif IS_MAC:
    from macos_native import KeyboardHook, check_shortcut, focus_target, same_target, target_known, modifiers_down, no_activate, paste_shortcut, clipboard_sequence, request_permissions
    HOTKEY_NAME = "правый Option"
    HOTKEY_SHORT = "⌥ Option"
    SYSTEM_NAME = "macOS"
else:
    raise RuntimeError("This preview supports Windows and macOS.")
