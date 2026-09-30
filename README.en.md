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
**Выбрать папку…** imports an existing faster-whisper / CTranslate2 model directory.

Windows supports CPU and NVIDIA CUDA with automatic CPU fallback if model loading or GPU warmup fails.
The Windows bundle includes the GPU runtime; the NVIDIA driver is still required for GPU acceleration.
Mac currently uses CPU / INT8 on both architectures. Apple GPU / Metal acceleration is not implemented.
On Mac grant Microphone, Accessibility and (if requested) Input Monitoring permissions.
Preview packages are not publisher-signed or Apple-notarized yet.

Recognition is offline after the model download. Microphone audio is not saved to disk and transcript contents
are not logged. The last transcript stays in memory until exit. Data lives in
`%LOCALAPPDATA%/WhisperLocalOpen` or `~/Library/Application Support/WhisperLocalOpen`.
The original personal Windows installation is left separate.

## Development

Use Python 3.12. Install `requirements.txt`; Windows NVIDIA support additionally uses `requirements-gpu.txt`.
Start `python app.py`. Build on the target OS:

```bash
python -m pip install -r requirements-build.txt
python -m unittest discover -s tests -v
python scripts/build_assets.py
python -m PyInstaller --noconfirm WhisperLocal.spec
```

CI builds Windows, Mac ARM64 and Mac Intel packages and checks the bundled recognition helper with a real
Whisper base model. Manual Mac microphone, permission and paste verification is still required.
See [validation](docs/VALIDATION.md), [Russian documentation](README.md) and [sound provenance](assets/sounds/SOURCES.md).
