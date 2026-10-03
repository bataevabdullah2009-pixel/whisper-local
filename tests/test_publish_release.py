import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import publish_release as publication
from scripts.release_manifest import TARGET_FILES, digest
from scripts.release_readiness import COMMON_CHECKS, PLATFORM_CHECKS


class PublicationFixture:
    repository = "test/project"
    source = "a" * 40
    commit = "b" * 40
    run_id = "123"
    tag = "v0.1.0"

    def __init__(self):
        self.commands = []
        self.release = None
        self.assets = []
        self.uploaded = {}
        self.existing_tag = False
        self.existing_release = False
        self.wrong_download = False
        self.wrong_manifest = False
        self.wrong_upload = False
        self.no_remote_digest = False
        self.interrupted_create = False
        self.concurrent_tag = False
        self.wrong_tag_after_create = False
        self.tag_reference = None
        self.ancestor = True
        self.package_version = "0.1.0"
        self.default_branch = "main"
        self.main_commit = "d" * 40
        self.candidate_workflow_tree = "c" * 40
        self.main_workflow_tree = self.candidate_workflow_tree
        self.truncated_tree = False
        self.change_workflows_after_download = False
        self.change_workflows_after_draft = False
        self.api = publication.GitHub(self.repository)
        self.api.get = self.get
        self.run = {"id": 123, "workflow_id": 9, "path": publication.WORKFLOW_PATH,
                    "name": "Signed release candidates", "status": "completed", "conclusion": "success",
                    "event": "workflow_dispatch", "head_branch": "main", "head_sha": self.source,
                    "repository": {"full_name": self.repository}, "head_repository": {"full_name": self.repository}}
        self.artifacts = {"total_count": 3, "artifacts": [
            {"name": "WhisperLocal-signed-" + target, "expired": False,
             "workflow_run": {"id": 123, "head_sha": self.source}} for target in TARGET_FILES]}
        self.evidence = {"schema_version": 1, "source_commit": self.source,
                         "sound_redistribution": {"status": "passed", "permission_reference": "synthetic permission"},
                         "targets": {}}
        self.packages = {filename: (target + " synthetic package").encode() for target, filename in TARGET_FILES.items()}
        with tempfile.TemporaryDirectory() as temporary:
            for target, filename in TARGET_FILES.items():
                path = Path(temporary) / filename
                path.write_bytes(self.packages[filename])
                sha = digest(path)
                manifest = {"schema_version": 1, "target": target, "source_commit": self.source,
                            "artifact": filename, "sha256": sha, "signature": "verified", "timestamp": "verified",
                            "verification_run": f"https://github.com/{self.repository}/actions/runs/{self.run_id}",
                            "notarization": "not_applicable" if target == "Windows-x64" else "accepted_and_stapled",
                            "physical_checks": "unknown"}
                if target != "Windows-x64":
                    manifest["notary_submission_id"] = "synthetic-notary-id"
                self.evidence["targets"][target] = {
                    "signed_manifest": manifest,
                    "physical": {"artifact_sha256": sha, "source_commit": self.source, "tester": "synthetic tester",
                                 "tested_at": "2026-10-02", "os_version": "synthetic OS", "hardware": "synthetic hardware",
                                 "checks": {check: {"status": "passed", "evidence": "synthetic outcome"}
                                            for check in COMMON_CHECKS + PLATFORM_CHECKS[target]}}}

    @property
    def environment(self):
        return {"RELEASE_TAG": self.tag, "CANDIDATE_RUN_ID": self.run_id, "GH_TOKEN": "synthetic-token",
                "GITHUB_SHA": self.commit, "GITHUB_REF": "refs/heads/main",
                "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": self.repository}

    def get(self, endpoint, *, missing_ok=False):
        if endpoint == "":
            return {"default_branch": self.default_branch}
        if endpoint == "git/ref/heads/main":
            return {"object": {"type": "commit", "sha": self.main_commit}}
        if endpoint == f"git/trees/{self.main_commit}?recursive=1":
            return {"truncated": self.truncated_tree,
                    "tree": [{"path": ".github/workflows", "type": "tree", "sha": self.main_workflow_tree}]}
        if endpoint == "actions/workflows/release.yml":
            return {"id": 9, "path": publication.WORKFLOW_PATH}
        if endpoint == "actions/runs/123":
            return self.run
        if endpoint.startswith("actions/runs/123/artifacts"):
            return self.artifacts
        if endpoint == "git/ref/tags/v0.1.0":
            if self.existing_tag:
                return {"object": {"type": "commit", "sha": self.source}}
            return self.tag_reference
        if endpoint.startswith("releases?"):
            if self.existing_release:
                return [{"tag_name": self.tag, "draft": True}]
            return [self.release] if self.release else []
        if endpoint.startswith("releases/99/assets"):
            return self.assets
        if endpoint == "releases/99":
            return self.release
        raise AssertionError("Unexpected read endpoint: " + endpoint)

    def command(self, arguments, *, input_data=None):
        self.commands.append(arguments)
        if arguments[:3] == ["git", "rev-parse", "HEAD"]:
            return self.commit.encode()
        if arguments == ["git", "rev-parse", f"{self.source}:.github/workflows"]:
            return self.candidate_workflow_tree.encode()
        if arguments[:2] == ["git", "show"]:
            path = arguments[2].split(":", 1)[1]
            if path.endswith(".json"):
                return json.dumps(self.evidence).encode()
            if path.endswith(".md"):
                return b"Synthetic release notes.\n"
            if path == "packaging/windows.iss":
                return f'#define AppVersion "{self.package_version}"\n'.encode()
            if path == "WhisperLocal.spec":
                return b'BUNDLE(version="0.1.0")\n'
        if arguments[:3] == ["git", "merge-base", "--is-ancestor"]:
            if not self.ancestor:
                raise publication.ReleaseError("Candidate source is not an ancestor")
            return b""
        if arguments[:3] == ["gh", "run", "download"]:
            if self.change_workflows_after_download:
                self.main_workflow_tree = "e" * 40
            directory = Path(arguments[arguments.index("--dir") + 1])
            directory.mkdir()
            target = arguments[arguments.index("--name") + 1].removeprefix("WhisperLocal-signed-")
            manifest = copy.deepcopy(self.evidence["targets"][target]["signed_manifest"])
            if self.wrong_manifest:
                manifest["physical_checks"] = "tampered"
            filename = TARGET_FILES[target]
            (directory / filename).write_bytes(b"tampered" if self.wrong_download else self.packages[filename])
            (directory / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            if target != "Windows-x64":
                (directory / "notarization.json").write_text(json.dumps(
                    {"sha256": manifest["sha256"], "stapled": True,
                     "submission": {"id": manifest["notary_submission_id"], "status": "Accepted"}}), encoding="utf-8")
            return b""
        if arguments[:3] == ["gh", "release", "create"]:
            self.release = {"id": 99, "tag_name": self.tag, "target_commitish": self.source,
                            "draft": True, "html_url": "https://github.com/test/project/releases/tag/v0.1.0"}
            for name in arguments[4:arguments.index("--repo")]:
                path = Path(name)
                self.uploaded[path.name] = path.read_bytes()
                self.assets.append({"name": path.name, "state": "uploaded", "size": path.stat().st_size,
                                    "digest": None if self.no_remote_digest else "sha256:" + digest(path)})
            if self.wrong_upload:
                self.assets[0]["digest"] = "sha256:" + "0" * 64
            if self.interrupted_create:
                raise publication.ReleaseError("Unknown network outcome")
            if self.wrong_tag_after_create:
                self.tag_reference["object"]["sha"] = "c" * 40
            if self.change_workflows_after_draft:
                self.main_workflow_tree = "e" * 40
            return b""
        if arguments[:4] == ["gh", "api", "--method", "POST"]:
            if self.concurrent_tag:
                raise publication.ReleaseError("Tag was concurrently created (HTTP 422)")
            request = json.loads(input_data)
            if request != {"ref": "refs/tags/v0.1.0", "sha": self.source}:
                raise AssertionError("Unexpected tag creation request")
            self.tag_reference = {"object": {"type": "commit", "sha": self.source}}
            return b""
        if arguments[:3] == ["gh", "release", "download"]:
            directory = Path(arguments[arguments.index("--dir") + 1])
            directory.mkdir(exist_ok=True)
            name = arguments[arguments.index("--pattern") + 1]
            (directory / name).write_bytes(self.uploaded[name])
            return b""
        if arguments[:4] == ["gh", "api", "--method", "PATCH"]:
            if arguments[4] != "repos/test/project/releases/99" or json.loads(input_data) != {"draft": False}:
                raise AssertionError("Unexpected release publication request")
            self.release["draft"] = False
            return b""
        raise AssertionError("Unexpected command: " + repr(arguments))

    def execute(self, *, check=False, environment=None):
        with patch.dict(os.environ, environment or self.environment, clear=True), \
                patch("sys.argv", ["publish_release.py"] + (["--check"] if check else [])), \
                patch.object(publication, "GitHub", return_value=self.api), \
                patch.object(publication, "command", side_effect=self.command), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            return publication.main(), output.getvalue()


class PublicationTests(unittest.TestCase):
    def mutation_commands(self, fixture):
        return [command for command in fixture.commands
                if command[:3] == ["gh", "release", "create"]
                or command[:4] in (["gh", "api", "--method", "POST"], ["gh", "api", "--method", "PATCH"])]

    def assert_no_mutation(self, fixture):
        status, output = fixture.execute()
        self.assertEqual(status, 1, output)
        self.assertEqual(self.mutation_commands(fixture), [])

    def test_complete_preflight_is_read_only(self):
        fixture = PublicationFixture()
        status, output = fixture.execute(check=True)
        self.assertEqual(status, 0, output)
        self.assertIsNone(fixture.release)
        self.assertEqual(self.mutation_commands(fixture), [])

    def test_success_uploads_all_verified_files_as_draft_before_publication(self):
        fixture = PublicationFixture()
        status, output = fixture.execute()
        self.assertEqual(status, 0, output)
        create = next(command for command in fixture.commands if command[:3] == ["gh", "release", "create"])
        self.assertIn("--draft", create)
        self.assertEqual(create[create.index("--target") + 1], fixture.source)
        self.assertEqual(set(fixture.uploaded), set(TARGET_FILES.values()) | {"SHA256SUMS", "release-evidence.json"})
        self.assertIs(fixture.release["draft"], False)
        published = [command for command in fixture.commands if command[:4] == ["gh", "api", "--method", "PATCH"]]
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0][4], "repos/test/project/releases/99")

    def test_wrong_run_identity_and_unsuccessful_build_never_mutate(self):
        for field, value in (("id", 124), ("workflow_id", 10), ("path", ".github/workflows/build.yml"),
                             ("event", "pull_request"), ("head_branch", "codex/other"),
                             ("head_sha", "c" * 40), ("status", "in_progress"), ("conclusion", "failure"),
                             ("head_repository", {"full_name": "other/project"})):
            with self.subTest(field=field):
                fixture = PublicationFixture()
                fixture.run[field] = value
                self.assert_no_mutation(fixture)

    def test_expired_missing_duplicate_or_wrong_source_artifacts_never_mutate(self):
        for mutation in ("expired", "missing", "duplicate", "source"):
            with self.subTest(mutation=mutation):
                fixture = PublicationFixture()
                if mutation == "expired":
                    fixture.artifacts["artifacts"][0]["expired"] = True
                elif mutation == "missing":
                    fixture.artifacts["artifacts"].pop()
                elif mutation == "duplicate":
                    fixture.artifacts["artifacts"][0]["name"] = fixture.artifacts["artifacts"][1]["name"]
                else:
                    fixture.artifacts["artifacts"][0]["workflow_run"]["head_sha"] = "c" * 40
                self.assert_no_mutation(fixture)

    def test_ci_manifest_and_exact_downloaded_bytes_are_required(self):
        for field in ("wrong_manifest", "wrong_download"):
            with self.subTest(field=field):
                fixture = PublicationFixture()
                setattr(fixture, field, True)
                self.assert_no_mutation(fixture)

    def test_unknown_physical_and_unapproved_sound_distribution_never_mutate(self):
        for gate in ("physical", "sound"):
            with self.subTest(gate=gate):
                fixture = PublicationFixture()
                if gate == "physical":
                    fixture.evidence["targets"]["macOS-Intel"]["physical"]["checks"]["offline_dictation"]["status"] = "unknown"
                else:
                    fixture.evidence["sound_redistribution"]["status"] = "unknown"
                self.assert_no_mutation(fixture)

    def test_owner_sound_decision_is_accepted_by_publication_preflight(self):
        fixture = PublicationFixture()
        root = Path(__file__).resolve().parents[1]
        owner = json.loads((root / "docs/sound-distribution-owner-decision-2026-10-03.json").read_text())
        fixture.evidence.update(owner)
        status, output = fixture.execute(check=True)
        self.assertEqual(status, 0, output)
        self.assertEqual(self.mutation_commands(fixture), [])
        self.assertEqual(fixture.evidence["sound_redistribution"]["status"], "unknown")
        fixture.evidence["targets"]["macOS-Intel"]["physical"]["checks"]["offline_dictation"]["status"] = "unknown"
        self.assert_no_mutation(fixture)

    def test_unmerged_source_and_existing_tag_or_draft_are_rejected(self):
        for field in ("ancestor", "existing_tag", "existing_release"):
            with self.subTest(field=field):
                fixture = PublicationFixture()
                setattr(fixture, field, field != "ancestor")
                self.assert_no_mutation(fixture)

    def test_tag_and_run_inputs_cannot_be_paths_or_shell_code(self):
        for tag, run_id in (("../v0.1.0", "123"), ("v0.1.0; echo bad", "123"),
                            ("v0.1.0", "123 --repo other/project"), ("v0.1.0", "$(echo bad)")):
            with self.subTest(tag=tag, run_id=run_id):
                with self.assertRaises(publication.ReleaseError):
                    publication.validate_inputs(tag, run_id, "test/project", PublicationFixture().environment)

    def test_non_main_or_non_manual_context_is_rejected_before_any_command(self):
        for field, value in (("GITHUB_REF", "refs/heads/codex/other"), ("GITHUB_EVENT_NAME", "push")):
            with self.subTest(field=field):
                fixture = PublicationFixture()
                environment = fixture.environment
                environment[field] = value
                status, output = fixture.execute(environment=environment)
                self.assertEqual(status, 1, output)
                self.assertEqual(fixture.commands, [])
                self.assertEqual(self.mutation_commands(fixture), [])

    def test_tag_must_match_both_package_source_versions(self):
        fixture = PublicationFixture()
        fixture.package_version = "0.2.0"
        self.assert_no_mutation(fixture)

    def test_changed_default_branch_or_incomplete_remote_tree_never_mutate(self):
        for field, value in (("default_branch", "other"), ("truncated_tree", True)):
            with self.subTest(field=field):
                fixture = PublicationFixture()
                setattr(fixture, field, value)
                self.assert_no_mutation(fixture)
                self.assertFalse(any(command[:3] == ["gh", "run", "download"] for command in fixture.commands))

    def test_candidate_workflow_mismatch_fails_before_downloads_or_tag_creation(self):
        fixture = PublicationFixture()
        fixture.main_workflow_tree = "e" * 40
        self.assert_no_mutation(fixture)
        self.assertFalse(any(command[:3] == ["gh", "run", "download"] for command in fixture.commands))

    def test_workflows_changed_during_download_fail_before_atomic_tag_creation(self):
        fixture = PublicationFixture()
        fixture.change_workflows_after_download = True
        self.assert_no_mutation(fixture)
        self.assertTrue(any(command[:3] == ["gh", "run", "download"] for command in fixture.commands))

    def test_workflows_changed_after_draft_creation_keep_the_release_private(self):
        fixture = PublicationFixture()
        fixture.change_workflows_after_draft = True
        status, output = fixture.execute()
        self.assertEqual(status, 1, output)
        self.assertIn("new signed candidate", output)
        self.assertIs(fixture.release["draft"], True)
        self.assertFalse(any(command[:4] == ["gh", "api", "--method", "PATCH"] for command in fixture.commands))

    def test_ambiguous_and_symbolic_link_candidate_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / TARGET_FILES["Windows-x64"]).write_bytes(b"synthetic")
            (directory / "release-manifest.json").write_text("{}")
            nested = directory / "nested"
            nested.mkdir()
            duplicate = nested / "release-manifest.json"
            duplicate.write_text("{}")
            with self.assertRaisesRegex(publication.ReleaseError, "duplicate"):
                publication.candidate_files(directory, "Windows-x64")
            duplicate.unlink()
            try:
                (nested / "link").symlink_to(directory / "release-manifest.json")
            except OSError:
                self.skipTest("This host does not permit creation of symbolic links")
            with self.assertRaisesRegex(publication.ReleaseError, "symbolic link"):
                publication.candidate_files(directory, "Windows-x64")

    def test_corrupt_uploaded_asset_keeps_release_private(self):
        fixture = PublicationFixture()
        fixture.wrong_upload = True
        status, output = fixture.execute()
        self.assertEqual(status, 1, output)
        self.assertIs(fixture.release["draft"], True)
        self.assertFalse(any(command[:4] == ["gh", "api", "--method", "PATCH"] for command in fixture.commands))

    def test_unknown_create_outcome_is_never_retried_or_published(self):
        fixture = PublicationFixture()
        fixture.interrupted_create = True
        status, output = fixture.execute()
        self.assertEqual(status, 1, output)
        self.assertIn("No automatic retry", output)
        self.assertEqual(sum(command[:3] == ["gh", "release", "create"] for command in fixture.commands), 1)
        self.assertIs(fixture.release["draft"], True)

    def test_concurrent_tag_creation_fails_atomically_before_creating_a_draft(self):
        fixture = PublicationFixture()
        fixture.concurrent_tag = True
        status, output = fixture.execute()
        self.assertEqual(status, 1, output)
        self.assertIn("No automatic retry", output)
        self.assertIsNone(fixture.release)
        self.assertEqual(sum(command[:4] == ["gh", "api", "--method", "POST"] for command in fixture.commands), 1)
        self.assertFalse(any(command[:3] == ["gh", "release", "create"] for command in fixture.commands))

    def test_wrong_tag_is_rejected_while_the_release_is_still_a_draft(self):
        fixture = PublicationFixture()
        fixture.wrong_tag_after_create = True
        status, output = fixture.execute()
        self.assertEqual(status, 1, output)
        self.assertIs(fixture.release["draft"], True)
        self.assertFalse(any(command[:4] == ["gh", "api", "--method", "PATCH"] for command in fixture.commands))

    def test_missing_github_digest_falls_back_to_downloading_exact_draft_bytes(self):
        fixture = PublicationFixture()
        fixture.no_remote_digest = True
        status, output = fixture.execute()
        self.assertEqual(status, 0, output)
        self.assertEqual(sum(command[:3] == ["gh", "release", "download"] for command in fixture.commands), 10)


if __name__ == "__main__":
    unittest.main()
