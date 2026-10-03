"""Offline comparison on developer fixtures. Write metrics only, never decoded text/PCM.

Corpus JSON: [{"audio": "relative.wav", "reference": "known fixture words", "language": "ru"}]
Supply purpose-made/public fixtures; this script never opens a microphone or downloads data.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
from queue import Queue, Empty
import re
import statistics
import subprocess
import sys
import threading
import time
import unicodedata
import wave

import numpy as np
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from memory_usage import MemoryReader
from runtime import worker_command


def normalize(text):
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).lower().replace("ё", "е")))


def distance(reference, hypothesis):
    previous = list(range(len(hypothesis) + 1))
    for i, left in enumerate(reference, 1):
        current = [i]
        for j, right in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def read_audio(path):
    with wave.open(str(path)) as wav:
        if (wav.getframerate(), wav.getsampwidth(), wav.getnchannels()) != (16000, 2, 1):
            raise ValueError("Fixtures must be mono PCM16 WAV at 16 kHz")
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype("<f4") / 32768
    return pcm.tobytes(), len(pcm) / 16000


class Probe(threading.Thread):
    def __init__(self, pid):
        super().__init__(daemon=True)
        self.pid, self.samples, self.done = pid, [], threading.Event()

    def run(self):
        reader = MemoryReader()
        try:
            while not self.done.is_set():
                self.samples.append((time.monotonic(), reader.sample([self.pid])))
                self.done.wait(.05)
        finally:
            reader.close()

    def summary(self, begin, end, aggregate=max):
        result = {}
        for name in ("rss_bytes", "dedicated_bytes", "shared_bytes"):
            values = [item[name] for at, item in self.samples if begin <= at <= end and item[name] is not None]
            result[name] = aggregate(values) if values else None
        return result


class Worker:
    STARTUP_STAGES = {"dispatch", "import_vad", "validate_model", "create_context", "warmup_cpp", "warmup_vad"}

    def __init__(self, model, device, compute, helper=None, *, startup_progress=False):
        command = ([str(helper.resolve()), "asr"] if helper else worker_command("asr"))
        command += ["--model", str(model), "--device", device, "--compute-type", compute]
        self.startup_progress = startup_progress
        self.startup_stage = None
        if startup_progress:
            command += ["--startup-progress"]
        self.started = time.monotonic()
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        self.events = Queue()
        self.probe = Probe(self.process.pid)
        self.probe.start()
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        for line in self.process.stdout:
            self.events.put(line)
        self.events.put(None)

    def receive(self):
        deadline = time.monotonic() + 300
        while True:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Empty
                line = self.events.get(timeout=remaining)
            except Empty:
                stage = f" at startup stage {self.startup_stage}" if self.startup_stage else ""
                raise RuntimeError("Worker response timed out" + stage) from None
            if line is None:
                raise RuntimeError("Worker exited before responding")
            event = json.loads(line)
            if event.get("type") == "startup_progress" and self.startup_progress:
                stage = event.get("stage")
                if not isinstance(stage, str) or stage not in self.STARTUP_STAGES:
                    raise RuntimeError("Invalid worker startup diagnostic")
                self.startup_stage = stage
                # Never print arbitrary worker payloads, model paths, audio or text.
                report = {"startup_stage": stage, "seconds": round(time.monotonic() - self.started, 2)}
                if type(event.get("gpu")) is bool:
                    report["gpu"] = event["gpu"]
                print(json.dumps(report), flush=True)
                continue
            if event.get("type") in ("fatal", "error"):
                raise RuntimeError("Worker failed; no audio or transcript has been logged")
            if event.get("type") == "ready":
                self.startup_stage = None
            return event

    def transcribe(self, pcm, language, request_id):
        self.process.stdin.write(json.dumps({"type": "transcribe", "id": request_id,
            "language": language, "audio": base64.b64encode(pcm).decode("ascii")}) + "\n")
        self.process.stdin.flush()
        event = self.receive()
        if event.get("type") != "result" or event.get("id") != request_id:
            raise RuntimeError("Unexpected worker response")
        return event

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=15)
        self.probe.done.set()
        self.probe.join(timeout=15)
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()


def run(model, device, compute, fixtures, repeats, helper):
    worker = Worker(model, device, compute, helper)
    try:
        ready = worker.receive()
        if ready.get("type") != "ready" or ready.get("device") != device or ready.get("fallback"):
            raise RuntimeError("Benchmark must run on the requested device without fallback")
        cold_seconds = time.monotonic() - worker.started
        loaded = time.monotonic()
        time.sleep(1)
        idle = worker.probe.summary(loaded, time.monotonic(), statistics.median)
        loading_peak = worker.probe.summary(worker.started, loaded)
        timings, quality = [], []
        inference_start = time.monotonic()
        for repeat in range(repeats):
            round_seconds = 0
            for index, fixture in enumerate(fixtures):
                started = time.monotonic()
                event = worker.transcribe(fixture["pcm"], fixture["language"], f"{repeat}-{index}")
                round_seconds += time.monotonic() - started
                reference, hypothesis = normalize(fixture["reference"]), normalize(event["text"])
                if repeat == 0:
                    quality.append({"fixture": index, "language": fixture["language"],
                        "word_errors": distance(reference.split(), hypothesis.split()), "words": len(reference.split()),
                        "char_errors": distance(reference, hypothesis), "chars": len(reference)})
                # Decoded content stays in memory only; it never reaches the report or stdout.
                del event, hypothesis
            timings.append(round_seconds)
        peak = worker.probe.summary(inference_start, time.monotonic())
        started = time.monotonic()
        owned_children = psutil.Process(worker.process.pid).children(recursive=True)
        worker.process.kill()
        worker.process.wait(timeout=15)
        exit_seconds = time.monotonic() - started
        released = time.monotonic()
        time.sleep(2)
        if any(child.is_running() for child in owned_children):
            raise RuntimeError("Owned child process survived model release")
        after_exit = worker.probe.summary(released, time.monotonic(), min)
        duration = sum(fixture["seconds"] for fixture in fixtures)
        return {"requested_compute": compute, "actual_compute": ready["compute_type"], "device": device,
            "cold_load_seconds": round(cold_seconds, 3), "loading_peak": loading_peak, "idle": idle,
            "inference_peak": peak, "exit_seconds": round(exit_seconds, 3), "after_exit": after_exit,
            "round_seconds": [round(value, 3) for value in timings],
            "median_seconds": round(statistics.median(timings), 3),
            "real_time_factor": round(statistics.median(timings) / duration, 4), "quality": quality}
    finally:
        worker.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    fixtures = []
    for item in json.loads(args.corpus.read_text(encoding="utf-8-sig")):
        pcm, seconds = read_audio(args.corpus.parent / item["audio"])
        fixtures.append({**item, "pcm": pcm, "seconds": seconds})
    if not fixtures or not all(normalize(item["reference"]) for item in fixtures):
        parser.error("Corpus must contain speech and nonempty reference text")
    model_hash = hashlib.sha256()
    with (args.model / "model.bin").open("rb") as model:
        for chunk in iter(lambda: model.read(4 * 1024**2), b""):
            model_hash.update(chunk)
    import ctranslate2
    report = {"platform": platform.platform(), "python": platform.python_version(),
        "ctranslate2": ctranslate2.__version__, "cpu": platform.processor(),
        "model_sha256": model_hash.hexdigest(), "corpus_sha256": hashlib.sha256(args.corpus.read_bytes()).hexdigest(),
        "audio_seconds": round(sum(item["seconds"] for item in fixtures), 3), "fixtures": len(fixtures),
        "repeats": args.repeats, "modes": []}
    for compute in (("float16", "int8") if args.device == "cuda" else ("float32", "int8")):
        report["modes"].append(run(args.model, args.device, compute, fixtures, args.repeats, args.worker))
        print(f"Completed {args.device}/{compute}; metrics only", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Saved aggregate performance and error counts; no decoded text", flush=True)


if __name__ == "__main__":
    main()
