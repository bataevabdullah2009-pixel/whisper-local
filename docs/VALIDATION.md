# First-run release validation

## Automated checks

- Resuming a partial download, a server ignoring Range, incorrect byte ranges,
  corrupted downloads, verified partial files and immutable catalogue checksums.
- CUDA failure during actual model warmup falls back to CPU; macOS does not request CUDA.
- Resource-based model recommendation and separation from personal installation paths.
- First launch without a model, download failure, model download finishing during a dictation,
  cancel/retry/progress/practice UI states and macOS LaunchAgent argument quoting.
- Local dictionary matching, add/edit/delete/preview, disabled rules, migration and persistence,
  and processed-text copy/paste paths with synthetic results. See [DICTIONARY.md](DICTIONARY.md).
- Optional local cleanup, protected technical/quoted text, dictionary priority, combined preview,
  live options and empty-result/copy/retry guards. See [TEXT-CLEANUP.md](TEXT-CLEANUP.md).
- Source and packaged workers: download and verify Whisper base, load on CPU, process silence,
  return an empty transcript, then shut down. This opens no microphone.
- Optional speech fixture: `scripts/smoke_asr.py --audio build/jfk.wav` accepts a 16 kHz mono
  16-bit WAV, e.g. the public sample from ggml-org/whisper.cpp `samples/jfk.wav`.

## Manual release gates

These are a checklist, not a claim that these tests have already passed.

- Clean Windows user without Python: install, download, first dictation, uninstall.
- Windows CPU-only and NVIDIA with current driver; a missing GPU library must show CPU fallback.
- Real Apple Silicon and Intel Macs: Gatekeeper, microphone prompt, Accessibility/Input Monitoring,
  right Option hold/release, Option shortcuts, Esc, menu-bar operation, Command+V and unchanged clipboard.
- Browser, TextEdit/Notepad, VS Code; change fields and applications during recognition.
- Microphone disconnection, system sleep, insufficient disk space and a network outage during download.
- Run the entire dictation flow with network access disabled after setup.
- Dictionary rules, enabled/disabled spelling, persistence, preview and real paste/copy in both
  dictation modes on Windows and physical Macs. See [DICTIONARY.md](DICTIONARY.md).
- Cleanup options and dictionary priority with actual dictation, preview and external paste/copy
  on Windows and physical Macs. See [TEXT-CLEANUP.md](TEXT-CLEANUP.md).
- Sign Windows installers and sign/notarize Mac bundles before a broad public release.

Signing setup, the separate manual candidate workflow and physical evidence requirements are in
[RELEASE.md](RELEASE.md). The [2026-10-02 readiness record](release-readiness-2026-10-02.json)
records successful [three-platform Desktop builds](https://github.com/bataevabdullah2009-pixel/whisper-local/actions/runs/37026534988)
for source commit `49aa8c0d3318bab94eb42161a12654f801b678ea`, including unit/UI tests,
unsigned or ad-hoc packaged smoke and hosted Windows installation/uninstallation. It keeps those
checks separate from full release approval: physical Windows/Mac workflows, public signing,
notarization and sound redistribution permission remain unknown. The
[historical record](release-readiness-2026-10-01.json) preserves the earlier blocked CI run.
The later [2026-10-03 owner sound decision](sound-distribution-owner-decision-2026-10-03.json)
allows distribution with rights status still unknown; the validator accepts that explicit
choice separately from verified permission. It does not fill the missing signing or physical evidence.
The [Mac tester guide](MAC-RELEASE-TESTING.md) describes collecting real Apple Silicon and Intel
evidence without treating hosted CI as a physical check.
Durable download behavior and its code checks are in [DOWNLOADS.md](DOWNLOADS.md).

## Scope of this preview

Mac CTranslate2 recognition uses CPU; experimental whisper.cpp / Metal remains subject to the physical
gates in [MAC-METAL.md](MAC-METAL.md). Configurable shortcuts and hold/toggle modes have code checks
and their own pending real-machine gates in [DICTATION.md](DICTATION.md). The local dictionary has
code checks and pending manual gates in [DICTIONARY.md](DICTIONARY.md). Optional local cleanup
has code checks and pending manual gates in [TEXT-CLEANUP.md](TEXT-CLEANUP.md);
more involved rewriting and automatic model-file removal are later work.
Cancelling an in-flight transcription now kills the worker,
stops native computation and automatically reloads the model. Esc during model preparation only
dismisses the capsule. Interruption validation and remaining hardware gates: [RELIABILITY.md](RELIABILITY.md).
Automatic paste success means the input shortcut was sent, not that the target application's document was read back.
Original sound files remain in this branch; their existing provenance notice is unchanged.
