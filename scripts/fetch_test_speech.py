"""Explicit CI download of a checksum-pinned public speech fixture, never user audio."""
import hashlib
from pathlib import Path
import ssl
import urllib.request

import certifi

URL = "https://raw.githubusercontent.com/ggml-org/whisper.cpp/v1.7.2/samples/jfk.wav"
SHA256 = "59dfb9a4acb36fe2a2affc14bacbee2920ff435cb13cc314a08c13f66ba7860e"
path = Path(__file__).resolve().parents[1] / "build/jfk.wav"
data = urllib.request.urlopen(URL, timeout=30, context=ssl.create_default_context(cafile=certifi.where())).read()
if hashlib.sha256(data).hexdigest() != SHA256:
    raise RuntimeError("Public speech fixture checksum mismatch")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(data)
print("Verified public JFK speech fixture")
