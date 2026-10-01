# Model memory management

## Behavior

- The Memory page and `idle_unload_seconds` setting offer 1, 5 (default), 10 or 30 minutes,
  or no automatic unload. Idle time starts at model readiness and resets after recording,
  cancellation and paste. Recording, recognition, queued cold audio, paste and a held hotkey
  protect the model from both automatic and manual unloading.
- **Освободить память** terminates the recognizer process and releases its allocator and CUDA
  context. Model files, device/precision settings and the last result remain available.
  An unloaded model stays unloaded across sleep/wake. Unexpected exit remains an error.
- The next recording starts while the model loads. Releasing the hotkey before readiness
  holds bounded PCM in memory until recognition can start. Esc, timeout, sleep and shutdown
  discard this PCM. Readiness during recording never stops the microphone or pastes early.
- The displayed RSS includes the GUI and its descendants, deduplicated. Shared pages can be
  counted in more than one process; this is resident memory, not a unique/private allocation
  or the entire system's RAM use. Source Windows venv launchers have a separate Python child,
  which is included. Windows dedicated/shared GPU bytes come from PID-filtered WDDM PDH
  counters across adapters, using language-neutral names. Driver/counter failure is unknown.
  Sampling runs off the UI thread every two seconds; stale worker snapshots are discarded.
- The current Mac backend runs on CPU. RAM is measured on both architectures; separate VRAM
  is not used. Apple GPU/Metal and whisper.cpp belong to the next improvement.
- `compute_type: auto` retains the existing policy: NVIDIA FP16, supported CPU INT8, otherwise
  CPU FP32. Optional `int8` requests CUDA `int8_float16` or CPU `int8`; the actual type is shown
  in System settings. GPU load/warmup failure keeps CPU fallback. Unsupported CPU INT8 fails
  explicitly, so select Auto. Changing precision reloads only an active idle model.

## Windows measurements, 2026-10-01

Windows 11 build 26200, Ryzen 7 5700X (8 cores / 16 threads), RTX 5060 Ti (16,311 MiB),
Python 3.12.10, faster-whisper 1.2.1, CTranslate2 4.8.2. Each comparison uses the same local
model file; the model SHA-256 and raw metrics are in [CUDA](benchmarks/2026-10-01-cuda-int8.json)
and [CPU](benchmarks/2026-10-01-cpu-int8.json). Models and dependencies were already local;
recognition used the production offline worker and decoding/VAD/gain settings.

| Model / device / actual compute | Idle RSS, MiB | Idle dedicated VRAM, MiB | Sampled inference peak RSS / VRAM, MiB | Median seconds for corpus | Cold load, seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| large-v3-turbo / CUDA / float16 | 717.0 | 2,269.5 | 748.5 / 2,365.5 | 2.375 | 3.828 |
| large-v3-turbo / CUDA / int8_float16 | 754.2 | 1,213.5 | 786.3 / 1,341.5 | 2.500 | 4.891 |
| base / CPU / float32 | 392.8 | 0 | 489.0 / 0 | 6.469 | 1.422 |
| base / CPU / int8_float32 | 194.6 | 0 | 290.5 / 0 | 4.562 | 1.250 |

CUDA INT8 saved **46.5%** of idle dedicated VRAM (1,056 MiB) and **43.3%** at the sampled
inference peak. Shared GPU RAM was 76 MiB in both modes. Worker RSS increased by 5.2% and
median recognition time increased by 5.3%. This is a memory option, not a guaranteed speedup.
CPU base INT8 saved **50.5%** of idle RSS and reduced median recognition time by **29.5%**.
These are different models; CPU and GPU rows are not a backend comparison for one model.

The corpus contains 56.975 seconds: eight developer-written Russian phrases synthesized
offline by Microsoft Irina Desktop (78 reference words) and the checksum-pinned public JFK
sample already used by CI (22 words). GPU timing uses five passes, CPU three, each after
kernel/VAD warmup. FP baseline ran before INT8 in separate processes. Timings include pipe
transport and resource sampling. RSS/GPU peaks are samples about 50 ms apart, so short peaks
may be missed. First load is from a warm OS file cache, not a freshly booted computer.

Quality uses the first pass, word/character edit distance after lowercasing, punctuation
removal, whitespace normalization and Russian ё→е. Character counts include normalized spaces.

| Model / compute | Russian WER | Combined WER | Combined CER |
| --- | ---: | ---: | ---: |
| turbo FP16 | 0 / 78 = 0% | 0 / 100 = 0% | 0 / 614 = 0% |
| turbo INT8/FP16 | 0 / 78 = 0% | 0 / 100 = 0% | 0 / 614 = 0% |
| base FP32 | 6 / 78 = 7.69% | 6 / 100 = 6% | 8 / 614 = 1.30% |
| base INT8/FP32 | 7 / 78 = 8.97% | 7 / 100 = 7% | 9 / 614 = 1.47% |

Zero errors on the turbo fixture does not establish equal quality on natural dictation.
This small clean corpus excludes accents, noisy microphones, domain vocabulary and long
speech. CPU base had one additional word error with INT8. No decoded text or microphone
recording is saved or printed by the benchmark; reports contain only counts and metrics.

In the real Qt controller with turbo/CUDA, the loaded worker family used 716.4 MiB RSS,
2,269.5 MiB dedicated VRAM and 76 MiB shared GPU RAM. Clicking Free memory removed the
launcher and model child; all their memory counters returned to zero. A new recording with
the public speech fixture began immediately and finished loading/recognizing in 3.375 seconds.
Automatic idle unload and simulated sleep/wake also passed. This checks the lifecycle with
fixture PCM and a replaced recorder/paste callback; it is not a microphone or native paste test.

## Reproduce

```powershell
python -m unittest discover -s tests -v
# Windows only, generates developer fixtures offline; optionally includes existing build/jfk.wav:
./scripts/create_synthetic_corpus.ps1
python scripts/benchmark_int8.py --model LOCAL_MODEL --corpus build/int8-corpus/corpus.json --device cuda --repeats 5 --output build/cuda-int8.json
python scripts/benchmark_int8.py --model LOCAL_MODEL --corpus build/int8-corpus/corpus.json --device cpu --output build/cpu-int8.json
python scripts/smoke_memory.py --model LOCAL_MODEL --device cuda --audio build/jfk.wav
# A built helper can be supplied to either script with --worker PATH.
```

Benchmarking accepts existing mono PCM16/16 kHz WAV fixtures and a JSON corpus containing
`audio` (relative path), `reference` and `language`. Use public or purpose-made fixtures.
It never downloads a model, records a microphone or writes decoded text. Mac uses the same
benchmark script with `--device cpu` and a supplied fixture corpus; Windows SAPI generation
is not available there.

## Validation boundary

Automated tests cover real subprocess unload, cold recording, cancellation/timeout, busy
guards, setting persistence, GPU fallback, process-tree accounting and simulated sleep.
Desktop CI additionally builds Windows x64 and both Mac architectures and runs the memory
lifecycle with the actual bundled CPU helper and public speech. CI results belong on the PR;
the workflow definition alone does not prove a successful run.

Still required on physical Windows, Apple Silicon and Intel Macs: dictate through a full idle
timeout; speak immediately after Free memory; cancel during cold loading; check CPU/INT8
fallback and visible RAM/VRAM; sleep after unloading and during queued cold audio; confirm
no lost leading words, late paste or stuck hotkey. Real-Mac permission/microphone/paste,
physical microphone disconnection and actual sleep gates from PR #2 remain open. INT8 needs
a larger natural Russian speech corpus before claiming unchanged general recognition quality.
