"""Explicit live capture/reopen check. Audio is discarded, never transcribed or saved."""
import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audio_capture import Recorder

parser = argparse.ArgumentParser()
parser.add_argument("--device", type=int)
args = parser.parse_args()
recorder = Recorder(max_seconds=5)
try:
    recorder.start(args.device)
    time.sleep(.4)
    assert recorder.healthy() and recorder.samples > 0, "Live microphone delivered no audio"
    samples = recorder.samples
    recorder.stream.abort()  # Simulate stream interruption, without disabling the OS device.
    assert not recorder.healthy()
    recorder.cancel()
    assert recorder.stream is None and not recorder.chunks and recorder.samples == 0
    recorder.start(args.device)
    time.sleep(.4)
    assert recorder.healthy() and recorder.samples > 0
    recorder.cancel()
    assert recorder.stream is None and not recorder.chunks
    print(f"Live microphone: {samples} samples received, stream interruption and reopen passed; no audio saved")
finally:
    recorder.cancel()
