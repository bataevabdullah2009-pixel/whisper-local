# Configurable dictation shortcut and mode

The General page chooses a default or custom shortcut and Hold / Press to start/stop.
Apply validates both settings and changes the hook without restarting the app. A conflict leaves
the previous configuration and hook in place. Other settings retain automatic saving.
`hotkey: "default"` and `dictation_mode: "hold"` preserve left Alt on Windows and right Option on Mac,
including old configs without these fields. Invalid saved values fall back to these defaults.

Custom shortcuts combine Ctrl, Alt/Option, Shift and, on Mac, Command with a letter, digit, Space or
F1–F19. Windows excludes F12. Function keys also work without modifiers. The named letters describe
physical Latin keyboard positions, so the same key works with Russian and English input layouts.
The original single modifier remains available through Default. Esc is always reserved for cancellation.

## Behavior and conflict checks

- Hold waits for the existing 180 ms threshold, then records until the primary key or a required
  modifier is released. A quick tap does not record. The default Windows Alt tap is replayed normally.
- Toggle starts on the first press and finishes on the next. Releases and key autorepeat do not
  toggle. The three-minute maximum and capsule finish/cancel buttons work in both modes.
- An extra key during a held shortcut cancels activation; default Alt/Option combinations remain
  available to the target application. Pause prevents activation and survives rebinding or wake.
- Rebinding is refused during recording, waiting for a model, recognition, cancellation, paste,
  unloading or while the old shortcut is still held. Events queued by a retired hook are ignored.
- Known editing/system combinations are rejected before installing a hook. Windows probes
  [RegisterHotKey](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey);
  macOS checks enabled symbolic system shortcuts and probes exclusive Carbon RegisterEventHotKey
  on the main thread, then immediately unregisters the probe. Custom input
  is handled by the existing low-level hook / event tap, including key release and Esc.
  Registration probes run when installing or reapplying a hook. They do not reserve the shortcut
  permanently or enumerate local accelerators and other applications' event taps. Test the chosen
  shortcut in the application you use; another app can also claim it after the check.
- Focus is captured at activation and checked before opening the microphone, every 200 ms while
  recording, and before finishing. Losing an identified field cancels and discards audio. Unknown
  initial fields retain the manual-copy path. Recognition/paste retain the original target, and the
  existing final paste checks refuse a different field without replacing it with a later press target.
- Recording and recognition stay offline. No key sequences, typed text, microphone audio or
  transcript content are written to the technical log. Existing capsule sizes/colors/sounds remain.

## Automated checks

```bash
python -m unittest discover -s tests -v
```

`tests/test_hotkey.py` covers migration, invalid/reserved settings, physical key mappings, both modes,
autorepeat, early modifier release, ordinary Alt/Option chords, Esc down/repeat/up, pause, busy rebinding,
config reload/reset, native conflict rollback, retired hook events and identified/unknown focus paths.
Windows checks an actual registered-shortcut conflict without installing a hook; injected input and
extended-key replay use a mocked hook boundary. Mac checks the event-tap boundary with mocks on all
platforms, and checks real Carbon registration/collision/release on Mac CI.

Windows development run: 133 tests, 132 passed and one Mac-only test skipped. This uses mocked
microphone/controller operations and an offscreen Qt settings render; it is not a real dictation check.
Desktop CI results belong on the PR. Successful packaging/Carbon checks on a hosted Mac runner do
not verify microphone access, event-tap permissions or insertion in applications on a physical Mac.

## Manual gates — pending

Repeat each case five times on Windows, Apple Silicon Mac and Intel Mac. Record OS/architecture,
shortcut/mode, input layout, application, observed behavior and technical error codes; keep private
audio and transcript text out of evidence.

| Case | Expected result |
| --- | --- |
| Old configuration/defaults | Left Alt / right Option and Hold; quick taps and ordinary Alt/Option shortcuts work |
| Custom Ctrl+Shift+Space, Hold | Both modifier sides work; release Space, Ctrl or Shift ends one recording; no shortcut text reaches the field |
| Default and custom Toggle | Tap starts, tap stops; prolonged key autorepeat causes no extra transition; max duration and capsule controls work |
| Russian/English layout | Same physical key activates; Russian speech retains leading words and inserts once |
| Reserved/occupied shortcut | Clear error and unchanged previous key/mode; previous shortcut still works; compare system and application-local conflicts |
| Esc in both modes | Cancel during recording, cold model load, recognition and queued paste; no late insertion; next dictation succeeds |
| Focus loss | Change field, browser tab/application, or close the original window while recording: discard recording; change during recognition: refuse a different paste target |
| Pause/rebind/relaunch/wake | No activation while paused; persisted key/mode and paused hook behavior are consistent; no stuck modifiers or retired events |
| Cold model/clipboard | Start after idle unload; stop before readiness; cancel or insert once; held modifiers delay paste and user clipboard is preserved |
| Mac permissions/input | Microphone, Accessibility/Input Monitoring, actual Option/custom event tap, TextEdit/browser/editor/terminal paste; document Fn/media-key settings |

The shortcut, memory and Metal PRs (#3–#5) were merged into their respective stack bases on
2026-10-01. Their manual gates still need evidence. Physical Metal speed, representative Russian quality,
microphone and paste checks in [MAC-METAL.md](MAC-METAL.md) remain required; no acceleration
completion claim is made from CI.
