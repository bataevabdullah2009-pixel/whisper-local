# Offline whole-phrase editor

Open **Очистка текста**, click **Скачать редактор · 1,8 ГБ**, then enable
**Исправлять ошибки, повторы и пунктуацию во всей фразе**. No word list is required.
An existing Qwen3 1.7B Q8_0 GGUF can be imported with **Выбрать скачанную модель…**;
the helper checks its pinned size and SHA-256 before activation. Cancelled downloads
keep resumable `.part` files. Download, retry and import require an explicit button press.

The editor is disabled for new and existing configurations. Simple cleanup remains
independent. A missing model/runtime or failed inference falls back to Whisper's text;
the page shows the reason. Downloading alone does not enable editing.

## Pipeline and privacy

Both ASR backends use: recognition → optional simple cleanup → optional phrase editor
→ dictionary → paste/copy. Dictionary source matches are hidden from the editor and
replaced once afterwards, with exact replacement spelling. Dictionary options are
snapshotted at ASR result arrival. Paste retry and manual copy reuse the finished text.

Inference uses pinned llama.cpp b6764 and the official Qwen3 1.7B Q8_0 GGUF on CPU
on Windows x64, Apple Silicon and Intel Macs. CPU operation keeps ASR GPU allocations
independent. Runtime binaries are prepared during builds from verified official release
archives, bundled and signed by the existing release pipeline. The download catalog pins
the model revision, file size and SHA-256. Qwen is Apache-2.0; llama.cpp is MIT.

Each phrase runs in an ephemeral CLI process. Source text reaches it through a private
Windows named pipe or a mode-0600 Unix FIFO inside a private temporary directory,
never argv, regular files, network services or a prompt cache. The CLI receives only
the pipe name via `--file`; bytes stay in pipe/worker memory. Prompt display and debug
logging are disabled; stderr is drained, not retained. External `LLAMA_*` and `GGML_*` flags
are removed. Only the CLI and local libraries are bundled; server executables and the
RPC backend are excluded. The process exits after the phrase, releasing model and
context memory. Cancellation, sleep and shutdown kill it and clear Python buffers.

Model download is the only runtime network operation. Recognition and editing never
request models automatically. Audio, source phrases, edits and preview text are not
written to configuration or logs. Configuration retains only the enabled flag and model
path. The most recent pre-editor phrase remains in memory for **Скопировать последний
текст без редактора**; it includes enabled simple cleanup and dictionary replacements.

## Limits

The editor receives the entire phrase, with protected spans replaced by numbered
markers. It preserves numbers, typical URLs/emails/paths, quoted text, code-like lines,
dictionary sources, original line breaks and indentation. Validation rejects missing,
duplicated or reordered markers, added numbers, loss of negation, major shortening,
large changes and common instruction-answer formatting. Rejected output falls back
to the pre-editor text. These checks cannot guarantee semantic equivalence: a small
generative model can overlook an error or make an unwanted change.

The first implementation uses a 4096-token context, accepts up to 2400 characters,
generates at most 1536 tokens and has a 60-second deadline. Output must end with the
pinned CLI's EOG marker; token/context exhaustion is rejected. Longer phrases, ambiguous
marker-like input and technical-only input are preserved without editing. The CPU
model loads for each phrase, so processing adds latency and temporary RAM use.

Typed simple-cleanup preview stays immediate. **Проверить редактор на примере** is
an explicit model operation. Editing the input or options cancels an obsolete preview;
beginning dictation cancels preview inference. No microphone or paste is involved.

## Verification

- `tests/test_phrase_editor.py`: output conservation, protected data, dictionary priority,
  ASR parity, cancellation, stale completions, fallback, original retrieval and privacy.
- `scripts/smoke_editor.py --model PATH --output build/editor-real-cpu.json`: real local
  CPU inference on synthetic Russian punctuation, spelling, repetition and protected text;
  reports contain only case names, booleans and durations. `--download` explicitly requests
  the pinned test model. No fixture source or generated phrase is recorded.
- Desktop CI builds the bundled editor runtime on Windows x64, Apple Silicon and Intel.
  Code tests and packaged runtime checks do not establish real microphone quality,
  Russian dictation quality, permissions or external paste on physical Macs.

Owner checks: enable the editor in a development build, dictate representative phrases,
compare edited and pre-editor text, cancel during editing, change focus before completion,
check manual copy/paste retry and repeat with the network disabled after downloading.

Primary references: [official model](https://huggingface.co/Qwen/Qwen3-1.7B-GGUF),
[pinned llama.cpp release](https://github.com/ggml-org/llama.cpp/releases/tag/b6764).
