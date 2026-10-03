# Local text cleanup

The settings page also contains the separate optional [whole-phrase editor](PHRASE-EDITOR.md).
The rules below describe **Простая очистка** only. The automatic editor has its own
enable switch and requires an explicit one-time model download or verified import.

Open **Очистка текста** (Text cleanup) in settings and enable **Очищать текст после
распознавания**. Cleanup is off by default, including for existing installations.
The two options are independent and are retained when the main switch is turned off.
Changes are saved automatically and affect the next accepted recognition result,
without restarting the model or changing a previous result.

- **Исправлять пробелы у знаков препинания** fixes ordinary prose spacing.
- **Убирать междометия «эээ», «эм», «uh», «um»** removes a narrow set of hesitation
  tokens. This option is off by default; enable it only when those tokens are unwanted.

With both options enabled, `эээ,  привет ,мир!` becomes `привет, мир!`.
The preview shows the same cleanup and saved dictionary rules used for insertion.
It requires no microphone, model or network. As with worker results, surrounding
whitespace is trimmed before processing. The dictionary page continues to preview
only dictionary rules.

## Rules and limits

Spacing collapses repeated ordinary, non-breaking and narrow non-breaking spaces
inside prose and removes those spaces before `, . ; : ! ? …`. It adds a missing
space after commas, semicolons, question marks and exclamation marks followed by
a letter. It leaves periods and colons without a following space alone because
they can belong to a domain, version or time. It does not invent punctuation,
capitalize sentences, rewrite wording or remove repeated words.

Line breaks, including CRLF, paragraphs, tabs and leading indentation are preserved
by cleanup. The controller's existing outer-whitespace trimming still applies to
worker results. Paired quotes, inline backticks, fenced code, typical URLs, email
addresses, paths, dotted terms, versions, decimal numbers and times are copied
verbatim. Lines that look like code (assignments, brackets, braces or function calls)
are also preserved. This is conservative pattern matching, not a general code parser;
use quotes/backticks or disable cleanup when exact technical formatting matters.

Hesitations match whole tokens without regard to case: two or more `э`, `э` followed
by one or more `м`, `u` followed by one or more `h` or `m`. Examples: `ээ`, `эээ`,
`эм`, `эмм`, `uh`, `uhh`, `um`, `umm`. Longer words, identifiers and hyphenated words
are protected by token boundaries. Meaningful words such as `ну`, `вот`, `как бы`,
`короче`, `да` and `нет` stay unchanged. Quoted and technical uses are preserved.
Hesitation removal joins neighboring prose with a space where needed and removes
an adjacent comma so `Привет, эм, мир` becomes `Привет, мир`, and `Привет, эм`
becomes `Привет`. Other punctuation remains when substantive text survives.
A result consisting entirely of removed hesitations and sentence punctuation
becomes empty; nothing is pasted and the previous result remains available.

## Dictionary priority and insertion

Enabled dictionary source matches are protected during cleanup. Dictionary rules
then run once on the cleaned text. For example, a saved `эм` → `EM` rule takes
priority over hesitation removal. Replacement spelling and internal spaces remain
exact, and replacement output never re-enters cleanup or dictionary matching.
Disabling the dictionary removes this protection but retains its saved rules.

Both faster-whisper and whisper.cpp use the same result path. Paste, manual copy
and retry share the stored processed result; retries do not process it again.
Cancelled, stale, empty and error events keep their existing guards and do not
run either text processor. Settings at result arrival determine processing.

## Data and validation

The local `config.json` stores only these options and existing user settings:

```json
{
  "cleanup_enabled": false,
  "cleanup_spacing": true,
  "cleanup_fillers": false
}
```

Missing or non-boolean values use these defaults. Cleanup uses Python rules in
memory, with no extra model, dependency, download or worker request. Preview text,
recognized text and microphone audio are not written to settings or logs.

`tests/test_text_cleanup.py` checks rule boundaries, conservation, independent
options, migration, dictionary priority and idempotence. `tests/test_cleanup_integration.py`
checks live UI changes, preview, settings persistence, both backend result paths,
copy/retry, stale/cancelled events, empty cleaned results and content-free logging.
The tests use synthetic text and mocked insertion; they do not establish microphone
quality or insertion into another application. The existing Desktop builds workflow
builds Windows x64, macOS Apple Silicon and macOS Intel; results are recorded on the PR.

Real-device checks remain pending on Windows and physical Apple Silicon/Intel Macs:

1. Enable cleanup, dictate a phrase with hesitations into the practice field and an
   external editor, and compare automatic paste with manual copy in hold and toggle modes.
2. Toggle each option, then the main switch; confirm the next result follows the
   options, previous text stays unchanged, and options survive restarting the preview app.
3. Check actual names/terms with dictionary rules, hesitation overrides, URLs,
   decimal numbers, quoted text, paragraphs and a hesitation-only recording.
4. Check keyboard navigation and scrolling at the minimum window size on each OS.

Development starts from PR #6's merged `codex/macos-metal-whispercpp` base in a new
`codex/offline-text-cleanup` branch. This PR does not merge the earlier stack into `dev`.
The optional [phrase editor](PHRASE-EDITOR.md) now adds local model-based editing separately.
Physical microphone, permission and paste checks remain separate from CI.
