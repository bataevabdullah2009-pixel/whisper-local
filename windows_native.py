"""Windows integration. Only hotkey state is inspected; keyboard input is never logged."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import threading
import time
from windows_focus import focused_edit_key
from dictation_hotkey import HotkeyState, parse_shortcut, WINDOWS_KEYS, WINDOWS_MODIFIERS

user32 = C.WinDLL("user32", use_last_error=True)
kernel32 = C.WinDLL("kernel32", use_last_error=True)
LRESULT = C.c_ssize_t
ULONG_PTR = C.c_size_t
HOOKPROC = C.WINFUNCTYPE(LRESULT, C.c_int, W.WPARAM, W.LPARAM)


class KBDLLHOOKSTRUCT(C.Structure):
    _fields_ = [("vkCode", W.DWORD), ("scanCode", W.DWORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD),
                ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(C.Structure):
    _fields_ = [("uMsg", W.DWORD), ("wParamL", W.WORD), ("wParamH", W.WORD)]


class INPUTUNION(C.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", W.DWORD), ("u", INPUTUNION)]


class GUITHREADINFO(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD), ("hwndActive", W.HWND),
                ("hwndFocus", W.HWND), ("hwndCapture", W.HWND), ("hwndMenuOwner", W.HWND),
                ("hwndMoveSize", W.HWND), ("hwndCaret", W.HWND), ("rcCaret", W.RECT)]


user32.SetWindowsHookExW.argtypes = [C.c_int, HOOKPROC, W.HINSTANCE, W.DWORD]
user32.SetWindowsHookExW.restype = W.HHOOK
user32.CallNextHookEx.argtypes = [W.HHOOK, C.c_int, W.WPARAM, W.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [W.HHOOK]
user32.GetMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
user32.GetMessageW.restype = W.BOOL
user32.PostThreadMessageW.argtypes = [W.DWORD, W.UINT, W.WPARAM, W.LPARAM]
user32.PeekMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT]
user32.GetForegroundWindow.restype = W.HWND
user32.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
user32.GetWindowThreadProcessId.restype = W.DWORD
user32.GetGUIThreadInfo.argtypes = [W.DWORD, C.POINTER(GUITHREADINFO)]
user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
user32.GetWindowLongPtrW.argtypes = [W.HWND, C.c_int]
user32.GetWindowLongPtrW.restype = C.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [W.HWND, C.c_int, C.c_ssize_t]
user32.SetWindowLongPtrW.restype = C.c_ssize_t
user32.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]
user32.SendInput.argtypes = [W.UINT, C.POINTER(INPUT), C.c_int]
user32.SendInput.restype = W.UINT
user32.GetClipboardSequenceNumber.restype = W.DWORD
user32.RegisterHotKey.argtypes = [W.HWND, C.c_int, W.UINT, W.UINT]
user32.RegisterHotKey.restype = W.BOOL
user32.UnregisterHotKey.argtypes = [W.HWND, C.c_int]
kernel32.GetModuleHandleW.argtypes = [W.LPCWSTR]
kernel32.GetModuleHandleW.restype = W.HMODULE
kernel32.GetCurrentThreadId.restype = W.DWORD


def send_keys(events):
    """Each event is (virtual key, is_key_up, is_extended)."""
    inputs = (INPUT * len(events))()
    for item, (vk, up, extended) in zip(inputs, events):
        item.type = 1
        item.ki = KEYBDINPUT(vk, 0, (2 if up else 0) | (1 if extended else 0), 0, 0)
    count = user32.SendInput(len(inputs), inputs, C.sizeof(INPUT))
    if count != len(inputs):
        raise OSError("Windows не разрешила ввод. Скопируйте текст из окна Whisper Local.")


def paste_shortcut():
    send_keys([(0x11, False, False), (0x56, False, False),
               (0x56, True, False), (0x11, True, False)])


def modifiers_down():
    return any(user32.GetAsyncKeyState(vk) & 0x8000
               for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))


def focus_target():
    hwnd = user32.GetForegroundWindow()
    thread_id = user32.GetWindowThreadProcessId(hwnd, None) if hwnd else 0
    info = GUITHREADINFO(cbSize=C.sizeof(GUITHREADINFO))
    user32.GetGUIThreadInfo(thread_id, C.byref(info))
    return int(hwnd or 0), int(info.hwndFocus or 0), focused_edit_key()


def same_target(target):
    if not target_known(target):
        return False
    current = focus_target()
    return current == target


def target_known(target):
    return bool(target and len(target) == 3 and target[0] and target[1] and target[2] is not None)


def check_shortcut(shortcut):
    if shortcut.default:
        return
    masks = {"alt": 1, "ctrl": 2, "shift": 4}
    if not user32.RegisterHotKey(None, 0x574C, 0x4000 | sum(masks[m] for m in shortcut.modifiers),
                                WINDOWS_KEYS[shortcut.key]):
        code = C.get_last_error()
        raise OSError(f"Windows не разрешила сочетание (код {code}). Оно может быть занято. Выберите другое.")
    user32.UnregisterHotKey(None, 0x574C)


def no_activate(hwnd):
    style = user32.GetWindowLongPtrW(hwnd, -20)
    # WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW. Qt already supplies the layered window.
    user32.SetWindowLongPtrW(hwnd, -20, style | 0x08000000 | 0x80)
    user32.SetWindowPos(hwnd, W.HWND(-1), 0, 0, 0, 0, 0x01 | 0x02 | 0x10)


class KeyboardHook(threading.Thread):
    def __init__(self, emit, threshold=.18, shortcut="default", mode="hold"):
        super().__init__(name="WhisperLocal-Hotkey", daemon=True)
        self.emit = emit
        self.state = HotkeyState(parse_shortcut(shortcut), threshold, mode,
                                 replay_default=shortcut == "default")
        self.hook = None
        self.thread_id = 0
        self.ready = threading.Event()
        self.error = None
        self.enabled = True
        self.escape_enabled = False
        self.stop_requested = threading.Event()

    def run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        msg = W.MSG()
        # Create the message queue before announcing readiness or accepting WM_QUIT.
        user32.PeekMessageW(C.byref(msg), None, 0, 0, 0)
        self.state.pressed_modifiers = {name for vk, name in WINDOWS_MODIFIERS.items()
                                       if user32.GetAsyncKeyState(vk) & 0x8000}
        primary = 0xA4 if self.state.shortcut.default else WINDOWS_KEYS[self.state.shortcut.key]
        self.state.primary_down = bool(user32.GetAsyncKeyState(primary) & 0x8000)
        names = {vk: name for name, vk in WINDOWS_KEYS.items()} | WINDOWS_MODIFIERS | {0x1B: "escape"}

        @HOOKPROC
        def callback(code, wparam, lparam):
            if code < 0:
                return user32.CallNextHookEx(self.hook, code, wparam, lparam)
            key = C.cast(lparam, C.POINTER(KBDLLHOOKSTRUCT)).contents
            if key.flags & 0x10:  # Own injected chords and third-party automation bypass dictation.
                return user32.CallNextHookEx(self.hook, code, wparam, lparam)
            down = wparam in (0x100, 0x104)
            try:
                suppress, event, replay = self.state.process(
                    names.get(key.vkCode, "other"), down, time.monotonic(),
                    enabled=self.enabled, escape_enabled=self.escape_enabled)
                if replay:
                    send_keys([(0xA4, False, False)] + ([(0xA4, True, False)] if replay == "tap"
                              else [(key.vkCode, False, bool(key.flags & 1))]))
                if event:
                    self.emit(event)
                if suppress:
                    return 1
            except Exception:
                # Fail open: never break keyboard input because the app failed.
                self.emit("hook_error")
            return user32.CallNextHookEx(self.hook, code, wparam, lparam)

        self.callback = callback
        self.hook = user32.SetWindowsHookExW(13, callback, kernel32.GetModuleHandleW(None), 0)
        if not self.hook:
            self.error = C.WinError(C.get_last_error())
            self.ready.set()
            return
        self.ready.set()
        try:
            while not self.stop_requested.is_set() and user32.GetMessageW(C.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(C.byref(msg))
                user32.DispatchMessageW(C.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(self.hook)

    def stop(self):
        self.stop_requested.set()
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x12, 0, 0)
        self.join(timeout=2)
