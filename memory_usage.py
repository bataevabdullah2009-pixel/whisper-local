"""Local process RSS and Windows WDDM dedicated/shared GPU bytes. No network or content."""
from __future__ import annotations

import ctypes
import re
import sys

import psutil


def sum_gpu_instances(items, pids):
    total = 0
    for name, value, status in items:
        match = re.match(r"pid_(\d+)_", name)
        if match and int(match[1]) in pids:
            if status not in (0, 1):  # PDH_CSTATUS_VALID_DATA / NEW_DATA
                return None
            total += max(0, value)
    return total


class WindowsGPU:
    """PDH uses language-neutral counter names, including on Russian Windows."""
    def __init__(self):
        from ctypes import wintypes as w
        self.query = ctypes.c_void_p()
        self.counters = []
        self.pdh = ctypes.WinDLL("pdh")
        signatures = {
            "PdhOpenQueryW": [w.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)],
            "PdhAddEnglishCounterW": [ctypes.c_void_p, w.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)],
            "PdhCollectQueryData": [ctypes.c_void_p],
            "PdhCloseQuery": [ctypes.c_void_p],
            "PdhGetFormattedCounterArrayW": [ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD), ctypes.c_void_p],
        }
        for name, arguments in signatures.items():
            function = getattr(self.pdh, name)
            function.argtypes, function.restype = arguments, w.DWORD
        class ValueUnion(ctypes.Union):
            _fields_ = [("large", ctypes.c_longlong), ("double", ctypes.c_double), ("long", w.LONG), ("string", w.LPWSTR)]
        class Value(ctypes.Structure):
            _anonymous_ = ("value",)
            _fields_ = [("status", w.DWORD), ("value", ValueUnion)]
        class Item(ctypes.Structure):
            _fields_ = [("name", w.LPWSTR), ("value", Value)]
        self.Item = Item
        try:
            if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)):
                raise OSError("GPU counters unavailable")
            for kind in ("Dedicated Usage", "Shared Usage"):
                counter = ctypes.c_void_p()
                if self.pdh.PdhAddEnglishCounterW(self.query, rf"\GPU Process Memory(*)\{kind}", 0, ctypes.byref(counter)):
                    raise OSError("GPU counters unavailable")
                self.counters.append(counter)
            self.pdh.PdhCollectQueryData(self.query)
        except Exception:
            self.close()
            raise

    def _read(self, counter, pids):
        from ctypes import wintypes as w
        size, count = w.DWORD(), w.DWORD()
        status = self.pdh.PdhGetFormattedCounterArrayW(counter, 0x400, ctypes.byref(size), ctypes.byref(count), None)
        if status != 0x800007D2 or not size.value:  # PDH_MORE_DATA
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if self.pdh.PdhGetFormattedCounterArrayW(counter, 0x400, ctypes.byref(size), ctypes.byref(count), buffer):
            return None
        items = ctypes.cast(buffer, ctypes.POINTER(self.Item))
        return sum_gpu_instances([(items[i].name, items[i].value.large, items[i].value.status)
                                  for i in range(count.value)], pids)

    def sample(self, pids):
        if self.pdh.PdhCollectQueryData(self.query):
            return None, None
        return tuple(self._read(counter, pids) for counter in self.counters)

    def close(self):
        if self.query:
            self.pdh.PdhCloseQuery(self.query)
            self.query = ctypes.c_void_p()


class MemoryReader:
    def __init__(self):
        self.gpu = None
        if sys.platform == "win32":
            try:
                self.gpu = WindowsGPU()
            except OSError:
                pass

    def sample(self, pids):
        # Windows venv python.exe is a redirector; the actual model lives in its child.
        # Deduplicate descendants when the GUI and its worker are both supplied.
        processes = {}
        incomplete = False
        for pid in set(pids):
            try:
                process = psutil.Process(pid)
                processes[pid] = process
                for child in process.children(recursive=True):
                    processes[child.pid] = child
            except psutil.NoSuchProcess:
                continue
            except psutil.Error:
                incomplete = True
        rss = 0
        for process in processes.values():
            try:
                rss += process.memory_info().rss
            except psutil.NoSuchProcess:
                continue
            except psutil.Error:
                rss = None
                break
        if incomplete:
            rss = None
        dedicated, shared = self.gpu.sample(set(processes)) if self.gpu and not incomplete else (None, None)
        return {"rss_bytes": rss, "dedicated_bytes": dedicated, "shared_bytes": shared}

    def close(self):
        if self.gpu:
            self.gpu.close()
