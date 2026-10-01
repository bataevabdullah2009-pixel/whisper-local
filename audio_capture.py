"""Microphone capture is opened only for an explicit dictation."""
from __future__ import annotations

import threading
import time
import numpy as np
import sounddevice as sd


class Recorder:
    def __init__(self, max_seconds=180):
        self.stream = None
        self.chunks = []
        self.lock = threading.Lock()
        self.level = 0.0
        self.peak = 0.0
        self.samples = 0
        self.limit = int(max_seconds * 16000)
        self.overflow = False
        self.device = None
        self.generation = 0
        self.accepting = False
        self.last_callback = 0.0
        self.callback_failed = False

    def start(self, device=None, name=None):
        self.cancel()
        self.samples = 0
        self.level = self.peak = 0.0
        self.overflow = False
        self.callback_failed = False
        if name:
            devices = sd.query_devices()
            if (not isinstance(device, int) or not 0 <= device < len(devices)
                    or devices[device]["name"] != name or devices[device]["max_input_channels"] <= 0):
                device = next((i for i, d in enumerate(devices)
                               if d["name"] == name and d["max_input_channels"] > 0), None)
                if device is None:
                    raise RuntimeError("Выбранный микрофон отключён. Выберите другой в настройках.")
        self.device = device
        with self.lock:
            self.accepting = True
            generation = self.generation
            self.last_callback = time.monotonic()
        try:
            self.stream = sd.InputStream(device=device, samplerate=16000, channels=1,
                dtype="float32", blocksize=320,
                callback=lambda *args: self._receive(*args, generation=generation))
            self.stream.start()
        except Exception:
            self.cancel()
            raise

    def _receive(self, data, frames, time_info, status, *, generation=None):
        with self.lock:
            if not self.accepting or (generation is not None and generation != self.generation):
                return
            mono = data[:, 0].copy()
            if not mono.size or not np.isfinite(mono).all() or status.input_underflow:
                self.callback_failed = True
                raise sd.CallbackAbort
            self.last_callback = time.monotonic()
            remaining = self.limit - self.samples
            if remaining > 0:
                self.chunks.append(mono[:remaining])
                self.samples += min(remaining, len(mono))
            self.overflow |= bool(status.input_overflow)
            rms = float(np.sqrt(np.mean(mono * mono)))
            self.level = min(1.0, rms * 15.0)
            self.peak = max(self.peak, float(np.max(np.abs(mono))))

    def healthy(self):
        try:
            return bool(self.stream is not None and self.stream.active
                        and not self.callback_failed
                        and time.monotonic() - self.last_callback <= 2.5)
        except Exception:
            return False

    def stop(self):
        return self._end(discard=False)

    def _end(self, *, discard):
        with self.lock:
            self.accepting = False
            self.generation += 1
        stream, self.stream = self.stream, None
        failed = False
        if stream is not None:
            try:
                stream.abort() if discard else stream.stop()
            except Exception:
                failed = True
            finally:
                try:
                    stream.close()
                except Exception:
                    failed = True
        with self.lock:
            result = (np.concatenate(self.chunks) if self.chunks and not discard and not failed
                      else np.empty(0, dtype=np.float32))
            self.chunks = []
            self.samples = 0
            self.level = self.peak = 0.0
        if failed and not discard:
            raise RuntimeError("Не удалось завершить запись: микрофон недоступен.")
        return result

    def cancel(self):
        self._end(discard=True)
