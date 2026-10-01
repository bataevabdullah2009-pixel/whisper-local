"""Identify the focused edit control with UI Automation; never read its text.

COM signatures/vtable slots follow Microsoft's UIAutomationClient.h. HWND alone
cannot distinguish two fields rendered inside the same browser or Qt window.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import uuid

ole32 = C.WinDLL("ole32")
oleaut32 = C.WinDLL("oleaut32")


class GUID(C.Structure):
    _fields_ = [("data1", W.DWORD), ("data2", W.WORD), ("data3", W.WORD), ("data4", W.BYTE * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


CLSID_AUTOMATION = GUID.parse("ff48dba4-60ef-4201-aa87-54103eef594e")
IID_AUTOMATION = GUID.parse("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee")
ole32.CoInitializeEx.argtypes = [C.c_void_p, W.DWORD]
ole32.CoInitializeEx.restype = C.c_long
ole32.CoUninitialize.argtypes = []
ole32.CoCreateInstance.argtypes = [C.POINTER(GUID), C.c_void_p, W.DWORD,
                                  C.POINTER(GUID), C.POINTER(C.c_void_p)]
ole32.CoCreateInstance.restype = C.c_long
oleaut32.SafeArrayGetDim.argtypes = [C.c_void_p]
oleaut32.SafeArrayGetDim.restype = W.UINT
for name in ("SafeArrayGetLBound", "SafeArrayGetUBound"):
    function = getattr(oleaut32, name)
    function.argtypes = [C.c_void_p, W.UINT, C.POINTER(C.c_long)]
    function.restype = C.c_long
oleaut32.SafeArrayAccessData.argtypes = [C.c_void_p, C.POINTER(C.c_void_p)]
oleaut32.SafeArrayAccessData.restype = C.c_long
oleaut32.SafeArrayUnaccessData.argtypes = [C.c_void_p]
oleaut32.SafeArrayDestroy.argtypes = [C.c_void_p]


def _method(pointer, slot, *arguments):
    table = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(C.c_long, C.c_void_p, *arguments)(table[slot])


def _integer_property(element, slot):
    value = C.c_int()
    if _method(element, slot, C.POINTER(C.c_int))(element, C.byref(value)) < 0:
        raise OSError("UI Automation property unavailable")
    return value.value


def _editable(element):
    control_type = _integer_property(element, 21)
    if _integer_property(element, 35):  # CurrentIsPassword
        return False
    if control_type == 50004:  # UIA_EditControlTypeId
        return True
    if control_type != 50030:  # UIA_DocumentControlTypeId
        return False
    # A browser's whole document is not proof of an identified input field.
    # Only an editing provider (UIA_TextEditPatternId) can authorize document paste.
    pattern = C.c_void_p()
    try:
        return (_method(element, 16, C.c_int, C.POINTER(C.c_void_p))(
            element, 10032, C.byref(pattern)) >= 0 and bool(pattern))
    finally:
        if pattern:
            _method(pattern, 2)(pattern)


def _runtime_id(element):
    array = C.c_void_p()
    try:
        if _method(element, 4, C.POINTER(C.c_void_p))(element, C.byref(array)) < 0 or not array:
            return None
        if oleaut32.SafeArrayGetDim(array) != 1:
            return None
        lower, upper = C.c_long(), C.c_long()
        if (oleaut32.SafeArrayGetLBound(array, 1, C.byref(lower)) < 0
                or oleaut32.SafeArrayGetUBound(array, 1, C.byref(upper)) < 0):
            return None
        count = upper.value - lower.value + 1
        if not 0 < count <= 256:
            return None
        data = C.c_void_p()
        if oleaut32.SafeArrayAccessData(array, C.byref(data)) < 0:
            return None
        try:
            integers = C.cast(data, C.POINTER(C.c_int))
            return tuple(integers[i] for i in range(count))
        finally:
            oleaut32.SafeArrayUnaccessData(array)
    finally:
        if array:
            oleaut32.SafeArrayDestroy(array)


def focused_edit_key():
    """Return only process/runtime IDs. Unknown, protected or non-edit fields require copying."""
    initialized = ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    if initialized not in (0, 1, -2147417850):  # RPC_E_CHANGED_MODE: COM already initialized
        return None
    automation, element = C.c_void_p(), C.c_void_p()
    try:
        if ole32.CoCreateInstance(C.byref(CLSID_AUTOMATION), None, 1,
                                 C.byref(IID_AUTOMATION), C.byref(automation)) < 0:
            return None
        if (_method(automation, 8, C.POINTER(C.c_void_p))(
                automation, C.byref(element)) < 0 or not element):
            return None
        if not _editable(element):
            return None
        runtime_id = _runtime_id(element)
        return (_integer_property(element, 20), runtime_id) if runtime_id else None
    except (OSError, ValueError):
        return None
    finally:
        for pointer in (element, automation):
            if pointer:
                _method(pointer, 2)(pointer)  # IUnknown.Release
        if initialized in (0, 1):
            ole32.CoUninitialize()
