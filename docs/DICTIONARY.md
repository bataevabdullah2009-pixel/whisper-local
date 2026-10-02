# Local user dictionary

Open **Словарь** (Dictionary) in settings. Enter **Whisper распознаёт** (what Whisper
recognizes) and **Как нужно писать** (the desired spelling), then **Добавить правило**.
For example, `опен ай` → `OpenAI`, `алексей` → `Алексей`, or `кубер нетес` → `Kubernetes`.
These examples are not preloaded; new and existing installations start with an empty dictionary.

Select a row to edit its two fields, then **Сохранить правило**. **Новое правило** starts
a new entry; **Удалить** removes the selected rule. Draft edits do not affect dictation or
the preview until saved. Blank entries, duplicate source phrases, control characters and
terms longer than 200 characters are rejected. The dictionary accepts up to 500 rules.

**Использовать замены при диктовке** enables or disables the dictionary immediately,
without reloading the model. Turning it off keeps every rule and returns the original ASR
text on the next result if cleanup is also disabled. Rules remain editable while disabled.
Saving a rule or changing this switch writes the existing local `config.json`; the application reports storage errors.
There is no account, synchronization, model hint, model download or separate dictionary service.

## Matching behavior

- Source words and phrases match literally, ignoring case; replacement spelling is exact.
  Regex characters such as `+`, `.` and backslashes are literal in both fields.
- Unicode word boundaries protect longer words, identifiers and numbers: `api` does not
  change `api_key` or `api2`, and `ай` does not change `айтишник`.
- A phrase can match spaces, tabs and non-breaking spaces between its words, but cannot
  cross a line break. Unmatched spacing, punctuation, fillers and paragraphs stay unchanged.
- At the same starting position, the longest source phrase wins. Replacements scan the original
  transcript once; output from one rule never triggers another rule, including cyclic rules.
- There is no fuzzy matching, inflection, automatic case selection or equivalence between Russian
  `е` and `ё`. Add a separate rule for each recognition variant that needs correction.
- Outer whitespace is trimmed from both saved fields; source spacing and Unicode representation
  are normalized for duplicate detection. Replacement spacing inside a field is retained.

For both faster-whisper and whisper.cpp, a valid, nonempty result passes through the dictionary
after recognition and optional [local cleanup](TEXT-CLEANUP.md), before insertion. Enabled source
matches are protected during cleanup; dictionary replacement output is kept exact. Automatic paste,
retry paste and manual copy share this same processed result. A retry never runs replacements again.
Cancelled, stale and empty results
retain the existing safety checks. The rules and enabled state at result arrival are used;
changing settings does not retroactively rewrite the previous result.

## Local data and preview

`dictionary_enabled` defaults to `true`; `dictionary_rules` defaults to `[]`.
Old configs without these fields keep existing behavior. The rules are a list of objects with
`source` and `replacement`. Valid entries in a manually damaged dictionary are retained;
malformed entries and later duplicate sources are ignored when loading it.

Rules intentionally persist in the same local data directory as other settings. Their contents
are not logged or sent to the ASR worker. The **Проверка сохранённых правил** preview applies
the same matcher to typed text, with the current enabled state. Preview input/output stay in
memory, are not saved to settings or technical logs, and require neither a microphone nor a model.
Dictation audio and transcript content continue to stay out of files and logs.

## Validation

```bash
python -m unittest discover -s tests -v
```

`tests/test_dictionary.py` covers names/terms, literal punctuation, Cyrillic boundaries,
longest phrases, cycles, horizontal spacing, disabled replacements, migration and validation,
add/edit/delete, unsaved drafts, preview, persistence and the controller's paste/copy paths for
both backend identities. The controller checks use synthetic results and mocked paste operations;
they do not prove microphone quality or insertion into another application.

The settings page is rendered offscreen at the existing minimum window size, with scrolling
when content needs more room. Hosted Windows/Apple Silicon/Intel builds run the same tests;
CI results are recorded on the PR and do not substitute for real-device checks.

Manual gates remain pending on Windows and physical Apple Silicon/Intel Macs:

1. Add an actual name, company and technical term, edit one, delete one, restart and confirm persistence.
2. With network access disabled after model setup, dictate into the practice field and an external
   editor. Confirm the exact spelling once in automatic paste and manual copy, including both
   hold and toggle modes.
3. Disable replacements, repeat the phrase, then enable them again. Confirm the rules survive
   and the next result follows the switch. Test overlapping phrases and a larger dictionary.
4. Check keyboard navigation, selected-row editing, long terms, validation messages and scrolling
   at the minimum window size on each OS.

PRs #3–#5 were merged into their respective stack bases on 2026-10-01. This dictionary
uses the resulting `codex/macos-metal-whispercpp` branch as its base and stays draft
pending its manual checks. `dev` has not yet incorporated that full stack. Windows
hotkey/mode checks in [DICTATION.md](DICTATION.md) and physical Mac
Metal speed, Russian dictation, microphone, permissions and paste checks in [MAC-METAL.md](MAC-METAL.md)
are still required. No physical validation is inferred from code checks or CI.

## Following stages

The separate [local cleanup stage](TEXT-CLEANUP.md) addresses prose spacing and configurable hesitation removal.
More involved local rewriting is a later stage. Download improvements, Windows signing,
macOS signing/notarization and preparation for public release follow; they are not part of this PR.
