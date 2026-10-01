# Mac Metal / whisper.cpp

## Behavior

New Mac model setup defaults to **Mac · Metal / CPU · whisper.cpp**. An explicit Download
button downloads a GGML model from a checksum/revision-pinned catalogue; imported GGML
FP16 and Q8_0 files are also supported. The separate faster-whisper choice retains existing
CTranslate2 folders and their CPU/INT8 behavior. Models are never converted, replaced or
downloaded just by opening settings or updating the application.

Auto and GPU/Metal try a local GPU context and an actual encoder/decoder warmup. Readiness
reports the backend of the initialized context; device enumeration/build flags alone do not
prove acceleration. Metal initialization/kernel failure closes the failed context and retries
the same local model on CPU. A context that already runs successfully on CPU reports CPU
fallback explicitly. CPU selection disables the GPU. Native FP16/Q8 precision comes from the
model file, so the CTranslate2 runtime INT8 control is disabled for this backend.

The worker uses the same bounded PCM pipe protocol, recording capsule and sounds. It applies
the existing gain and Silero VAD settings before whisper.cpp beam decoding, with no previous
text context, translation or realtime printing. Native logs are suppressed; decoded text is
returned through the existing in-memory result channel. Recognition has no network transport;
RPC/dynamic backend loading are disabled in the native build and Python networking is blocked.

Esc, idle unload, Free memory, sleep, timeout and shutdown terminate the entire worker, including
native kernels. Cold recording starts while loading; pending PCM is discarded on cancellation.
The Mac memory page reports application/process RSS. Metal GPU allocations cannot currently be
measured separately, so it shows that limit rather than zero. On Apple Silicon CPU/GPU share
physical memory; RSS is not a measurement of all GPU allocations or unique physical memory.

## Build and model pins

- [whisper.cpp v1.9.4](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.4), commit
  `927cfce34f31707e17f2bff35c349632fb9e2c3a`, MIT license.
- [GGML models](https://huggingface.co/ggerganov/whisper.cpp/tree/5359861c739e955e79d9a303bcbc70fb988958b1),
  revision `5359861c739e955e79d9a303bcbc70fb988958b1`; base, small and large-v3-turbo sizes and
  SHA-256 checksums are in `whispercpp_catalog.json`.
- Build on the target Mac architecture, macOS 14+ deployment target, portable CPU instructions,
  Apple Accelerate, Metal with embedded shader sources, static whisper/GGML dependencies. The
  bridge, source version record and MIT notice are bundled with the helper; no Homebrew or compiler
  is needed on the user's Mac. Development needs Xcode command line tools, CMake and Git.
- A small accessor is appended to a generated copy of the pinned whisper.cpp translation unit
  to inspect its otherwise opaque initialized state backends. Upstream checkout files remain
  unchanged. The ctypes application ABI is fixed and checked before model creation.

```bash
python scripts/build_whispercpp.py
python -m unittest discover -s tests -v
python scripts/build_assets.py
python -m PyInstaller --noconfirm WhisperLocal.spec
# Explicitly downloads/verifies the public GGML base test model if --model is omitted:
python scripts/smoke_whispercpp.py --worker "dist/Whisper Local.app/Contents/MacOS/WhisperWorker"
# Physical supported Mac; a CPU fallback must fail this check:
python scripts/smoke_whispercpp.py --model /path/ggml-base.bin --require-metal
```

The smoke check uses existing `build/jfk.wav`, the checksum-pinned public fixture from the
reliability checks. It measures CPU and Auto separately, requires speech and empty silence,
checks cancellation/reload, next speech, idle/manual unload and cold recording. Reports contain
backend and timings, never decoded text. It does not open a microphone or perform native paste.
CI runs it on the bundled helper for both Mac architectures. If a hosted runner exposes no Metal
GPU, the report says `unavailable_on_runner` and checks CPU fallback; that is not Metal execution
evidence and does not support a speedup claim.

## Physical Mac gates

Code/unit tests, native compilation, package launch and fixture recognition are distinct from
microphone/permissions/paste validation. Keep the PR draft until the required physical gates have
evidence. No physical Mac was available in the Windows development environment.

| Environment | Required evidence |
| --- | --- |
| Apple Silicon, supported GPU | `--require-metal` succeeds; compare CPU/Metal on the same local model and representative Russian dictation; record chip, OS, backend, load/warm timings, RAM and WER/CER without attaching private audio/text |
| Intel Mac, supported integrated/discrete Metal GPU | Repeat native GPU warmup and dictation; also force CPU and verify explicit fallback on unavailable/failed GPU |
| Both architectures | Microphone, Accessibility/Input Monitoring permissions; Option hold/release; TextEdit/browser/editor/terminal paste; no lost leading words after idle/Free memory; Esc during cold load/recognition; actual sleep/wake and unplug/reconnect |
| Both architectures, offline | Disconnect network after explicit setup; recording, recognition, cancellation, unload and reload continue without network or files containing microphone audio |

CI fixture timings describe its runner and a short public clip. They do not establish performance
or quality on users' physical Macs. A larger natural Russian corpus and the existing release
gates remain necessary before a general speed/quality claim.
