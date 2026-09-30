"""Microphone capture is opened only for an explicit dictation."""
from __future__ import annotations

import threading
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

    def start(self, device=None, name=None):
        self.cancel()
        self.samples = 0
        self.level = self.peak = 0.0
        self.overflow = False
        if name:
            devices = sd.query_devices()
            if device is None or device >= len(devices) or devices[device]["name"] != name:
                device = next((i for i, d in enumerate(devices)
                               if d["name"] == name and d["max_input_channels"] > 0), None)
                if device is None:
                    raise RuntimeError("Выбранный микрофон отключён. Выберите другой в настройках.")
        self.device = device
        self.stream = sd.InputStream(device=device, samplerate=16000, channels=1,
                                     dtype="float32", blocksize=320, callback=self._receive)
        try:
            self.stream.start()
        except Exception:
            self.stream.close()
            self.stream = None
            raise

    def _receive(self, data, frames, time_info, status):
        mono = data[:, 0].copy()
        with self.lock:
            remaining = self.limit - self.samples
            if remaining > 0:
                self.chunks.append(mono[:remaining])
                self.samples += min(remaining, len(mono))
            self.overflow |= bool(status.input_overflow)
        rms = float(np.sqrt(np.mean(mono * mono)))
        self.level = min(1.0, rms * 15.0)
        self.peak = max(self.peak, float(np.max(np.abs(mono))))

    def stop(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        with self.lock:
            result = np.concatenate(self.chunks) if self.chunks else np.empty(0, dtype=np.float32)
            self.chunks = []
        self.level = 0
        return result

    def cancel(self):
        self.stop()
        self.samples = 0
