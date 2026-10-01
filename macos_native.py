"""macOS integration. Only the dictation modifier and cancellation are observed."""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time

import AppKit
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


@dataclass
class OptionState:
    held: bool = False
    canceled: bool = False
    started: float = 0.0


class KeyboardHook(threading.Thread):
    def __init__(self, emit, threshold=.18):
        super().__init__(name="WhisperLocal-Hotkey", daemon=True)
        self.emit, self.threshold = emit, threshold
        self.state = OptionState()
        self.ready = threading.Event()
        self.error = None
        self.enabled = True
        self.escape_enabled = False
        self.run_loop = None
        self.tap = None
        self.escape_down = False

    def run(self):
        try:
            if not AX.AXIsProcessTrusted():
                raise PermissionError("Для горячей клавиши разрешите Универсальный доступ в macOS.")
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
                self.emit("cancel")
                CG.CGEventTapEnable(self.tap, True)
                return event
            key = CG.CGEventGetIntegerValueField(event, CG.kCGKeyboardEventKeycode)
            if key == 61 and kind == CG.kCGEventFlagsChanged:  # right Option
                down = CG.CGEventSourceKeyState(CG.kCGEventSourceStateCombinedSessionState, 61)
                if down and self.enabled and not self.state.held:
                    flags = CG.CGEventGetFlags(event)
                    if not flags & (CG.kCGEventFlagMaskCommand | CG.kCGEventFlagMaskControl | CG.kCGEventFlagMaskShift):
                        self.state = OptionState(True, False, time.monotonic())
                        self.emit("down")
                elif not down and self.state.held:
                    self.state.held = False
                    if not self.state.canceled:
                        self.emit("up" if time.monotonic() - self.state.started >= self.threshold else "cancel")
            elif key == 53 and (self.escape_enabled or self.state.held or self.escape_down):
                if kind == CG.kCGEventKeyDown:
                    self.escape_down = True
                    self.state.canceled = True
                    self.emit("cancel")
                    return None
                if kind == CG.kCGEventKeyUp and self.escape_down:
                    self.escape_down = False
                    return None
            elif self.state.held and kind in (CG.kCGEventKeyDown, CG.kCGEventFlagsChanged):
                self.state.canceled = True
                self.emit("cancel")
        except Exception:
            self.emit("hook_error")
        return event  # Normal Option combinations continue to work.

    def stop(self):
        if self.run_loop:
            CF.CFRunLoopStop(self.run_loop)
        self.join(timeout=2)
