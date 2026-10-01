"""macOS integration. Dictation key state is observed without retaining typed text."""
from __future__ import annotations

import threading
import time
from dictation_hotkey import HotkeyState, parse_shortcut, MAC_KEYS, MAC_MODIFIERS
from hotkey_registration import MacShortcutReservation

import AppKit
import Foundation
import ApplicationServices as AX
import CoreFoundation as CF
import Quartz as CG
import objc


def clipboard_sequence():
    return AppKit.NSPasteboard.generalPasteboard().changeCount()


def focus_target():
    application = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if application is None:
        return 0, None
    try:
        error, element = AX.AXUIElementCopyAttributeValue(
            AX.AXUIElementCreateSystemWide(), AX.kAXFocusedUIElementAttribute, None)
    except Exception:
        return int(application.processIdentifier()), None
    return int(application.processIdentifier()), element if error == 0 else None


def same_target(target):
    current = focus_target()
    # If accessibility cannot identify the original field, keep a manual copy.
    return bool(target and target[0] == current[0] and target[1] is not None
                and current[1] is not None and CF.CFEqual(target[1], current[1]))


def target_known(target):
    return bool(target and target[0] and target[1] is not None)


def check_shortcut(shortcut):
    if shortcut.default:
        return
    # Carbon registration and user defaults are inspected on the Qt/main thread.
    preferences = Foundation.NSUserDefaults.standardUserDefaults().persistentDomainForName_("com.apple.symbolichotkeys") or {}
    masks = {"shift": 1 << 17, "ctrl": 1 << 18, "alt": 1 << 19, "cmd": 1 << 20}
    wanted = sum(masks[m] for m in shortcut.modifiers)
    for entry in (preferences.get("AppleSymbolicHotKeys", {}) or {}).values():
        parameters = (entry.get("value", {}) or {}).get("parameters", ())
        if (entry.get("enabled") and len(parameters) == 3 and parameters[1] == MAC_KEYS[shortcut.key]
                and parameters[2] & sum(masks.values()) == wanted):
            raise OSError("Это сочетание включено в системных горячих клавишах macOS. Выберите другое.")
    reservation = MacShortcutReservation(shortcut)
    reservation.close()


def modifiers_down():
    flags = CG.CGEventSourceFlagsState(CG.kCGEventSourceStateCombinedSessionState)
    return bool(flags & (CG.kCGEventFlagMaskShift | CG.kCGEventFlagMaskControl |
                         CG.kCGEventFlagMaskAlternate | CG.kCGEventFlagMaskCommand))


def paste_shortcut():
    if not AX.AXIsProcessTrusted():
        raise OSError("Разрешите доступ в macOS → Конфиденциальность → Универсальный доступ.")
    for down in (True, False):
        event = CG.CGEventCreateKeyboardEvent(None, 9, down)  # ANSI V
        CG.CGEventSetFlags(event, CG.kCGEventFlagMaskCommand)
        CG.CGEventSetIntegerValueField(event, CG.kCGEventSourceUserData, 0x574C4F43)
        CG.CGEventPost(CG.kCGHIDEventTap, event)


def no_activate(hwnd):
    view = objc.objc_object(c_void_p=int(hwnd))
    window = view.window()
    window.setLevel_(AppKit.NSFloatingWindowLevel)
    window.setHidesOnDeactivate_(False)
    window.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
                                   AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)


def request_permissions():
    AX.AXIsProcessTrustedWithOptions({AX.kAXTrustedCheckOptionPrompt: True})
    if hasattr(CG, "CGRequestListenEventAccess"):
        CG.CGRequestListenEventAccess()


class KeyboardHook(threading.Thread):
    def __init__(self, emit, threshold=.18, shortcut="default", mode="hold"):
        super().__init__(name="WhisperLocal-Hotkey", daemon=True)
        self.emit, self.threshold = emit, threshold
        self.state = HotkeyState(parse_shortcut(shortcut, True), threshold, mode)
        self.ready = threading.Event()
        self.error = None
        self.enabled = True
        self.escape_enabled = False
        self.stop_requested = threading.Event()
        self.run_loop = None
        self.tap = None
        self.names = {code: key for key, code in MAC_KEYS.items()} | MAC_MODIFIERS | {53: "escape"}

    def run(self):
        try:
            if not AX.AXIsProcessTrusted():
                raise PermissionError("Для горячей клавиши разрешите Универсальный доступ в macOS.")
            self.state.pressed_modifiers = {key for code, key in MAC_MODIFIERS.items()
                if CG.CGEventSourceKeyState(CG.kCGEventSourceStateCombinedSessionState, code)}
            primary = 61 if self.state.shortcut.default else MAC_KEYS[self.state.shortcut.key]
            self.state.primary_down = bool(CG.CGEventSourceKeyState(CG.kCGEventSourceStateCombinedSessionState, primary))
            self.tap = CG.CGEventTapCreate(CG.kCGSessionEventTap, CG.kCGHeadInsertEventTap,
                CG.kCGEventTapOptionDefault, sum(1 << kind for kind in
                    (CG.kCGEventFlagsChanged, CG.kCGEventKeyDown, CG.kCGEventKeyUp)), self._event, None)
            if self.tap is None:
                raise PermissionError("Разрешите Мониторинг ввода и перезапустите приложение.")
            source = CF.CFMachPortCreateRunLoopSource(None, self.tap, 0)
            self.run_loop = CF.CFRunLoopGetCurrent()
            CF.CFRunLoopAddSource(self.run_loop, source, CF.kCFRunLoopCommonModes)
            CG.CGEventTapEnable(self.tap, True)
            self.ready.set()
            if not self.stop_requested.is_set():
                CF.CFRunLoopRun()
        except Exception as error:
            self.error = error
            self.ready.set()
        finally:
            if self.tap:
                CG.CGEventTapEnable(self.tap, False)
                CF.CFMachPortInvalidate(self.tap)

    def _event(self, proxy, kind, event, refcon):
        try:
            if kind in (CG.kCGEventTapDisabledByTimeout, CG.kCGEventTapDisabledByUserInput):
                self.state.held = False
                self.state.canceled = True
                self.state.owned_primary = False
                self.state.primary_down = False
                self.state.pressed_modifiers.clear()
                self.emit("cancel")
                CG.CGEventTapEnable(self.tap, True)
                return event
            key = CG.CGEventGetIntegerValueField(event, CG.kCGKeyboardEventKeycode)
            if CG.CGEventGetIntegerValueField(event, CG.kCGEventSourceUserData) == 0x574C4F43:
                return event
            if kind == CG.kCGEventFlagsChanged:
                down = bool(CG.CGEventSourceKeyState(CG.kCGEventSourceStateCombinedSessionState, key))
            else:
                down = kind == CG.kCGEventKeyDown
            suppress, notification, _ = self.state.process(self.names.get(key, "other"),
                down, time.monotonic(), enabled=self.enabled, escape_enabled=self.escape_enabled)
            if notification:
                self.emit(notification)
            if suppress:
                return None
        except Exception:
            self.emit("hook_error")
        return event  # Normal Option combinations continue to work.

    def stop(self):
        self.stop_requested.set()
        if self.run_loop:
            CF.CFRunLoopStop(self.run_loop)
        self.join(timeout=2)
