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
   Prepare the CSR and protected P12 on Windows using the procedure below, or use Apple's
   [Keychain/Xcode export procedure](https://developer.apple.com/documentation/Xcode/sharing-your-teams-signing-certificates)
   if a Mac is already available. Retain the corresponding private key; the downloaded `.cer`
   alone cannot sign packages.
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

## Prepare the CSR and P12 on Windows

Apple's [certificate technical note TN3161](https://developer.apple.com/documentation/technotes/tn3161-inside-code-signing-certificates)
describes the public-key CSR, matching Apple-issued certificate and PKCS#12 identity, including
OpenSSL conversion. Together with the [OpenSSL CSR](https://docs.openssl.org/3.5/man1/openssl-req/)
and [PKCS#12 commands](https://docs.openssl.org/3.5/man1/openssl-pkcs12/), this supports the
following standards-based Windows procedure. This is an inference from the documented formats;
Apple's account walkthrough uses Keychain. No publisher keys were generated, certificate issued
by Apple or P12 imported on a Mac while preparing this guide.

Use an interactive PowerShell terminal as the owner. Git for Windows can provide OpenSSL at the
path shown below; confirm that it exists and use a trusted OpenSSL 3 installation. Choose a fresh
private folder outside the repository, protect it with your Windows account permissions and keep
an encrypted backup of the key. Use a new request folder name for another attempt. Run each block
separately and stop on any exception or nonzero exit; do not continue with partial output.

```powershell
$credentialOpenSsl = 'C:\Program Files\Git\usr\bin\openssl.exe'
$credentialDirectory = Join-Path $env:LOCALAPPDATA 'WhisperLocal-Credentials\DeveloperID-request-01'
& {
    if (-not (Test-Path -LiteralPath $credentialOpenSsl -PathType Leaf)) { throw 'OpenSSL not found.' }
    if (Test-Path -LiteralPath $credentialDirectory) { throw 'Choose a new request folder; nothing is overwritten.' }
    New-Item -ItemType Directory -Path $credentialDirectory -ErrorAction Stop | Out-Null
    Push-Location -LiteralPath $credentialDirectory -ErrorAction Stop
    try {
        & $credentialOpenSsl req -new -newkey rsa:2048 -sha256 -keyout developer-id-private.pem -out developer-id.certSigningRequest
        if ($LASTEXITCODE -ne 0) { throw 'Key/CSR creation failed; stop here.' }
        & $credentialOpenSsl req -in developer-id.certSigningRequest -verify -noout
        if ($LASTEXITCODE -ne 0) { throw 'CSR verification failed; stop here.' }
    } finally { Pop-Location }
}
```

OpenSSL prompts for a private-key encryption password and the request identity fields. Supply
your own identity details and retain the password. The command creates an RSA-2048 key and a
SHA-256 PKCS#10 request; the OpenSSL 3 default encrypts the key. Do not add `-nodes`/`-noenc`
or place passwords in command arguments, environment variables, chat or terminal recordings.
CSR verification checks the request's signature; it does not grant a public certificate.

As Account Holder, use Apple's website: Certificates, Identifiers & Profiles → Certificates →
add → **Developer ID Application**. Upload only `developer-id.certSigningRequest`, never the
private key or its password. Download the actual Apple-issued `.cer` and copy it to the request
folder as `developerID_application.cer`. Apple enrollment, identity approval and issuance remain
owner actions. A locally self-signed certificate cannot replace this step.

Then convert Apple's DER certificate and combine it with the retained encrypted key:

```powershell
& {
    Push-Location -LiteralPath $credentialDirectory -ErrorAction Stop
    try {
        foreach ($credentialOutput in @('developerID_application.pem', 'developer-id.p12')) {
            if (Test-Path -LiteralPath $credentialOutput) { throw 'Output already exists; stop without overwriting it.' }
        }
        & $credentialOpenSsl x509 -inform DER -in developerID_application.cer -out developerID_application.pem
        if ($LASTEXITCODE -ne 0) { throw 'Certificate conversion failed; stop here.' }
        & $credentialOpenSsl pkcs12 -export -inkey developer-id-private.pem -in developerID_application.pem -out developer-id.p12 -name 'Developer ID Application' -iter 100000
        if ($LASTEXITCODE -ne 0) { throw 'P12 export failed; stop here.' }
        & $credentialOpenSsl pkcs12 -in developer-id.p12 -info -noout
        if ($LASTEXITCODE -ne 0) { throw 'P12 inspection failed; stop here.' }
    } finally { Pop-Location }
}
```

Export prompts for the private-key password and a new P12 password; use the latter for
`MACOS_CERTIFICATE_PASSWORD`. OpenSSL checks that the certificate matches the private key at
export. `-info -noout` inspects the P12 without printing its key or certificate contents. These
checks prove local consistency, not genuine Apple issuance, trusted-chain validation or Mac import.

The example retains OpenSSL 3's AES-256-CBC/PBKDF2 encryption and SHA-256 integrity MAC, with
100,000 iterations. This format's import into the actual Mac signing runner remains unverified.
Do not silently switch to `-legacy` or disable encryption/MAC checks after an import failure.
The real signed-candidate workflow must import this P12 and find a valid Apple Developer ID
Application identity before signing can pass. The P12 then supplies
`MACOS_CERTIFICATE_P12_BASE64`; keep that base64 and both passwords out of logs and artifacts.
No borrowed/rented Mac is required for this local credential preparation, while signing,
notarization and physical tests still have their separate requirements above and below.

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
provide a remote desktop path. A dedicated rented Mac can support GUI, permission-dialog and
paste checks in a remote session. Describe those results with their actual remote
input/environment scope. Credential preparation can use the Windows procedure above.

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
