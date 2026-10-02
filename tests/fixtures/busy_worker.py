"""Test helper: real computation which deliberately never reads a cancellation command."""
import json
import sys


def emit(event):
    print(json.dumps(event), flush=True)


emit({"type": "ready", "device": "cpu", "load_seconds": 0})
for line in sys.stdin:
    request = json.loads(line)
    if request.get("type") == "quit":
        break
    if request.get("audio") == "hang":
        emit({"type": "busy"})
        while True:
            sum(range(10000))
    emit({"type": "result", "id": request["id"], "text": "fixture transcript", "seconds": 0})
