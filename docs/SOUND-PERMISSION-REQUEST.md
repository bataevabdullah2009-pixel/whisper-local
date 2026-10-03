# Permission request for the retained Wispr Flow cues

Prepared on 2026-10-03. **Draft only: not sent; permission not received.**
The owner has confirmed that the current Wispr Flow sounds should remain. Their
files, the default `flow` choice, Console/Air and the recording capsule are unchanged.

## Recipient and sending

Suggested recipient: `support@wisprflow.ai`, asking them to route the request to the
person responsible for licensing these audio assets. This address is listed in the
[official support guide](https://docs.wisprflow.ai/articles/7837779518-getting-help-with-wispr-flow-reporting-flagging-and-contacting-support)
and as a legal/business contact in the [official store legal notice](https://store.wisprflow.ai/policies/legal-notice).
Contact verified on 2026-10-03; a support address is not proof of licensing authority.

The owner should review the text and send it from their own reply-capable email
address. No sender identity, paid agreement or approval to send is supplied by this
draft. Do not send private dictation, microphone recordings, logs or credentials.

## Email draft

**Subject:** Permission request: two Wispr Flow audio cues in Whisper Local

Hello Wispr team,

I maintain Whisper Local, an independent desktop dictation application for Windows
and macOS. Recording and transcription run locally. Our public project repository is:
https://github.com/bataevabdullah2009-pixel/whisper-local

I would like written permission to retain and redistribute the two short audio cues
referenced by your official web demo at https://wisprflow.ai/demo:

- Start: https://dl.dropbox.com/scl/fi/zv8278qh9ovwq0r89rch1/dictation-start.wav?rlkey=pk241hf4c8qv7780qsq2m4cgv&st=wgpy7vop&dl=0
- Stop: https://dl.dropbox.com/scl/fi/4mkbgr7om46imcp0l86yd/dictation-stop.wav?rlkey=4t5uwvg9kufphlu58natjn43t&st=9or4oqr0&dl=0

These files are already included in the public source repository with a provenance
notice that does not claim redistribution permission. No packaged public release
has been published. I am asking about both the existing repository copies and the
planned packaged release.

The cues play when recording starts and after text is successfully inserted. We
converted the original 48 kHz stereo 24-bit PCM to 16-bit PCM for playback. Sample
rate, channels, frame count and timing are unchanged; users can adjust playback
volume. The start cue is 12,170 frames and the stop cue is 14,532 frames. We have
not composed these two sounds ourselves.

The requested scope is worldwide distribution with the public source repository,
Windows installers and macOS disk images for Apple Silicon and Intel, including
future versions of Whisper Local. Could you confirm whether you control the rights
needed for this use and can grant permission? If a separate rights holder must
approve it, please direct me to them.

Please specify any attribution, naming, modification, redistribution, duration or
commercial-use conditions, and whether permission can be granted without a fee.
We would also like to know whether retaining the selectable label "Wispr Flow" is
acceptable with appropriate attribution. We do not intend to suggest affiliation
or endorsement. This request concerns the two audio files and their label only;
it does not request approval for other branding or visual assets.

If available, a written license or an explicit permission statement covering the
scope above would help us document the release. We will review any proposed terms
before accepting an agreement or paying a fee.

Thank you,
Whisper Local maintainer

## Recording the response

Sending the request, acknowledging receipt or receiving an automated reply does not
pass the release gate. Review the actual grant, its issuer and its scope against
both bundled files and the intended distribution. Record a non-sensitive evidence
reference in `sound_redistribution.permission_reference` only after permission is
verified; use `passed` only when it covers the shipped assets. Until then, retain
`status: unknown` and the empty permission reference. See
[sound provenance](../assets/sounds/SOURCES.md) and [release gates](RELEASE.md).
