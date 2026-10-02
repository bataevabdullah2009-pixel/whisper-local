# Mac release from a Windows workstation

The owner can build, sign and notarize both Mac packages from Windows using GitHub Actions.
Completing microphone, permissions and paste checks requires access to a physical Apple Silicon
Mac and a physical Intel Mac running macOS 14 or later. Ownership of those Macs is not required:
willing testers can run the checks locally.

This guide does not purchase services, create accounts, enroll an Apple publisher, provision
credentials or claim that a physical check has passed.

## Build and sign through GitHub

The manual **Signed release candidates** workflow in
[release.yml](../.github/workflows/release.yml) uses `macos-15` for ARM64 and
`macos-15-intel` for Intel. Those native architectures are available on
[GitHub-hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
The workflow builds each package, signs it, submits its DMG to Apple, requires an `Accepted`
response, staples the ticket, assesses the distributed DMG and uploads the manifest with its
SHA-256. Hosted jobs provide build/signature/fixture evidence; they do not establish physical
microphone, keyboard, permissions, paste or sleep behavior.

The workflow must be present on the repository's default branch, hosted jobs must be allowed
to start, and the `release-signing` GitHub environment must restrict which reviewed branches or
tags may use the credentials. Follow [RELEASE.md](RELEASE.md) for the complete signing setup.

The publisher must perform these identity and account steps:

1. [Enroll in the Apple Developer Program](https://developer.apple.com/programs/enroll/).
   Enrollment is available through the web. An individual needs an Apple Account with two-factor
   authentication, their legal identity, contact details and the legal age of majority. Apple
   verifies enrollment; the owner accepts the agreement and pays the annual membership fee
   (99 USD, with regional pricing). Organization enrollment has additional verification.
2. As Account Holder, create a
   [Developer ID Application certificate](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/).
   Generate the CSR and retain its corresponding private key. A borrowed or dedicated rented
   Mac can prepare the CSR and
   [export the signing identity as a password-protected P12](https://developer.apple.com/documentation/Xcode/sharing-your-teams-signing-certificates).
   Export both certificate and private key; the downloaded `.cer` alone cannot sign packages.
   The same publisher identity signs ARM64 and Intel. This DMG distribution does not need a
   Developer ID Installer certificate, which is for installer packages.
3. An App Store Connect administrator creates a permitted **team API key** for notarization,
   retains the downloaded `.p8`, and records its Key ID and Issuer ID.
   [Individual API keys cannot use notaryTool](https://developer.apple.com/documentation/AppStoreConnectAPI/creating-api-keys-for-app-store-connect-api).
4. Put the following values in environment secrets for `release-signing`. Keep private keys,
   passwords and their base64 encodings out of the repository, chat, diagnostic reports and
   uploaded artifacts.

| Environment secret | Value |
| --- | --- |
| `MACOS_CERTIFICATE_P12_BASE64` | Base64 of the Developer ID Application P12 |
| `MACOS_CERTIFICATE_PASSWORD` | P12 password |
| `MACOS_NOTARY_KEY_P8_BASE64` | Base64 of the team API private key `.p8` |
| `MACOS_NOTARY_KEY_ID` | Team API Key ID |
| `MACOS_NOTARY_ISSUER_ID` | Team API Issuer UUID |

Then dispatch the workflow for the reviewed source commit from Windows. Apple documents
[automated notarization and ticket stapling](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).
Credentials and successful notarization remain requirements; ad-hoc signatures do not replace
the publisher identity. The workflow produces candidates and does not publish a release.

## Arrange two physical testers

Use one tester with an Apple Silicon Mac and another with an Intel Mac, each on macOS 14+.
The tester needs a microphone connected directly to that Mac, a local keyboard, permission to
create a fresh user/install the app, and permission to disconnect the microphone, sleep/wake
the machine and disconnect its network after explicit model setup. One person with both
architectures can perform both records. Rosetta on Apple Silicon does not supply an Intel Mac
hardware record.

Send each tester their architecture's exact signed candidate DMG, its `release-manifest.json`,
the source commit and the successful signing workflow URL. The two DMG names are
`WhisperLocal-macOS-Apple-Silicon.dmg` and `WhisperLocal-macOS-Intel.dmg`.

1. Download the actual DMG through a browser. Compare its SHA-256 with the signed manifest,
   using `shasum -a 256 /path/to/candidate.dmg` on Mac or `Get-FileHash -Algorithm SHA256`
   on Windows. Confirm the manifest's full source commit and target. Do not substitute an
   unsigned preview or a later rebuild with different bytes.
2. Retain normal download quarantine and Gatekeeper protections, copy the app to Applications
   and use a fresh user. Verify clean install, first dictation and uninstall. After explicit
   model download/setup, launch and dictate while the Mac's network is disconnected to test
   offline Gatekeeper behavior and transcription.
3. Run each scenario five times: Microphone and Accessibility/Input Monitoring permissions;
   default right Option and custom hotkeys; hold/toggle modes and Esc; TextEdit/browser/editor/
   terminal paste, clipboard restoration and target focus changes; physical microphone
   unplug/reconnect and actual sleep/wake; cold load, idle unload and Free memory; dictionary,
   cleanup, compact capsule and existing sound choices.
4. Test interrupted download across restart, network outage and insufficient disk in a
   controlled test environment. Verify CPU, supported native Metal and explicit fallback.
   Follow the details in [DICTATION.md](DICTATION.md), [MAC-METAL.md](MAC-METAL.md),
   [RELIABILITY.md](RELIABILITY.md), [DOWNLOADS.md](DOWNLOADS.md), [MEMORY.md](MEMORY.md),
   [DICTIONARY.md](DICTIONARY.md) and [TEXT-CLEANUP.md](TEXT-CLEANUP.md).
5. Record tester, date, macOS version, architecture/chip, microphone type, backend, package
   hash, source commit, scenario outcome and non-sensitive failure/timing metadata. Report
   failures and skipped/blocked checks as `failed` or `unknown`.

Record speech locally and discard it after each operation. Do not retain or upload microphone
audio, transcription content or clipboard contents, including in screenshots or session
recordings. Do not redirect the Windows microphone into a remote Mac: that sends microphone
audio over the network and cannot satisfy this app's offline-recording requirement.

## What remote rental can cover

[MacinCloud Dedicated](https://www.macincloud.com/pages/dedicated.html) advertises Apple Silicon
and Intel plans with administrator access; availability and pricing must be confirmed before
purchase. Its [Windows RDP instructions](https://support.macincloud.com/support/solutions/articles/8000079292-how-to-connect-to-macincloud-dedicated-server-using-rdp)
provide a remote desktop path. A dedicated rented Mac can prepare the CSR/P12 and support
GUI, permission-dialog and paste checks in a remote session. Describe those results with their
actual remote input/environment scope.

The vendor's [audio documentation](https://support.macincloud.com/support/solutions/articles/8000057678-is-audio-supported-with-macincloud-servers-)
documents sound playback, but does not establish a directly attached microphone, technician
USB disconnect/reconnect or physical sleep/wake service. Rental alone therefore does not
close all gates. To use rented hardware for the complete physical record, arrange a willing
onsite tester with the attached microphone, local keyboard and control of those operations,
including offline testing. Otherwise use the two physical testers above and leave uncovered
checks `unknown`.

## Bind the results to the distributed bytes

Copy [release-evidence.template.json](release-evidence.template.json) to an owner evidence file.
Set the top-level `source_commit` to the reviewed 40-character commit. For each target, copy the
exact CI manifest into `signed_manifest`; set `physical.artifact_sha256` and
`physical.source_commit` to that manifest's values. Fill tester/date/OS/hardware and every
physical check only from observed results. A new package hash requires new evidence for that
package. A manifest or filled template alone does not prove that a test occurred.

Collect the Windows physical record too, and put all three candidate files together. From the
repository root on Windows, run:

```powershell
python scripts/release_readiness.py owner-release-evidence.json --artifact-dir downloaded-candidates
```

The [readiness validator](../scripts/release_readiness.py) checks all three hashes and source
bindings, verified signatures/timestamps, accepted/stapled Mac notarization, all recorded
physical gates and sound redistribution evidence. It returns nonzero while any requirement
is incomplete. Review the actual evidence before publication; the validator cannot observe
the tester's hardware or manufacture an outcome. See [RELEASE.md](RELEASE.md) for the separate
sound permission and Windows signing requirements.
