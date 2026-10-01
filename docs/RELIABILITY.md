# Dictation reliability

## Behavior

- Esc while recognizing kills the process, including native CPU/CUDA computation. A replacement
  starts after the old process exits. The UI stays responsive; a model reload is required before
  the next recording. Recording cancellation discards PCM without concatenating it.
- Process callbacks belong to their worker instance. Cancelled, duplicated and stale results cannot
  paste text or stop a newer operation's timeout. Timeout and exit also stop computation.
- Windows power broadcasts and macOS workspace sleep/wake notifications invalidate the target,
  discard pending recording/paste, stop the worker and reinitialize it after wake. A clock-gap guard
  catches missed notifications or an event-loop stall over five seconds before result/paste handling.
- An inactive or failing microphone, a stream without callbacks for 2.5 seconds, invalid samples,
  or failure during stop discards the recording. Abort/close failures cannot retain its PCM.
  A late callback from a previous recording cannot enter a new recording.
- Windows compares UI Automation process/runtime IDs as well as HWNDs. Two fields within one
  renderer/window are different targets. A whole browser document without an editing provider is not
  an identified field. Unknown, protected or non-edit targets require manual copying.
  macOS retains its Accessibility element comparison and falls back to manual copying if unavailable.
- Cancellation restores the clipboard immediately when the application still owns its contents.
  Clipboard changes by the user are preserved. Delayed paste is never replayed after the deadline.

## Verified locally, 2026-10-01

Windows source checkout, Python 3.12.14, faster-whisper 1.2.1 and the pinned Whisper base model:

| Check | Result |
| --- | --- |
| Unit/UI suite | 55 tests passed |
| Real CPU recognition cancellation | Process exit in 0.062 seconds; model reload, next public speech and empty silence passed |
| Real NVIDIA CUDA recognition cancellation | Process exit in 0.094 seconds; model reload, next public speech and empty silence passed |
| Native Windows UI Automation and SendInput | Two Qt fields sharing HWNDs distinguished; changed field refused; exact paste and clipboard restoration passed |
| Live microphone | Received 5,760 samples; stream abort, cleanup and reopen passed; audio discarded |
| Sleep/wake, process races, device failures | Automated simulated notifications/failures and actual busy subprocess termination passed |

The cancellation timings are single observations, not a performance benchmark. Live stream abort is
not physical USB disconnection. Native paste verification uses owned Qt fixture fields, not all browsers.
No microphone recordings or transcript contents are saved in these tests. The speech fixture is a
checksum-pinned public JFK sample from whisper.cpp v1.7.2.

## Reproduce

```bash
python -m unittest discover -s tests -v
python scripts/fetch_test_speech.py
python scripts/smoke_asr.py
python scripts/smoke_reliability.py --audio build/jfk.wav
# Actual Windows GUI/clipboard test, in an interactive desktop session:
python scripts/smoke_windows_focus.py
# Opens the selected/default microphone briefly; does not transcribe or save audio:
python scripts/smoke_microphone.py
```

`smoke_reliability.py --model PATH --device cuda --cuda DLL_DIRECTORY --audio build/jfk.wav`
checks an existing NVIDIA setup without downloading a model. `--worker PATH` checks a standalone helper.
After cancellation a new public speech request must succeed; the next silence request must be empty.

Desktop CI runs the suite and packaged CPU cancellation/reload on Windows, Apple Silicon and Intel Mac.
Windows additionally installs the generated installer, launches GUI/helper with host Python removed from
PATH and Python environment variables, checks installed offline recognition, then uninstalls. This is a
fresh CI runner check, not an interactive clean Windows machine with no Python installed. CI results are
recorded on the PR; the steps listed here do not imply a successful run.

## Manual gates still required

Record OS/build, architecture, model/device, case, expected/actual behavior, repeat count and any
technical error codes. Do not attach microphone audio or private transcripts.

| Environment | Procedure and acceptance criteria | Status |
| --- | --- | --- |
| Clean Windows 10/11 without Python | Install, download model, first dictation, relaunch, uninstall; existing personal installation stays separate | Pending |
| Real Apple Silicon Mac | Microphone and Accessibility/Input Monitoring prompts; right Option hold/release, shortcuts, Esc; TextEdit, browser, VS Code and terminal paste | Pending |
| Real Intel Mac | Repeat the Apple Silicon checklist with the Intel package and CPU model | Pending |
| Each OS, physical USB microphone | Unplug during recording and before release; no partial automatic paste; reconnect/select microphone; next dictation succeeds, repeat five times | Pending |
| Each OS, actual system sleep | Sleep during recording, recognition and queued paste; wake after 30 seconds; no late paste, no stuck hotkey; next dictation succeeds, repeat five times | Pending |
| Each OS, fields/applications | Switch field within one browser tab, tab, application, and close the original window while recognizing; text stays available for manual copying | Pending |
| Each OS, clipboard/modifiers/offline | Copy other content while paste is queued, hold modifiers, cancel, and disconnect network after setup; user clipboard preserved, no Enter sent, next dictation succeeds | Pending |

Keep the PR in draft until these real-machine gates have evidence. Signing and public distribution belong
to the separate public-release task; Metal acceleration belongs to the separate Mac acceleration task.
