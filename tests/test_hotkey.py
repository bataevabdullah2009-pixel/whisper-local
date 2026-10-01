import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtWidgets import QApplication

from app import Controller, load_config
import platform_native as native
from dictation_hotkey import HotkeyState, normalize_config, parse_shortcut
from hotkey_registration import MacShortcutReservation


class Shortcuts(unittest.TestCase):
    def test_old_config_keeps_platform_default_and_hold(self):
        for is_mac, key in ((False, "alt_l"), (True, "alt_r")):
            config = {}
            normalize_config(config, is_mac)
            self.assertEqual(config, {"hotkey": "default", "dictation_mode": "hold"})
            self.assertEqual(parse_shortcut(config["hotkey"], is_mac).key, key)

    def test_invalid_saved_settings_fall_back_without_breaking_other_settings(self):
        config = {"hotkey": ["ctrl", "space"], "dictation_mode": "bad", "language": "ru"}
        normalize_config(config)
        self.assertEqual(config, {"hotkey": "default", "dictation_mode": "hold", "language": "ru"})

    def test_chord_is_canonical_and_case_insensitive(self):
        self.assertEqual(parse_shortcut("SHIFT+Ctrl+SPACE").value, "ctrl+shift+space")

    def test_unsafe_and_editing_shortcuts_are_rejected(self):
        for value in ("escape", "ctrl+escape", "a", "space", "shift+a", "ctrl+v", "ctrl+shift+v",
                      "alt+f4", "alt+space", "ctrl+space", "f12", "cmd+a", "ctrl+ctrl+f9", "ctrl+f99"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_shortcut(value)
        for value in ("cmd+v", "cmd+shift+v", "cmd+space", "cmd+alt+space", "cmd+h"):
            with self.subTest(mac=value), self.assertRaises(ValueError):
                parse_shortcut(value, True)

    def test_mac_command_chord_and_function_key_are_supported(self):
        self.assertEqual(parse_shortcut("cmd+shift+d", True).value, "shift+cmd+d")
        self.assertEqual(parse_shortcut("f9").key, "f9")


class KeyTransitions(unittest.TestCase):
    def state(self, value="ctrl+shift+space", mode="hold", mac=False):
        return HotkeyState(parse_shortcut(value, mac), mode=mode,
                           replay_default=value == "default" and not mac)

    def press_chord(self, state, now=0):
        state.process("ctrl_l", True, now)
        state.process("shift_r", True, now)
        return state.process("space", True, now)

    def test_hold_suppresses_primary_and_stops_on_release(self):
        state = self.state()
        self.assertEqual(self.press_chord(state), (True, "down", None))
        self.assertEqual(state.process("space", False, 1), (True, "up", None))
        self.assertFalse(state.held)

    def test_release_of_either_modifier_stops_hold_and_consumes_primary_release(self):
        for key in ("ctrl_l", "shift_r"):
            state = self.state()
            self.press_chord(state)
            self.assertEqual(state.process(key, False, 1), (False, "up", None))
            self.assertFalse(state.held)
            self.assertEqual(state.process("space", True, 1.1), (True, None, None))
            self.assertEqual(state.process("space", False, 1.2), (True, None, None))

    def test_autorepeat_does_not_toggle_or_restart_hold(self):
        for mode in ("hold", "toggle"):
            state = self.state(mode=mode)
            self.press_chord(state)
            for now in (.1, .5, 1):
                self.assertEqual(state.process("space", True, now), (True, None, None))
            state.process("space", False, 2)
            self.assertEqual(state.process("space", True, 3), (True, "down", None))

    def test_mismatched_modifiers_and_repeats_do_not_activate(self):
        state = self.state()
        state.process("ctrl_l", True, 0)
        self.assertEqual(state.process("space", True, 0), (False, None, None))
        state.process("shift_r", True, .1)
        self.assertEqual(state.process("space", True, .2), (False, None, None))
        state.process("space", False, .3)
        state.process("alt_r", True, .4)
        self.assertEqual(state.process("space", True, .5), (False, None, None))

    def test_short_hold_is_cancelled_and_toggle_tap_is_accepted(self):
        state = self.state()
        self.press_chord(state)
        self.assertEqual(state.process("space", False, .05), (True, "cancel", None))
        state = self.state(mode="toggle")
        self.press_chord(state)
        self.assertEqual(state.process("space", False, .05), (True, "up", None))

    def test_default_windows_tap_replays_alt_but_toggle_does_not(self):
        state = self.state("default")
        state.process("alt_l", True, 0)
        self.assertEqual(state.process("alt_l", False, .05), (True, "cancel", "tap"))
        state = self.state("default", "toggle")
        state.process("alt_l", True, 0)
        self.assertEqual(state.process("alt_l", False, .05), (True, "up", None))

    def test_default_alt_chords_are_replayed_and_not_dictated(self):
        state = self.state("default")
        state.process("alt_l", True, 0)
        self.assertEqual(state.process("other", True, .1), (True, "cancel", "down"))
        self.assertEqual(state.process("alt_l", False, .2), (False, None, None))
        state.process("ctrl_r", True, .3)
        self.assertEqual(state.process("alt_l", True, .4), (False, None, None))

    def test_alt_chords_still_work_after_focus_cancellation(self):
        state = self.state("default")
        state.process("alt_l", True, 0)
        state.canceled = True  # Controller cancelled while the user still holds Alt.
        self.assertEqual(state.process("other", True, 1), (True, "cancel", "down"))
        self.assertEqual(state.process("alt_l", False, 2), (False, None, None))

    def test_forwarded_alt_shortcut_keeps_normal_escape(self):
        state = self.state("default")
        state.process("alt_l", True, 0)
        state.process("other", True, .1)
        self.assertEqual(state.process("escape", True, .2), (False, None, None))
        self.assertEqual(state.process("escape", False, .3), (False, None, None))

    def test_mac_default_option_preserves_regular_shortcuts(self):
        state = self.state("default", mac=True)
        self.assertEqual(state.process("alt_r", True, 0), (False, "down", None))
        self.assertEqual(state.process("a", True, .1), (False, "cancel", None))
        self.assertEqual(state.process("alt_r", False, .2), (False, None, None))

    def test_escape_is_consumed_once_including_release_after_cancel(self):
        state = self.state(mode="toggle")
        self.press_chord(state)
        state.process("space", False, .1)
        self.assertEqual(state.process("escape", True, .2, escape_enabled=True), (True, "cancel", None))
        self.assertEqual(state.process("escape", True, .3), (True, None, None))
        self.assertEqual(state.process("escape", False, .4, enabled=False), (True, None, None))
        self.assertEqual(state.process("escape", True, .5), (False, None, None))

    def test_pause_blocks_activation_and_keyup_still_cleans_up(self):
        state = self.state()
        state.process("ctrl_l", True, 0)
        state.process("shift_l", True, 0)
        self.assertEqual(state.process("space", True, 0, enabled=False), (False, None, None))
        state.process("space", False, .1, enabled=False)
        self.assertEqual(state.process("space", True, .2), (True, "down", None))
        self.assertEqual(state.process("space", False, 1, enabled=False), (True, "up", None))


class DictationController(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        patch("setup_service.SetupService.start").start()
        patch("app.set_autostart").start()
        patch("app.SoundCues.play").start()
        patch("app.native.check_shortcut").start()
        patch("app.native.target_known", return_value=True).start()
        self.same_target = patch("app.native.same_target", return_value=True).start()
        self.target = patch("app.native.focus_target", return_value="field A").start()
        self.addCleanup(patch.stopall)
        self.c = Controller(self.app, self.data, background=True, no_hook=True)
        self.c.state, self.c.ready = "idle", True
        self.c.recorder.start = Mock()
        self.c.recorder.cancel = Mock()
        self.c.recorder.healthy = Mock(return_value=True)
        self.c.recorder.stop = Mock(return_value=np.zeros(8000, dtype=np.float32))
        self.c.submit_audio = Mock()
        def cleanup():
            self.c.shutdown()
            self.c.settings.hide()
            self.c.overlay.hide()
        self.addCleanup(cleanup)

    def test_hold_uses_delay_and_finishes_after_release(self):
        self.c.on_hotkey("down")
        self.c.recorder.start.assert_not_called()
        self.assertTrue(self.c.hold_timer.isActive())
        self.c.hold_timer.timeout.emit()
        self.assertEqual(self.c.state, "recording")
        self.c.on_hotkey("up")
        self.c.recorder.stop.assert_called_once()
        self.c.submit_audio.assert_called_once()
        self.assertFalse(self.c.focus_timer.isActive())

    def test_toggle_records_on_press_and_second_press_finishes(self):
        self.c.config["dictation_mode"] = "toggle"
        self.c.on_hotkey("down")
        self.assertEqual(self.c.state, "recording")
        self.assertFalse(self.c.hold_timer.isActive())
        self.c.on_hotkey("up")
        self.c.recorder.stop.assert_not_called()
        self.c.on_hotkey("down")
        self.c.recorder.stop.assert_called_once()
        self.c.submit_audio.assert_called_once()
        self.assertEqual(self.c.target, "field A")

    def test_toggle_second_press_waits_for_unloaded_model_without_redirecting_target(self):
        self.c.config["dictation_mode"] = "toggle"
        self.c.on_hotkey("down")
        self.c.ready = False
        self.c.on_hotkey("down")
        self.assertEqual(self.c.state, "waiting_model")
        self.assertIsNotNone(self.c.pending_audio)
        self.target.return_value = "field B"
        self.c.on_hotkey("down")
        self.assertEqual(self.c.target, "field A")
        self.c.on_hotkey("cancel")
        self.assertIsNone(self.c.pending_audio)

    def test_escape_cancels_both_modes_without_submitting(self):
        for mode in ("hold", "toggle"):
            self.c.config["dictation_mode"] = mode
            self.c.target = "field A"
            self.c.begin_recording(manual=True)
            self.c.on_hotkey("cancel")
            self.assertEqual(self.c.state, "idle")
            self.assertFalse(self.c.focus_timer.isActive())
        self.c.submit_audio.assert_not_called()

    def test_focus_loss_cancels_both_modes_and_drops_audio(self):
        for mode in ("hold", "toggle"):
            self.c.config["dictation_mode"] = mode
            self.same_target.return_value = True
            self.c.target = "field A"
            self.c.begin_recording(manual=True)
            self.same_target.return_value = False
            self.c.check_recording_focus()
            self.assertEqual(self.c.state, "idle")
        self.assertEqual(self.c.recorder.cancel.call_count, 2)
        self.c.recorder.stop.assert_not_called()
        self.c.submit_audio.assert_not_called()

    def test_focus_loss_before_delay_or_at_finish_cannot_record_or_transcribe(self):
        self.c.on_hotkey("down")
        self.same_target.return_value = False
        self.c.hold_timer.timeout.emit()
        self.c.recorder.start.assert_not_called()
        self.same_target.return_value = True
        self.c.begin_recording(manual=True)
        self.same_target.return_value = False
        self.c.finish_recording()
        self.c.recorder.stop.assert_not_called()
        self.c.submit_audio.assert_not_called()

    def test_unknown_initial_field_keeps_manual_paste_path_available(self):
        with patch("app.native.target_known", return_value=False):
            self.same_target.return_value = False
            self.c.begin_recording(manual=True)
            self.c.check_recording_focus()
            self.assertEqual(self.c.state, "recording")

    def test_hotkeys_do_not_redirect_processing_or_pasting(self):
        for state in ("processing", "waiting_model", "canceling", "pasting"):
            self.c.state, self.c.target = state, "field A"
            self.target.return_value = "field B"
            self.c.on_hotkey("down")
            self.assertEqual(self.c.target, "field A")
            self.assertFalse(self.c.hold_timer.isActive())

    def test_saving_custom_settings_and_reset_survive_reload(self):
        self.assertTrue(self.c.change_dictation("ctrl+shift+space", "toggle"))
        restored = load_config(self.data)
        self.assertEqual((restored["hotkey"], restored["dictation_mode"]), ("ctrl+shift+space", "toggle"))
        self.assertIn("начала / остановки", self.c.tray.toolTip())
        self.assertIn("нажмите для начала", self.c.settings.practice_note.text())
        self.assertTrue(self.c.change_dictation("shift+f9", "hold"))
        self.assertEqual(self.c.settings.hotkey_modifiers.currentData(), "shift")
        self.assertTrue(self.c.change_dictation("default", "hold"))
        self.assertEqual(load_config(self.data)["hotkey"], "default")

    def test_busy_and_unsafe_rebind_leave_config_unchanged(self):
        before = self.c.config.copy()
        for state in ("recording", "waiting_model", "processing", "canceling", "pasting", "unloading"):
            self.c.state = state
            self.assertFalse(self.c.change_dictation("ctrl+shift+space", "toggle"))
        self.c.state = "idle"
        self.assertFalse(self.c.change_dictation("cmd+v" if native.IS_MAC else "ctrl+v", "toggle"))
        self.assertEqual(self.c.config, before)

    def test_conflict_restores_old_hook_and_does_not_persist_candidate(self):
        self.c.no_hook = False
        hooks = []
        def hook(*args):
            instance = Mock(error=OSError("shortcut occupied") if not hooks else None)
            instance.state.held = instance.state.owned_primary = False
            hooks.append(instance)
            return instance
        with patch("app.native.KeyboardHook", side_effect=hook):
            self.assertFalse(self.c.change_dictation("ctrl+shift+space", "toggle"))
            self.assertEqual(len(hooks), 2)
            hooks[0].stop.assert_called_once()
            self.assertTrue(hooks[1].enabled)
            self.assertEqual(self.c.config["hotkey"], "default")
            self.assertFalse((self.data / "config.json").exists())
            self.assertIn("Прежние настройки", self.c.settings.hotkey_note.text())

    def test_registration_probe_conflict_preserves_running_hook_without_replacing_it(self):
        self.c.no_hook = False
        hook = Mock(error=None)
        hook.state.owned_primary = False
        self.c.hook = hook
        with patch("app.native.check_shortcut", side_effect=OSError("occupied")), patch("app.native.KeyboardHook") as create:
            self.assertFalse(self.c.change_dictation("ctrl+shift+space", "toggle"))
        create.assert_not_called()
        hook.stop.assert_not_called()
        self.assertIs(self.c.hook, hook)

    def test_paused_rebind_stays_paused_and_old_generation_is_ignored(self):
        self.c.pause_action.setChecked(True)
        self.c.no_hook = False
        hook = Mock(error=None)
        hook.state.held = hook.state.owned_primary = False
        with patch("app.native.KeyboardHook", return_value=hook):
            self.assertTrue(self.c.change_dictation("ctrl+shift+space", "toggle"))
        self.assertFalse(hook.enabled)
        self.c.on_hotkey("down", self.c.hook_generation - 1)
        self.c.on_hotkey("down", self.c.hook_generation)
        self.c.recorder.start.assert_not_called()

    def test_ui_controls_apply_without_saving_unapplied_selection(self):
        window = self.c.settings
        window.hotkey_choice.setCurrentIndex(1)
        window.dictation_mode.setCurrentIndex(1)
        self.assertEqual(self.c.config["dictation_mode"], "hold")
        self.assertEqual(self.c.config["hotkey"], "default")
        window.apply_dictation.click()
        self.assertEqual(self.c.config["hotkey"], "ctrl+shift+space")
        self.assertEqual(self.c.config["dictation_mode"], "toggle")

    def test_mac_modifier_order_roundtrips_in_ui(self):
        with patch("ui.native.IS_MAC", True):
            from ui import SettingsWindow
            window = SettingsWindow({**self.c.config, "hotkey": "shift+cmd+d"})
            self.addCleanup(window.hide)
            self.assertEqual(window.hotkey_modifiers.currentData(), "shift+cmd")
            requested = []
            window.dictationRequested.connect(lambda *args: requested.append(args))
            window.apply_dictation.click()
            self.assertEqual(requested, [("shift+cmd+d", "hold")])


class MacBoundary(unittest.TestCase):
    def test_enabled_symbolic_system_shortcuts_are_rejected_before_carbon_probe(self):
        foundation = Mock()
        entry = {"enabled": True, "value": {"parameters": [32, 49, (1 << 17) | (1 << 18)]}}
        foundation.NSUserDefaults.standardUserDefaults.return_value.persistentDomainForName_.return_value = {
            "AppleSymbolicHotKeys": {"fixture": entry}}
        spec = importlib.util.spec_from_file_location("mac_conflict_test", Path(__file__).parents[1] / "macos_native.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"Foundation": foundation, **{name: Mock() for name in
                ("Quartz", "AppKit", "ApplicationServices", "CoreFoundation", "objc")}}):
            spec.loader.exec_module(module)
        with patch.object(module, "MacShortcutReservation") as reserve:
            with self.assertRaisesRegex(OSError, "системных"):
                module.check_shortcut(parse_shortcut("ctrl+shift+space", True))
            reserve.assert_not_called()
            entry["enabled"] = False
            module.check_shortcut(parse_shortcut("ctrl+shift+space", True))
            reserve.return_value.close.assert_called_once()

    @unittest.skipUnless(sys.platform == "darwin", "Carbon shortcut registration on macOS")
    def test_real_carbon_registration_collision_and_release(self):
        shortcut = parse_shortcut("ctrl+shift+f18", True)
        reservation = MacShortcutReservation(shortcut)
        try:
            with self.assertRaises(OSError):
                MacShortcutReservation(shortcut)
        finally:
            reservation.close()
        again = MacShortcutReservation(shortcut)
        again.close()

    def test_registration_collision_and_release(self):
        api = Mock()
        api.RegisterEventHotKey.return_value = -9878
        with patch("hotkey_registration.C.CDLL", return_value=api):
            with self.assertRaisesRegex(OSError, "занято"):
                MacShortcutReservation(parse_shortcut("shift+cmd+d", True))
            api.RegisterEventHotKey.return_value = 0
            def register(*args):
                args[-1]._obj.value = 123
                return 0
            api.RegisterEventHotKey.side_effect = register
            reservation = MacShortcutReservation(parse_shortcut("shift+cmd+d", True))
            self.assertEqual(api.RegisterEventHotKey.call_args.args[4], 1)
            reservation.close()
            reservation.close()
            api.UnregisterEventHotKey.assert_called_once()

    def test_event_tap_chords_esc_and_own_paste_events(self):
        cg = Mock(kCGEventKeyDown=10, kCGEventKeyUp=11, kCGEventFlagsChanged=12,
            kCGEventTapDisabledByTimeout=-2, kCGEventTapDisabledByUserInput=-1,
            kCGKeyboardEventKeycode="key", kCGEventSourceUserData="tag")
        cg.CGEventGetIntegerValueField.side_effect = lambda event, field: event.get(field, 0)
        cg.CGEventSourceKeyState.side_effect = lambda source, key: key in pressed
        spec = importlib.util.spec_from_file_location("mac_hotkey_test", Path(__file__).parents[1] / "macos_native.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {name: mock for name, mock in
                (("Quartz", cg), ("AppKit", Mock()), ("Foundation", Mock()), ("ApplicationServices", Mock()), ("CoreFoundation", Mock()), ("objc", Mock()))}):
            spec.loader.exec_module(module)
        events, pressed = [], set()
        hook = module.KeyboardHook(events.append, shortcut="ctrl+shift+space", mode="toggle")
        for key in (59, 56):
            pressed.add(key)
            self.assertIsNotNone(hook._event(None, 12, {"key": key}, None))
        self.assertIsNone(hook._event(None, 10, {"key": 49}, None))
        self.assertIsNone(hook._event(None, 10, {"key": 49}, None))
        self.assertIsNone(hook._event(None, 11, {"key": 49}, None))
        self.assertEqual(events, ["down", "up"])
        hook.escape_enabled = True
        self.assertIsNone(hook._event(None, 10, {"key": 53}, None))
        hook.escape_enabled = False
        self.assertIsNone(hook._event(None, 11, {"key": 53}, None))
        injected = {"key": 49, "tag": 0x574C4F43}
        self.assertIs(hook._event(None, 10, injected, None), injected)
        hook._event(None, -2, {}, None)
        self.assertFalse(hook.state.held)
        self.assertEqual(events, ["down", "up", "cancel", "cancel"])


@unittest.skipUnless(sys.platform == "win32", "Win32 shortcut boundary")
class WindowsBoundary(unittest.TestCase):
    def test_real_registered_conflict_does_not_install_hook(self):
        import windows_native as native
        if not native.user32.RegisterHotKey(None, 0x574D, 6, 0x81):  # Ctrl+Shift+F18
            self.skipTest("The fixture shortcut is occupied on this desktop")
        try:
            with patch.object(native.user32, "SetWindowsHookExW") as install:
                with self.assertRaises(OSError):
                    native.check_shortcut(parse_shortcut("ctrl+shift+f18"))
                install.assert_not_called()
        finally:
            native.user32.UnregisterHotKey(None, 0x574D)

    def test_injected_keys_bypass_hook_and_alt_tab_preserves_extended_flag(self):
        import ctypes as C
        import windows_native as native
        emitted = []
        hook = native.KeyboardHook(emitted.append)
        with patch.object(native.user32, "SetWindowsHookExW", return_value=1), patch.object(
                native.user32, "GetMessageW", return_value=0), patch.object(native.user32, "GetAsyncKeyState", return_value=0), patch.object(
                native.user32, "UnhookWindowsHookEx"), patch.object(native.user32, "CallNextHookEx", return_value=0), patch.object(native, "send_keys") as replay:
            hook.run()
            def event(key, down, flags=0):
                value = native.KBDLLHOOKSTRUCT(vkCode=key, flags=flags)
                return hook.callback(0, 0x100 if down else 0x101, C.addressof(value))
            event(0xA4, True, 0x10)
            self.assertEqual(emitted, [])
            self.assertEqual(event(0xA4, True), 1)
            self.assertEqual(event(0x09, True, 1), 1)
            replay.assert_called_once_with([(0xA4, False, False), (0x09, False, True)])
            self.assertEqual(event(0xA4, False), 0)
            self.assertEqual(emitted, ["down", "cancel"])


if __name__ == "__main__":
    unittest.main()
