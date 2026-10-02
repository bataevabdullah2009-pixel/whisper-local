"""Publish one exact signed candidate only after committed owner evidence passes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

try:
    from scripts.release_manifest import TARGET_FILES, digest
    from scripts.release_readiness import readiness_errors
except ModuleNotFoundError:
    from release_manifest import TARGET_FILES, digest
    from release_readiness import readiness_errors


WORKFLOW_PATH = ".github/workflows/release.yml"
SHA_PATTERN = r"[0-9a-f]{40}"
TAG_PATTERN = r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


class ReleaseError(RuntimeError):
    pass


def command(arguments: list[str], *, input_data: bytes | None = None) -> bytes:
    result = subprocess.run(arguments, input=input_data, capture_output=True)
    if result.returncode:
        # Do not echo credentials, release evidence or arbitrary tool output.
        raise ReleaseError(f"{arguments[0]} command failed (exit {result.returncode})")
    return result.stdout


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise ReleaseError("Unexpected GitHub API redirect")


class GitHub:
    def __init__(self, repository: str):
        self.repository = repository
        self.opener = build_opener(NoRedirect())

    def get(self, endpoint: str, *, missing_ok: bool = False):
        token = os.environ.get("GH_TOKEN", "")
        if not token:
            raise ReleaseError("GH_TOKEN is required")
        request = Request(
            f"https://api.github.com/repos/{self.repository}/{endpoint}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
        )
        try:
            with self.opener.open(request, timeout=60) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code == 404 and missing_ok:
                return None
            raise ReleaseError(f"GitHub API request failed (HTTP {error.code})") from None

    def releases(self) -> list[dict]:
        releases = []
        page = 1
        while True:
            batch = self.get(f"releases?per_page=100&page={page}")
            if not isinstance(batch, list):
                raise ReleaseError("Invalid GitHub release list")
            releases.extend(batch)
            if len(batch) < 100:
                return releases
            page += 1

    def version_absent(self, tag: str) -> None:
        if self.get(f"git/ref/tags/{tag}", missing_ok=True) is not None:
            raise ReleaseError("Version tag already exists; it will never be replaced")
        # Listing also detects drafts, which a release-by-tag lookup can omit.
        if any(release.get("tag_name") == tag for release in self.releases()):
            raise ReleaseError("Version release already exists; it will never be replaced")


def validate_inputs(tag: str, run_id: str, repository: str, environment: dict) -> str:
    if not re.fullmatch(TAG_PATTERN, tag):
        raise ReleaseError("Release tag must be vMAJOR.MINOR.PATCH")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", run_id):
        raise ReleaseError("Candidate run ID must be a positive decimal integer")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ReleaseError("Invalid GitHub repository")
    commit = environment.get("GITHUB_SHA", "")
    if (environment.get("GITHUB_REF") != "refs/heads/main"
            or environment.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or environment.get("GITHUB_REPOSITORY", "").lower() != repository.lower()
            or not re.fullmatch(SHA_PATTERN, commit)):
        raise ReleaseError("Publication requires a manual workflow on this repository's main branch")
    return commit


def committed_file(commit: str, path: str) -> bytes:
    return command(["git", "show", f"{commit}:{path}"])


def validate_run(run: dict, workflow: dict, repository: str, run_id: str, source: str) -> None:
    if (not isinstance(run, dict) or not isinstance(workflow, dict)
            or run.get("id") != int(run_id) or run.get("workflow_id") != workflow.get("id")
            or workflow.get("path") != WORKFLOW_PATH or run.get("path") != WORKFLOW_PATH
            or run.get("name") != "Signed release candidates"
            or run.get("status") != "completed" or run.get("conclusion") != "success"
            or run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main"
            or run.get("head_sha") != source or not re.fullmatch(SHA_PATTERN, source)
            or any(not isinstance(run.get(field), dict)
                   or run[field].get("full_name", "").lower() != repository.lower()
                   for field in ("repository", "head_repository"))):
        raise ReleaseError("Candidate is not a successful exact signed workflow run from main")


def validate_artifacts(response: dict, run_id: str, source: str) -> None:
    if not isinstance(response, dict):
        raise ReleaseError("Invalid signed-candidate artifact list")
    artifacts = response.get("artifacts", [])
    expected = {f"WhisperLocal-signed-{target}" for target in TARGET_FILES}
    if (not isinstance(artifacts, list) or any(not isinstance(item, dict) for item in artifacts)
            or response.get("total_count") != len(expected) or len(artifacts) != len(expected)
            or {artifact.get("name") for artifact in artifacts} != expected
            or any(artifact.get("expired") is not False or not isinstance(artifact.get("workflow_run"), dict)
                   or artifact.get("workflow_run", {}).get("id") != int(run_id)
                   or artifact.get("workflow_run", {}).get("head_sha") != source
                   for artifact in artifacts)):
        raise ReleaseError("Exactly three nonexpired artifacts from the selected candidate run are required")


def candidate_files(directory: Path, target: str) -> tuple[Path, Path, Path | None]:
    files = []
    for path in directory.rglob("*"):
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise ReleaseError("Candidate artifact contains an unsafe path or symbolic link")
        if path.is_file():
            files.append(path)
    expected = {TARGET_FILES[target], "release-manifest.json"}
    if target != "Windows-x64":
        expected.add("notarization.json")
    if len(files) != len(expected) or {path.name for path in files} != expected:
        raise ReleaseError(f"{target}: unexpected or duplicate candidate files")
    by_name = {path.name: path for path in files}
    return by_name[TARGET_FILES[target]], by_name["release-manifest.json"], by_name.get("notarization.json")


def stage_candidate(directory: Path, target: str, evidence: dict, repository: str, run_id: str,
                    destination: Path) -> None:
    artifact, manifest_path, notary_path = candidate_files(directory, target)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_run = f"https://github.com/{repository}/actions/runs/{run_id}"
    if (not isinstance(manifest, dict) or manifest.get("verification_run") != expected_run
            or manifest != evidence.get("targets", {}).get(target, {}).get("signed_manifest")):
        raise ReleaseError(f"{target}: downloaded CI manifest does not match committed owner evidence")
    if notary_path is not None:
        notary = json.loads(notary_path.read_text(encoding="utf-8"))
        if (not isinstance(notary, dict) or not isinstance(notary.get("submission"), dict)
                or notary.get("sha256") != manifest.get("sha256") or notary.get("stapled") is not True
                or notary.get("submission", {}).get("status") != "Accepted"
                or notary.get("submission", {}).get("id") != manifest.get("notary_submission_id")):
            raise ReleaseError(f"{target}: notarization report does not match the signed candidate")
    shutil.copy2(artifact, destination / artifact.name)


def preflight(api: GitHub, tag: str, run_id: str, publication_commit: str, workspace: Path) -> tuple[dict, Path, Path]:
    if command(["git", "rev-parse", "HEAD"]).decode().strip() != publication_commit:
        raise ReleaseError("Checkout does not match the publication workflow commit")
    evidence_bytes = committed_file(publication_commit, f"docs/releases/{tag}.json")
    notes_bytes = committed_file(publication_commit, f"docs/releases/{tag}.md")
    evidence = json.loads(evidence_bytes.decode("utf-8"))
    if not isinstance(evidence, dict) or not notes_bytes.decode("utf-8").strip():
        raise ReleaseError("Committed owner evidence and nonempty release notes are required")
    # Check the existing physical and sound gates before downloading large packages.
    errors = readiness_errors(evidence)
    if errors:
        raise ReleaseError("Release evidence is incomplete:\n- " + "\n- ".join(errors))
    source = evidence["source_commit"]
    command(["git", "merge-base", "--is-ancestor", source, publication_commit])
    windows_version = re.findall(r'^\s*#define AppVersion "([^"]+)"\s*$',
                                 committed_file(source, "packaging/windows.iss").decode(), re.MULTILINE)
    mac_version = re.findall(r'\bversion="([^"]+)"', committed_file(source, "WhisperLocal.spec").decode())
    if windows_version != [tag[1:]] or mac_version != [tag[1:]]:
        raise ReleaseError("Version tag does not match both signed package source versions")
    workflow = api.get("actions/workflows/release.yml")
    run = api.get(f"actions/runs/{run_id}")
    validate_run(run, workflow, api.repository, run_id, source)
    validate_artifacts(api.get(f"actions/runs/{run_id}/artifacts?per_page=100"), run_id, source)
    api.version_absent(tag)
    assets = workspace / "assets"
    assets.mkdir()
    for target in TARGET_FILES:
        directory = workspace / target
        command(["gh", "run", "download", run_id, "--repo", api.repository,
                 "--name", f"WhisperLocal-signed-{target}", "--dir", str(directory)])
        stage_candidate(directory, target, evidence, api.repository, run_id, assets)
    errors = readiness_errors(evidence, assets)
    if errors:
        raise ReleaseError("Exact candidate validation failed:\n- " + "\n- ".join(errors))
    (assets / "release-evidence.json").write_bytes(evidence_bytes)
    (assets / "SHA256SUMS").write_text(
        "".join(f"{digest(assets / filename)}  {filename}\n" for filename in sorted(TARGET_FILES.values())),
        encoding="utf-8",
    )
    notes = workspace / "release-notes.md"
    notes.write_bytes(notes_bytes)
    return evidence, assets, notes


def verify_uploaded_assets(api: GitHub, release: dict, assets: Path, tag: str, workspace: Path) -> None:
    remote = api.get(f"releases/{release['id']}/assets?per_page=100")
    expected = {path.name: path for path in assets.iterdir()}
    if (not isinstance(remote, list) or len(remote) != len(expected)
            or {asset.get("name") for asset in remote} != set(expected)):
        raise ReleaseError("Draft release does not contain exactly the verified release assets")
    for asset in remote:
        local = expected[asset["name"]]
        if asset.get("state") != "uploaded" or asset.get("size") != local.stat().st_size:
            raise ReleaseError("Uploaded release asset is incomplete")
        remote_digest = asset.get("digest")
        if remote_digest:
            if remote_digest != "sha256:" + digest(local):
                raise ReleaseError("Uploaded release asset hash does not match verified bytes")
        else:
            directory = Path(tempfile.mkdtemp(prefix="remote-", dir=workspace))
            command(["gh", "release", "download", tag, "--repo", api.repository,
                     "--pattern", local.name, "--dir", str(directory)])
            downloaded = directory / local.name
            if downloaded.is_symlink() or not downloaded.is_file() or digest(downloaded) != digest(local):
                raise ReleaseError("Downloaded draft asset does not match verified bytes")


def publish(api: GitHub, tag: str, evidence: dict, assets: Path, notes: Path, workspace: Path) -> str:
    # Repeat the no-overwrite check immediately before the first remote mutation.
    api.version_absent(tag)
    try:
        # Creating a new ref is atomic: an existing/racing tag causes failure.
        # Never update or delete a ref, including after an uncertain API result.
        command(["gh", "api", "--method", "POST", f"repos/{api.repository}/git/refs", "--input", "-"],
                input_data=json.dumps({"ref": f"refs/tags/{tag}", "sha": evidence["source_commit"]}).encode())
        require_candidate_tag(api, tag, evidence["source_commit"])
        command(["gh", "release", "create", tag, *[str(path) for path in sorted(assets.iterdir())],
                 "--repo", api.repository, "--draft", "--verify-tag", "--target", evidence["source_commit"],
                 "--title", f"Whisper Local {tag}", "--notes-file", str(notes)])
        releases = [release for release in api.releases() if release.get("tag_name") == tag]
        if (len(releases) != 1 or releases[0].get("draft") is not True
                or releases[0].get("target_commitish") != evidence["source_commit"]):
            raise ReleaseError("Created draft does not identify the exact candidate commit")
        verify_uploaded_assets(api, releases[0], assets, tag, workspace)
        require_candidate_tag(api, tag, evidence["source_commit"])
        # Publish only the draft whose exact assets were inspected, by ID.
        command(["gh", "api", "--method", "PATCH", f"repos/{api.repository}/releases/{releases[0]['id']}",
                 "--input", "-"], input_data=b'{"draft": false}')
        release = api.get(f"releases/{releases[0]['id']}")
        require_candidate_tag(api, tag, evidence["source_commit"])
        if release.get("draft") is not False or release.get("tag_name") != tag:
            raise ReleaseError("Publication could not be confirmed at the exact candidate commit")
        verify_uploaded_assets(api, release, assets, tag, workspace)
        return release["html_url"]
    except Exception as error:
        raise ReleaseError(
            f"Publication interrupted for {tag}: {error}. Inspect the draft/release and tag in GitHub; "
            "the remote outcome may be unknown. No automatic retry or overwrite was performed."
        ) from None


def require_candidate_tag(api: GitHub, tag: str, source: str) -> None:
    reference = api.get(f"git/ref/tags/{tag}")
    if (not isinstance(reference, dict) or reference.get("object", {}).get("type") != "commit"
            or reference.get("object", {}).get("sha") != source):
        raise ReleaseError("Version tag does not identify the exact candidate commit")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="complete read-only preflight without publishing")
    args = parser.parse_args()
    try:
        tag = os.environ.get("RELEASE_TAG", "")
        run_id = os.environ.get("CANDIDATE_RUN_ID", "")
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        publication_commit = validate_inputs(tag, run_id, repository, os.environ)
        with tempfile.TemporaryDirectory(prefix="whisperlocal-release-") as temporary:
            workspace = Path(temporary)
            api = GitHub(repository)
            evidence, assets, notes = preflight(api, tag, run_id, publication_commit, workspace)
            if args.check:
                print(f"Preflight passed for {tag}, signed run {run_id}, source {evidence['source_commit']}.")
            else:
                print("Published verified signed release: " + publish(api, tag, evidence, assets, notes, workspace))
        return 0
    except (OSError, ValueError, TypeError, ReleaseError) as error:
        print(f"Release stopped: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
