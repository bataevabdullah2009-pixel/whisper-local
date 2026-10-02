"""Offline playback of the cues used in Wispr Flow's public web demo.

Source attribution is in assets/sounds/SOURCES.md. No synthesis or networking.
"""
from __future__ import annotations

from array import array
import hashlib
import logging
from pathlib import Path
import wave
import sys

LOG = logging.getLogger("WhisperLocal")
ASSETS = Path(__file__).resolve().parent / "assets" / "sounds"
FILES = {
    'flow': {'start':'dictation-start.wav','insert':'dictation-stop.wav'},
    'console': {'start':'console-start.wav','insert':'console-insert.wav'},
    'air': {'start':'air-start.wav','insert':'air-insert.wav'},
}


class SoundCues:
    def __init__(self, data_dir: Path, config: dict):
        self.directory = data_dir / "cues"
        self.config = config
        self._cache = {}
        self.prepare()

    def prepare(self) -> None:
        # Prewarm outside recording, so the first key press needs no conversion.
        for kind in ('start','insert'):
            try:
                self.path_for(kind)
            except (OSError, ValueError, wave.Error) as error:
                LOG.warning("Sound cue unavailable: %s", error)

    def path_for(self, kind: str) -> Path:
        volume = max(0, min(100, int(self.config.get("sound_volume", 65))))
        style=self.config.get('sound_style','flow')
        if style not in FILES: style='flow'
        key = (style, kind, volume)
        if key in self._cache:
            return self._cache[key]
        source = ASSETS / FILES[style][kind]
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
        path = self.directory / f"flow-{digest}-{volume}.wav"
        if not path.is_file():
            self.directory.mkdir(parents=True, exist_ok=True)
            with wave.open(str(source), "rb") as original:
                parameters = original.getparams()
                if original.getsampwidth() != 2:
                    raise ValueError("Expected a 16-bit PCM cue")
                samples = array("h", original.readframes(original.getnframes()))
            gain = volume / 100
            frames = array("h", (round(sample * gain) for sample in samples))
            # Preserve the source pitch, duration and stereo image.
            with wave.open(str(path), "wb") as output:
                output.setparams(parameters)
                output.writeframes(frames.tobytes())
        self._cache[key] = path
        return path

    def play(self, kind: str, force: bool = False) -> None:
        if kind not in ('start','insert') or (not force and not self.config.get("sound_enabled", True)):
            return
        if int(self.config.get("sound_volume", 65)) <= 0:
            return
        try:
            path = str(self.path_for(kind))
            if sys.platform == "win32":
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            elif sys.platform == "darwin":
                from AppKit import NSSound
                if getattr(self, "_playing", None):
                    self._playing.stop()
                self._playing = NSSound.alloc().initWithContentsOfFile_byReference_(path, True)
                if self._playing:
                    self._playing.play()
        except (OSError, RuntimeError, ValueError, wave.Error) as error:
            LOG.warning("Sound cue unavailable: %s", error)
