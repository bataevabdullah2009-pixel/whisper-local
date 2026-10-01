"""Temporary Developer ID session and notarization; manual release workflow only."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys


REQUIRED_SECRETS = (
    "MACOS_CERTIFICATE_P12_BASE64", "MACOS_CERTIFICATE_PASSWORD",
    "MACOS_NOTARY_KEY_P8_BASE64", "MACOS_NOTARY_KEY_ID", "MACOS_NOTARY_ISSUER_ID",
)


def run(arguments: list[str]) -> str:
    # Never echo commands, credentials, or secret-tool output in an exception.
    result = subprocess.run(arguments, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{Path(arguments[0]).name} failed (exit {result.returncode})")
    return result.stdout


def session_paths() -> tuple[Path, Path]:
    if (sys.platform != "darwin" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or not os.environ.get("RUNNER_TEMP") or not os.environ.get("GITHUB_ENV")):
        raise RuntimeError("Signing sessions require a temporary GitHub-hosted Mac runner")
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    return runner_temp, runner_temp / "whisperlocal-signing"


def prepare() -> None:
    _, directory = session_paths()
    missing = [name for name in REQUIRED_SECRETS if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Missing release credentials: " + ", ".join(missing))
    if directory.exists():
        raise RuntimeError("A signing session already exists")
    old_keychains = re.findall(r'"([^"\n]+)"', run(["security", "list-keychains", "-d", "user"]))
    if not old_keychains:
        raise RuntimeError("Cannot preserve the runner keychain search list")
    directory.mkdir(mode=0o700)
    keychain = directory / "release.keychain-db"
    (directory / "state.json").write_text(json.dumps({"keychains": old_keychains}), encoding="utf-8")
    p12 = directory / "developer-id.p12"
    p8 = directory / "notary.p8"
    try:
        p12.write_bytes(base64.b64decode(os.environ["MACOS_CERTIFICATE_P12_BASE64"], validate=True))
        p8.write_bytes(base64.b64decode(os.environ["MACOS_NOTARY_KEY_P8_BASE64"], validate=True))
        p12.chmod(0o600)
        p8.chmod(0o600)
        password = secrets.token_hex(32)
        run(["security", "create-keychain", "-p", password, str(keychain)])
        run(["security", "set-keychain-settings", "-lut", "21600", str(keychain)])
        run(["security", "unlock-keychain", "-p", password, str(keychain)])
        run(["security", "import", str(p12), "-k", str(keychain),
             "-P", os.environ["MACOS_CERTIFICATE_PASSWORD"], "-T", "/usr/bin/codesign",
             "-T", "/usr/bin/security"])
        run(["security", "set-key-partition-list", "-S", "apple-tool:,apple:,codesign:",
             "-s", "-k", password, str(keychain)])
        run(["security", "list-keychains", "-d", "user", "-s", str(keychain), *old_keychains])
        identities = run(["security", "find-identity", "-v", "-p", "codesigning", str(keychain)])
        matches = re.findall(r'([0-9A-Fa-f]{40})\s+"Developer ID Application:[^"\n]+"', identities)
        if len(matches) != 1:
            raise RuntimeError("Exactly one valid Apple Developer ID Application identity is required")
        with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as output:
            output.write(f"WHISPERLOCAL_MACOS_SIGNING_IDENTITY={matches[0]}\n")
    finally:
        p12.unlink(missing_ok=True)


def cleanup() -> None:
    runner_temp, directory = session_paths()
    if not directory.exists():
        return
    # Verify the absolute deletion target and restore only our own temporary session.
    if directory.resolve().parent != runner_temp or directory.name != "whisperlocal-signing":
        raise RuntimeError("Unsafe signing cleanup path")
    state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    try:
        run(["security", "list-keychains", "-d", "user", "-s", *state["keychains"]])
    finally:
        keychain = directory / "release.keychain-db"
        if keychain.exists():
            try:
                run(["security", "delete-keychain", str(keychain)])
            finally:
                shutil.rmtree(directory)
        else:
            shutil.rmtree(directory)


def accepted_submission(output: str) -> dict:
    submission = json.loads(output)
    if submission.get("status") != "Accepted" or not submission.get("id"):
        raise RuntimeError("Apple notarization did not return an Accepted submission")
    return {"id": submission["id"], "status": submission["status"]}


def sign_app(app: Path) -> None:
    session_paths()
    identity = os.environ.get("WHISPERLOCAL_MACOS_SIGNING_IDENTITY")
    if not identity:
        raise RuntimeError("Prepare a Developer ID signing session first")
    app = app.resolve(strict=True)
    entitlements = Path(__file__).resolve().parents[1] / "packaging/macos-entitlements.plist"
    # PyInstaller's BUNDLE inherits COLLECT's final EXE (the worker). Apply GUI
    # resource/callback rights to the app after bundle construction, without
    # giving microphone or executable-memory exceptions to the ASR worker.
    run(["codesign", "--force", "--timestamp", "--options", "runtime", "--sign", identity,
         str(app / "Contents/MacOS/WhisperWorker")])
    run(["codesign", "--force", "--timestamp", "--options", "runtime", "--sign", identity,
         "--entitlements", str(entitlements), str(app)])
    run(["codesign", "--verify", "--deep", "--strict", "--all-architectures", str(app)])


def notarize(dmg: Path, report: Path) -> None:
    _, directory = session_paths()
    if not os.environ.get("WHISPERLOCAL_MACOS_SIGNING_IDENTITY"):
        raise RuntimeError("Prepare a Developer ID signing session first")
    dmg = dmg.resolve(strict=True)
    run(["hdiutil", "verify", str(dmg)])
    run(["codesign", "--force", "--timestamp", "--sign",
         os.environ["WHISPERLOCAL_MACOS_SIGNING_IDENTITY"], str(dmg)])
    run(["codesign", "--verify", "--strict", str(dmg)])
    output = run(["xcrun", "notarytool", "submit", str(dmg), "--wait", "--timeout", "20m",
                  "--output-format", "json", "--key", str(directory / "notary.p8"),
                  "--key-id", os.environ["MACOS_NOTARY_KEY_ID"],
                  "--issuer", os.environ["MACOS_NOTARY_ISSUER_ID"]])
    submission = accepted_submission(output)
    run(["xcrun", "stapler", "staple", str(dmg)])
    run(["xcrun", "stapler", "validate", str(dmg)])
    run(["spctl", "--assess", "--type", "open", "--context", "context:primary-signature", str(dmg)])
    # Contains only public artifact metadata, never private-key/API-key contents.
    report.parent.mkdir(parents=True, exist_ok=True)
    with dmg.open("rb") as stream:
        sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    report.write_text(json.dumps({"artifact": dmg.name, "sha256": sha256,
                                  "submission": submission, "stapled": True}, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "cleanup", "sign-app", "notarize"))
    parser.add_argument("--app", type=Path)
    parser.add_argument("--dmg", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            prepare()
        elif args.action == "cleanup":
            cleanup()
        elif args.action == "sign-app":
            if not args.app:
                parser.error("sign-app requires --app")
            sign_app(args.app)
        else:
            if not args.dmg or not args.report:
                parser.error("notarize requires --dmg and --report")
            notarize(args.dmg, args.report)
    except (RuntimeError, ValueError, OSError, KeyError) as error:
        # Base64 parse errors can contain encoded fragments: print a type only.
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
