"""Small ctypes ABI to the bundled library; no Python struct mirrors of whisper internals."""
import ctypes
import os
from pathlib import Path
import sys

import numpy as np
from runtime import ROOT


def bridge_path():
    name = "libwhisperlocal.dylib" if sys.platform == "darwin" else "whisperlocal.dll" if sys.platform == "win32" else "libwhisperlocal.so"
    path = ROOT / "native" / name if getattr(sys, "frozen", False) else ROOT / "build/native" / name
    if not path.is_file():
        raise RuntimeError("Локальный движок whisper.cpp отсутствует. Установите Mac-сборку или соберите native bridge.")
    return path


class WhisperCpp:
    def __init__(self, model, gpu, threads):
        self.library = ctypes.CDLL(str(bridge_path()))
        lib = self.library
        signatures = {
            "wl_abi_version": ([], ctypes.c_int), "wl_version": ([], ctypes.c_char_p),
            "wl_create": ([ctypes.c_char_p, ctypes.c_int, ctypes.c_int], ctypes.c_void_p),
            "wl_free": ([ctypes.c_void_p], None), "wl_uses_metal": ([ctypes.c_void_p], ctypes.c_int),
            "wl_ftype": ([ctypes.c_void_p], ctypes.c_int),
            "wl_transcribe": ([ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_char_p], ctypes.c_int),
            "wl_text": ([ctypes.c_void_p], ctypes.c_char_p), "wl_clear_text": ([ctypes.c_void_p], None),
        }
        for name, (args, result) in signatures.items():
            function = getattr(lib, name)
            function.argtypes, function.restype = args, result
        if lib.wl_abi_version() != 1:
            raise RuntimeError("Unsupported whisper.cpp bridge ABI")
        self.context = lib.wl_create(os.fsencode(model), int(gpu), threads)
        if not self.context:
            raise RuntimeError("whisper.cpp could not load the local model")
        self.metal = bool(lib.wl_uses_metal(self.context))
        self.compute_type = {0: "float32", 1: "float16", 7: "q8_0"}.get(lib.wl_ftype(self.context), "quantized")

    def transcribe(self, audio, language):
        samples = np.ascontiguousarray(audio, dtype=np.float32)
        try:
            code = self.library.wl_transcribe(self.context, samples.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                len(samples), (language or "auto").encode("ascii"))
            if code:
                raise RuntimeError(f"whisper.cpp inference failed ({code})")
            return self.library.wl_text(self.context).decode("utf-8").strip()
        finally:
            self.library.wl_clear_text(self.context)

    def close(self):
        if self.context:
            self.library.wl_free(self.context)
            self.context = None
