# Whisper Local · Preview

Local dictation for Windows 10/11 x64 and macOS 14+ (Apple Silicon and Intel).
The standalone applications bundle Python and the recognition engine. No account or API key is needed.
The current UI is in Russian.

## First launch

1. Install the Windows Setup.exe, or drag the application from the matching Mac DMG to Applications.
2. Open **Модель** (Model). Choose the recommended model and **Скачать и настроить** (Download and set up).
3. Downloads can be cancelled and resumed. Every file is verified against a pinned model revision and checksum.
4. Choose **Проверить диктовку** (Test dictation). Record a phrase with the button or the global shortcut.

Hold **left Alt** on Windows or **right Option** on Mac, speak, and release to paste.
Esc cancels. The application never presses Enter. Manual copying is available when pasting fails.
Cancelling recognition terminates the computation process, then reloads the model automatically.
Sleep invalidates pending dictation; start a new recording after the model is ready again.
Unavailable or unidentified input fields require manual copying.
**Выбрать папку…** imports an existing faster-whisper / CTranslate2 model directory.

Windows supports CPU and NVIDIA CUDA with automatic CPU fallback if model loading or GPU warmup fails.
The Windows bundle includes the GPU runtime; the NVIDIA driver is still required for GPU acceleration.
Mac whisper.cpp setup uses Metal after a real GPU kernel warmup, with explicit CPU fallback.
It requires a separate GGML model downloaded by the button or imported as a `.bin` file.
Existing CTranslate2 models retain CPU/INT8 support. See [Mac backend validation](docs/MAC-METAL.md).
On Mac grant Microphone, Accessibility and (if requested) Input Monitoring permissions.
Preview packages are not publisher-signed or Apple-notarized yet.

Recognition is offline after the model download. Microphone audio is not saved to disk and transcript contents
are not logged. The last transcript stays in memory until exit. Data lives in
`%LOCALAPPDATA%/WhisperLocalOpen` or `~/Library/Application Support/WhisperLocalOpen`.
The original personal Windows installation is left separate.

## Memory

The **Память** (Memory) page shows RSS RAM for the application and its processes,
plus Windows dedicated/shared GPU memory. Unavailable counters are shown as unknown.
Mac Metal GPU allocations are not currently measured separately; CPU uses no separate VRAM.

Models unload after five idle minutes by default; select 1, 5, 10, 30 minutes or disable
automatic unloading. **Освободить память** (Free memory) stops the model process while
keeping local model files and the last text. Recording, recognition and paste are protected.
The next recording starts immediately while the model loads; recognition waits for readiness.
Esc discards waiting audio. The first result after unloading can take longer.

For faster-whisper, optional INT8 mode reloads the active model. Automatic mode retains NVIDIA FP16 and CPU INT8
where supported. See [measurements and limits](docs/MEMORY.md) for memory, speed and fixture WER.

## Development

Use Python 3.12. Install `requirements.txt`; Windows NVIDIA support additionally uses `requirements-gpu.txt`.
On Mac first build the pinned bridge with `python scripts/build_whispercpp.py` (Xcode command line
tools, CMake and Git are development requirements). Start `python app.py`. Build on the target OS:

```bash
python -m pip install -r requirements-build.txt
python -m unittest discover -s tests -v
python scripts/build_assets.py
python -m PyInstaller --noconfirm WhisperLocal.spec
```

CI builds Windows, Mac ARM64 and Mac Intel packages and checks the bundled recognition helper with a real
Whisper base model. Manual Mac microphone, permission and paste verification is still required.
See [validation](docs/VALIDATION.md), [Russian documentation](README.md) and [sound provenance](assets/sounds/SOURCES.md).
Cancellation, interruption and field safety checks are documented in [reliability](docs/RELIABILITY.md).
