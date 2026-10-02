# Release preparation

This repository is preparing release candidates. It is not ready for a broad public release.
The owner currently has no Macs or public signing certificates. No actual public signing or
Apple notarization has been performed. The personal installation in
`C:\Users\batae\Documents\WhisperLocal` is outside this work.

## Current evidence, 2026-10-01

- The downloader source suite ran 199 tests: 198 passed, one Mac-only skip before the release additions.
- The combined source suite ran 210 tests: 209 passed, one Mac-only skip after the release additions.
- A live Windows microphone smoke captured 5760 samples and passed stream abort/reopen.
  It discarded audio and did not transcribe or save it.
- The native Windows focus/clipboard fixture timed out before identifying its target and before
  any paste. Its real paste/focus/clipboard result remains **unknown**; no automatic retry occurred.
- Source and isolated unsigned Windows portable worker checks passed CPU and NVIDIA CUDA
  model unload/reload and memory lifecycle, using an existing checksum-pinned base model and
  cached public JFK fixture. The portable GUI smoke also passed. No model was downloaded.
- Current [three-platform CI run 36900622996](https://github.com/bataevabdullah2009-pixel/whisper-local/actions/runs/36900622996)
  did not start any job because GitHub reported failed account payments or a spending limit.
  Current Windows, Apple Silicon and Intel CI validation is **blocked** until the owner resolves billing.
- Clean Windows installation, a complete user dictation/paste workflow, both physical Mac types,
  public signatures and notarization remain **unknown**. Inno Setup is not installed here, and
  the shared installer AppId/registry was not exercised on this development computer.

The machine-readable [current record](release-readiness-2026-10-01.json) preserves these partial
observations separately from the release gates. It intentionally cannot pass the readiness check.
Code checks and unsigned portable smoke do not certify a future signed installer with different bytes.

## Two separate workflows

**Desktop builds** continues to test and package unsigned Windows previews and ad-hoc Mac previews
on pull requests. **Signed release candidates** is a separate manual workflow in
`.github/workflows/release.yml`. It builds Windows x64, macOS Apple Silicon and macOS Intel,
runs tests and packaged offline smoke, and uploads verified signed candidates and manifests.
It fails when credentials, signing, timestamps, notarization or verification are missing or fail.
It never publishes a GitHub Release or treats physical checks as passed.

GitHub must have this workflow on the repository's default branch before `workflow_dispatch` is
available. The current default is `main`; preparing a PR does not make this manual workflow immediately
dispatchable. Integrate it into the default branch only with the owner's explicit merge instruction.
Billing also needs to allow hosted jobs to start.

Before adding credentials, create the **release-signing** GitHub environment and configure
reviewers and explicit **selected branches and tags** that may use signing credentials.
Do not choose “Protected branches only” without actual branch protections: GitHub allows all
branches when none are protected. Keep credentials in environment secrets. No signing secret
is available to the ordinary PR workflow. Each platform fails closed on absent credentials.

## Windows signing setup

The prepared implementation supports a valid, publicly trusted, code-signing certificate with
a private key that its provider permits exporting as PFX. Provision these environment secrets:

| Secret | Value |
| --- | --- |
| `WINDOWS_CERTIFICATE_PFX_BASE64` | Base64 of the permitted certificate/private-key PFX |
| `WINDOWS_CERTIFICATE_PASSWORD` | Its password |

Certificate acquisition requires publisher identity validation by a public CA or signing service.
Modern providers may require a hardware or cloud protected key that cannot be exported as PFX.
For those providers, adapt the signing helper to the provider's supported SignTool/HSM/cloud
integration before using this workflow. The current PFX path does not implement that integration
or provision a paid service. [Microsoft Artifact Signing](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart)
is one documented cloud option; eligibility and account setup are owner tasks.

Self-signed certificates, including the local `JARVIS PC Dev Code Signing` development identity,
are rejected. The helper imports only one code-signing end identity on a temporary GitHub-hosted
runner, validates its public trust chain and uses SHA-256 plus an RFC 3161 timestamp.
The default TSA is DigiCert's documented `http://timestamp.digicert.com`; the timestamp token
is cryptographically verified. HTTP and HTTPS provider endpoints are supported. Override it with
the environment variable `WHISPERLOCAL_WINDOWS_TIMESTAMP_URL` for your signing provider.
It does not pass the PFX password to SignTool. The selected private key is removed in an
`always()` cleanup step; the decoded PFX is deleted immediately after import.

The GUI and worker are signed before Inno Setup. The `ReleaseSigning` define enables Inno's
SignTool hook and `SignedUninstaller`, so the installer, uninstaller and temporary installer
copies are signed. Every signing callback verifies the selected signer and timestamp. The fresh
runner installation smoke verifies installed GUI/helper/uninstaller signatures before testing
startup without host Python and uninstalling. That is a hosted smoke, not a clean physical user test.

## macOS signing and notarization setup

Enroll the publisher in the Apple Developer Program, create a **Developer ID Application**
certificate, export the certificate and private key from Keychain as a password-protected P12,
and create a permitted App Store Connect team API key for notarization. Provision:

| Secret | Value |
| --- | --- |
| `MACOS_CERTIFICATE_P12_BASE64` | Base64 of the Developer ID Application P12 |
| `MACOS_CERTIFICATE_PASSWORD` | P12 password |
| `MACOS_NOTARY_KEY_P8_BASE64` | Base64 of the App Store Connect API private key `.p8` |
| `MACOS_NOTARY_KEY_ID` | API key ID |
| `MACOS_NOTARY_ISSUER_ID` | Team API issuer UUID |

The same publisher identity signs both native architectures. The helper accepts exactly one
valid Developer ID Application identity, creates a temporary keychain and restores the original
keychain search list on cleanup. Secrets stay outside the checkout and artifact uploads. Neither
commands containing credentials nor secret-tool output are printed on errors.

PyInstaller signs bundled Python, Qt, native whisper.cpp and other Mach-O dependencies with
the same identity, hardened runtime and secure timestamps. The GUI needs three documented
entitlements: microphone/audio input, PyObjC executable callback stubs (`allow-jit`), and the
existing sounddevice CFFI ABI callbacks (`allow-unsigned-executable-memory`). The worker is
signed without these GUI resource/exception entitlements. Library validation remains enabled.
The packaged GUI smoke allocates CFFI and PyObjC callbacks without opening a microphone,
creating an event tap, or doing network operations; actual permissions and recording still
require physical checks.

The workflow signs the app and the final DMG, submits the outermost distributed DMG with
`notarytool --wait`, requires an explicit **Accepted** result, staples its ticket, validates the
ticket, and assesses the exact DMG with Gatekeeper. Rejected, pending, timed-out or malformed
responses cannot produce an uploaded release candidate. A local app outside the DMG is not
mistakenly assessed as a separately stapled distribution. Browser-download quarantine, copying
the app to Applications, and launching it while offline remain physical release gates.

## Collecting physical evidence

Use willing testers with a clean Windows user without Python, an Apple Silicon Mac, and an
Intel Mac. Hosted runners, virtualized Mac CI, Rosetta on Apple Silicon, and this Windows
computer do not substitute for the two physical Mac architectures.

1. Resolve CI billing and signing setup; build a signed candidate from the exact reviewed commit.
   Download the actual installer/DMG through a browser and retain normal quarantine/Gatekeeper
   protections. Record the provided manifest, SHA-256, OS version, hardware/backend, tester and date.
2. Install as a fresh user, handle microphone and Accessibility/Input Monitoring prompts, and
   run the complete dictation flow. On Macs, launch the browser-downloaded/copied app offline
   after setup to verify stapling and Gatekeeper without development-machine trust/cache.
3. Run each scenario five times: default hotkey and hold/toggle modes, Esc, focus changes,
   clipboard restoration, browser/Notepad or TextEdit/VS Code/terminal paste, real microphone
   unplug/reconnect, real sleep/wake, cold load/idle unload, dictionary and cleanup.
4. Interrupt a model download, exit and restart the app, resume the existing bytes and finish
   verification; test network outage and insufficient disk. Then disable network and dictate.
   See [downloads](DOWNLOADS.md), [reliability](RELIABILITY.md), [dictation](DICTATION.md),
   [memory](MEMORY.md), [Metal](MAC-METAL.md), [dictionary](DICTIONARY.md) and [cleanup](TEXT-CLEANUP.md).
5. On Windows check CPU-only plus NVIDIA and a failed GPU warmup fallback. On each Mac check
   its native package, CPU and supported Metal/fallback. Uninstall without removing another
   personal/public installation or its data.

Record outcome and non-sensitive diagnostic metadata. Do not attach private microphone audio,
dictation text or clipboard contents. A blocked/skipped scenario stays `unknown` or `failed`.
The existing sounds and capsule are preserved. [Sound provenance](../assets/sounds/SOURCES.md)
does not grant redistribution permission for the Flow cues; retain a separate **unknown**
redistribution gate until the owner obtains permission or explicitly approves another resolution.

Copy [release-evidence.template.json](release-evidence.template.json) to an owner evidence file.
Insert the three exact signed CI manifests and fill the physical records only after real tests.
Run this local read-only validator with all three distributed candidate files:

```powershell
python scripts/release_readiness.py owner-release-evidence.json --artifact-dir downloaded-candidates
```

It exits nonzero for absent public-signature/accepted-notary manifests, wrong hashes/commit,
missing physical evidence, or unverified sound redistribution. Completed evidence is still
subject to owner review before publication. No current record passes, and no release is published.

## Tool references

- [Microsoft SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool)
- [DigiCert RFC 3161 timestamp endpoint](https://knowledge.digicert.com/solution/troubleshooting-timestamping-problems)
- [Inno SignTool](https://jrsoftware.org/ishelp/topic_setup_signtool.htm) and
  [SignedUninstaller](https://jrsoftware.org/ishelp/topic_setup_signeduninstaller.htm)
- [PyInstaller macOS signing](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing)
- [Apple distribution packaging](https://developer.apple.com/documentation/xcode/packaging-mac-software-for-distribution)
  and [notarytool authentication](https://developer.apple.com/documentation/technotes/tn3147-migrating-to-the-latest-notarization-tool)
- [Apple audio input entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.device.audio-input),
  [PyObjC signing](https://pyobjc.readthedocs.io/en/latest/notes/codesigning.html), and
  [CFFI callback requirements](https://cffi.readthedocs.io/en/stable/using.html#callbacks)
- [GitHub environment protection](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
