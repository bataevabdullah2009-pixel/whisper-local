"""Content-free checks of real offline phrase inference. Synthetic text only."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from editor_service import PhraseEditorService
import editor_service
from model_manager import download_model
from user_dictionary import UserDictionary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--download", action="store_true", help="Explicitly download the pinned test model")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--runtime-dir", type=Path, help="Verify the runtime inside a packaged app")
    args = parser.parse_args()
    if args.download:
        args.model = download_model("qwen3-1.7b", ROOT / "build/test-models", lambda _event: None, "editor")
    if not args.model:
        parser.error("Pass --model or explicitly request --download")
    if args.runtime_dir:
        binary = args.runtime_dir.resolve() / ("llama-cli.exe" if sys.platform == "win32" else "llama-cli")
        editor_service.editor_executable = lambda: binary
    app = QCoreApplication.instance() or QCoreApplication([])
    service = PhraseEditorService()
    dictionary = UserDictionary([{"source": "опен ай", "replacement": "OpenAI"}])
    cases = [
        ("punctuation", "он сказал что завтра придет", lambda text: "," in text and "завтра" in text.casefold()),
        ("repetition", "я хочу хочу домой", lambda text: text.casefold().count("хочу") == 1),
        ("spelling", "я хочю чтобы ты пришол завтра", lambda text: "хочу" in text.casefold() and "пришел" in text.casefold().replace("ё", "е")),
        ("protected", "опен ай стоит 1250,50 в 10:30 ссылка https://example.com/a", lambda text: all(value in text for value in ("опен ай", "1250,50", "10:30", "https://example.com/a"))),
        ("negation", "я не хочу отправлять письмо завтра", lambda text: "не" in text.casefold().split()),
    ]
    results = []
    for label, source, check in cases:
        loop = QEventLoop()
        event = []
        def finished(*values):
            event.extend(values)
            loop.quit()
        service.finished.connect(finished)
        started = time.monotonic()
        service.edit(label, source, args.model, dictionary.pattern)
        if not event:
            QTimer.singleShot(65000, loop.quit)
            loop.exec()
        service.finished.disconnect(finished)
        accepted = bool(event) and check(event[1])
        results.append({"case": label, "passed": accepted, "edited": bool(event and event[2]),
                        "fallback": bool(not event or event[3]), "seconds": round(time.monotonic() - started, 3)})
        # No source/candidate text in reports, console output or files.
        print(json.dumps(results[-1]), flush=True)
    service.stop()
    report = {"runtime": "llama.cpp b6764 CPU", "model": "Qwen3-1.7B-Q8_0", "synthetic_only": True,
              "cases": results, "passed": all(item["passed"] for item in results)}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
