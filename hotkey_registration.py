"""Probe Carbon shortcuts on the main thread, then release the reservation."""
import ctypes as C


class MacHotkeyID(C.Structure):
    _fields_ = [("signature", C.c_uint32), ("id", C.c_uint32)]


class MacShortcutReservation:
    def __init__(self, shortcut):
        from dictation_hotkey import MAC_KEYS
        self.ref = C.c_void_p()
        self.api = C.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        self.api.GetApplicationEventTarget.restype = C.c_void_p
        self.api.RegisterEventHotKey.argtypes = [C.c_uint32, C.c_uint32, MacHotkeyID,
                                                 C.c_void_p, C.c_uint32, C.POINTER(C.c_void_p)]
        self.api.RegisterEventHotKey.restype = C.c_int32
        self.api.UnregisterEventHotKey.argtypes = [C.c_void_p]
        masks = {"cmd": 1 << 8, "shift": 1 << 9, "alt": 1 << 11, "ctrl": 1 << 12}
        status = self.api.RegisterEventHotKey(MAC_KEYS[shortcut.key],
            sum(masks[m] for m in shortcut.modifiers), MacHotkeyID(0x574C4F43, 1),
            self.api.GetApplicationEventTarget(), 1, C.byref(self.ref))  # kEventHotKeyExclusive
        if status:
            raise OSError(f"macOS не разрешила сочетание (код {status}). Оно может быть занято. Выберите другое.")

    def close(self):
        if self.ref.value:
            self.api.UnregisterEventHotKey(self.ref)
            self.ref = C.c_void_p()
