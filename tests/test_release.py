import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.macos_signing import accepted_submission, prepare, run, sign_app
from scripts.release_manifest import TARGET_FILES, digest
from scripts.release_readiness import COMMON_CHECKS, PLATFORM_CHECKS, readiness_errors


class ReleaseGateTests(unittest.TestCase):
    def complete_evidence(self, directory):
        commit = "a" * 40
        result = {"schema_version": 1, "source_commit": commit,
                  "sound_redistribution": {"status": "passed", "permission_reference": "owner-held permission"},
                  "targets": {}}
        for target, filename in TARGET_FILES.items():
            artifact = directory / filename
            artifact.write_bytes((target + " synthetic candidate").encode())
            sha = digest(artifact)
            result["targets"][target] = {
                "signed_manifest": {"schema_version": 1, "target": target, "source_commit": commit, "artifact": filename,
                                    "sha256": sha, "signature": "verified", "timestamp": "verified",
                                    "verification_run": "https://github.com/test/project/actions/runs/123",
                                    "notarization": "not_applicable" if target == "Windows-x64" else "accepted_and_stapled",
                                    "notary_submission_id": "synthetic-submission"},
                "physical": {"artifact_sha256": sha, "source_commit": commit, "tester": "synthetic tester",
                             "tested_at": "2026-10-01T12:00:00+03:00", "os_version": "synthetic OS",
                             "hardware": "synthetic physical hardware",
                             "checks": {name: {"status": "passed", "evidence": "synthetic recorded outcome"}
                                        for name in COMMON_CHECKS + PLATFORM_CHECKS[target]}}}
        return result

    def test_complete_evidence_and_exact_bytes_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            evidence = self.complete_evidence(directory)
            self.assertEqual(readiness_errors(evidence, directory), [])
            (directory / TARGET_FILES["Windows-x64"]).write_bytes(b"different candidate")
            self.assertTrue(any("bytes" in error for error in readiness_errors(evidence, directory)))

    def test_ci_success_never_fills_unknown_physical_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            evidence = self.complete_evidence(directory)
            check = evidence["targets"]["macOS-Intel"]["physical"]["checks"]["paste_clipboard_focus_targets"]
            check["status"] = "unknown"
            self.assertTrue(any("paste_clipboard" in error for error in readiness_errors(evidence, directory)))

    def test_pending_sound_permission_blocks_ready_signed_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            evidence = self.complete_evidence(directory)
            evidence["sound_redistribution"]["status"] = "unknown"
            self.assertTrue(any("Sound" in error for error in readiness_errors(evidence, directory)))

    def test_wrong_source_and_wrong_tested_artifact_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            evidence = self.complete_evidence(directory)
            evidence["targets"]["Windows-x64"]["physical"]["artifact_sha256"] = "0" * 64
            evidence["targets"]["macOS-Apple-Silicon"]["signed_manifest"]["source_commit"] = "b" * 40
            errors = readiness_errors(evidence, directory)
            self.assertTrue(any("Windows-x64: physical" in error for error in errors))
            self.assertTrue(any("macOS-Apple-Silicon: exact" in error for error in errors))

    def test_notarization_claim_without_submission_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            evidence = self.complete_evidence(directory)
            del evidence["targets"]["macOS-Intel"]["signed_manifest"]["notary_submission_id"]
            self.assertTrue(any("notarization" in error for error in readiness_errors(evidence, directory)))

    def test_current_record_and_template_remain_not_ready(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("release-evidence.template.json", "release-readiness-2026-10-01.json"):
            with self.subTest(name=name):
                evidence = json.loads((root / "docs" / name).read_text(encoding="utf-8"))
                self.assertTrue(readiness_errors(evidence))

    def test_malformed_records_fail_closed(self):
        for value in ({}, {"targets": []}, {"sound_redistribution": [], "targets": {"Windows-x64": []}}):
            self.assertTrue(readiness_errors(copy.deepcopy(value)))


class MacNotarizationTests(unittest.TestCase):
    def test_missing_credentials_fail_before_any_signing_tool(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch("scripts.macos_signing.session_paths", return_value=(Path(temporary), Path(temporary) / "session")), \
                patch("scripts.macos_signing.run") as execute, patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Missing release credentials"):
                prepare()
            execute.assert_not_called()

    def test_only_explicit_accepted_submission_is_success(self):
        self.assertEqual(accepted_submission('{"id":"public-id","status":"Accepted"}'),
                         {"id": "public-id", "status": "Accepted"})
        for result in ('{"id":"id","status":"Invalid"}', '{"id":"id","status":"In Progress"}',
                       '{"status":"Accepted"}', '{}'):
            with self.subTest(result=result), self.assertRaises(RuntimeError):
                accepted_submission(result)

    def test_subprocess_failure_does_not_echo_secrets(self):
        with patch("scripts.macos_signing.subprocess.run") as execute:
            execute.return_value.returncode = 1
            execute.return_value.stdout = "PRIVATE-KEY"
            execute.return_value.stderr = "PASSWORD"
            with self.assertRaises(RuntimeError) as raised:
                run(["security", "import", "PRIVATE-KEY", "-P", "PASSWORD"])
            self.assertNotIn("PASSWORD", str(raised.exception))
            self.assertNotIn("PRIVATE-KEY", str(raised.exception))

    def test_gui_rights_are_not_applied_to_worker(self):
        with tempfile.TemporaryDirectory() as temporary, patch("scripts.macos_signing.session_paths"), \
                patch("scripts.macos_signing.run") as execute, \
                patch.dict("os.environ", {"WHISPERLOCAL_MACOS_SIGNING_IDENTITY": "a" * 40}):
            sign_app(Path(temporary))
            commands = [call.args[0] for call in execute.call_args_list]
            self.assertNotIn("--entitlements", commands[0])
            self.assertIn("WhisperWorker", commands[0][-1])
            self.assertIn("--entitlements", commands[1])
            self.assertIn("--deep", commands[2])


if __name__ == "__main__":
    unittest.main()
