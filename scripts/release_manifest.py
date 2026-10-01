"""Record exact candidate bytes after the signed workflow has verified them."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re


TARGET_FILES = {
    "Windows-x64": "WhisperLocal-Windows-Setup.exe",
    "macOS-Apple-Silicon": "WhisperLocal-macOS-Apple-Silicon.dmg",
    "macOS-Intel": "WhisperLocal-macOS-Intel.dmg",
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=TARGET_FILES)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Signing status is an attestation by the preceding fail-closed workflow
    # steps, not an inference from a filename or an unsigned local build.
    if (not re.fullmatch(r"[0-9a-f]{40}", args.commit) or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_WORKFLOW") != "Signed release candidates"
            or not re.fullmatch(r"[0-9]+", os.environ.get("GITHUB_RUN_ID", ""))):
        parser.error("Manifest requires the verified signed-candidate workflow and a full commit SHA")
    candidate = args.dist / TARGET_FILES[args.target]
    result = {
        "schema_version": 1, "target": args.target, "source_commit": args.commit,
        "artifact": candidate.name, "sha256": digest(candidate),
        "verification_run": f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
        "signature": "verified", "timestamp": "verified",
        "notarization": "not_applicable" if args.target == "Windows-x64" else "accepted_and_stapled",
        "physical_checks": "unknown",
    }
    if args.target != "Windows-x64":
        notary = json.loads((args.output.parent / "notarization.json").read_text(encoding="utf-8"))
        if (notary.get("sha256") != result["sha256"] or notary.get("stapled") is not True
                or notary.get("submission", {}).get("status") != "Accepted"):
            parser.error("Notarization report does not match this exact stapled candidate")
        result["notary_submission_id"] = notary["submission"]["id"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
