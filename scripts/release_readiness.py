"""Check recorded release evidence; never manufacture a physical test result."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

try:
    from scripts.release_manifest import TARGET_FILES, digest
except ModuleNotFoundError:
    from release_manifest import TARGET_FILES, digest


COMMON_CHECKS = (
    "clean_install_first_dictation_uninstall", "microphone_and_permissions",
    "hotkeys_hold_toggle_escape", "paste_clipboard_focus_targets",
    "microphone_disconnect_sleep_wake", "offline_dictation",
    "download_interruption_restart_disk_full", "model_idle_unload_reload",
    "dictionary_and_cleanup", "capsule_and_sound_choices",
)
PLATFORM_CHECKS = {
    "Windows-x64": ("cpu_and_nvidia_fallback", "trusted_publisher_install_uninstall"),
    "macOS-Apple-Silicon": ("gatekeeper_download_quarantine_offline", "metal_cpu_fallback"),
    "macOS-Intel": ("gatekeeper_download_quarantine_offline", "metal_cpu_fallback"),
}


def readiness_errors(evidence: dict, artifact_dir: Path | None = None) -> list[str]:
    errors: list[str] = []
    if evidence.get("schema_version") != 1:
        errors.append("Unsupported evidence schema")
    commit = evidence.get("source_commit", "")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        errors.append("A full source commit SHA is required")
    license_evidence = evidence.get("sound_redistribution", {})
    if (not isinstance(license_evidence, dict) or license_evidence.get("status") != "passed"
            or not license_evidence.get("permission_reference")):
        errors.append("Sound redistribution permission remains unverified")
    targets = evidence.get("targets", {})
    if not isinstance(targets, dict):
        return errors + ["Target evidence must be an object"]
    for target, filename in TARGET_FILES.items():
        record = targets.get(target, {})
        if not isinstance(record, dict):
            errors.append(f"{target}: invalid target record")
            continue
        manifest = record.get("signed_manifest", {})
        if not isinstance(manifest, dict):
            errors.append(f"{target}: signed manifest missing")
            manifest = {}
        sha = manifest.get("sha256", "")
        if (manifest.get("schema_version") != 1 or manifest.get("target") != target or manifest.get("artifact") != filename
                or manifest.get("source_commit") != commit or manifest.get("signature") != "verified"
                or manifest.get("timestamp") != "verified" or not isinstance(sha, str)
                or not re.fullmatch(r"[0-9a-f]{64}", sha)
                or not re.fullmatch(r"https://github\.com/[^/]+/[^/]+/actions/runs/[0-9]+", str(manifest.get("verification_run", "")))):
            errors.append(f"{target}: exact signed-candidate CI manifest missing or invalid")
        if target != "Windows-x64" and (manifest.get("notarization") != "accepted_and_stapled"
                                        or not manifest.get("notary_submission_id")):
            errors.append(f"{target}: accepted notarization and stapling missing")
        if artifact_dir is not None:
            artifact = artifact_dir / filename
            if not artifact.is_file() or digest(artifact) != sha:
                errors.append(f"{target}: local candidate bytes do not match signed manifest")
        physical = record.get("physical", {})
        if not isinstance(physical, dict):
            physical = {}
        if (physical.get("artifact_sha256") != sha or not sha or physical.get("source_commit") != commit
                or any(not physical.get(field) for field in ("tester", "tested_at", "os_version", "hardware"))):
            errors.append(f"{target}: physical machine, tester, date and exact artifact identity required")
        checks = physical.get("checks", {})
        if not isinstance(checks, dict):
            checks = {}
        for check in COMMON_CHECKS + PLATFORM_CHECKS[target]:
            item = checks.get(check, {})
            if not isinstance(item, dict) or item.get("status") != "passed" or not item.get("evidence"):
                errors.append(f"{target}: {check} remains incomplete")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--artifact-dir", type=Path, required=True,
                        help="directory containing all three exact signed candidate files")
    args = parser.parse_args()
    try:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        if not isinstance(evidence, dict):
            raise ValueError("Evidence must be an object")
        errors = readiness_errors(evidence, args.artifact_dir)
    except (OSError, ValueError) as error:
        print(f"Release evidence could not be read: {type(error).__name__}")
        return 1
    if errors:
        print("Release is not ready:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Recorded evidence is complete and candidate hashes match. Owner review is required before publication.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
