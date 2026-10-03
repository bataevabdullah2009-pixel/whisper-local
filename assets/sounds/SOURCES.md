# Wispr Flow reference cues

Retrieved 2026-09-30 from the audio URLs used by the **official public demo**:
https://wisprflow.ai/demo

The page loads https://cdn.jsdelivr.net/npm/@tanay-wispr/webflow-package@6.5.17/dist/web-demo/index.js
which constructs two `Audio` instances from:

- Start: https://dl.dropbox.com/scl/fi/zv8278qh9ovwq0r89rch1/dictation-start.wav?rlkey=pk241hf4c8qv7780qsq2m4cgv&st=wgpy7vop&dl=0
- Stop: https://dl.dropbox.com/scl/fi/4mkbgr7om46imcp0l86yd/dictation-stop.wav?rlkey=4t5uwvg9kufphlu58natjn43t&st=9or4oqr0&dl=0

These are Wispr Flow assets, not original compositions by this project. They were included for the requested personal local setup. No license to redistribute the vendor's assets is asserted.

The downloaded files are 48 kHz, stereo, 24-bit PCM. Bundled files are converted to 16-bit PCM for Windows playback; sample rate, stereo channels, frame count and timing are unchanged. Start is 12,170 frames (0.254 s); stop is 14,532 frames (0.303 s). The application only applies the user's volume gain. Its completion cue plays after successful paste.

Verified again on 2026-10-03: converting each official source's signed 24-bit PCM
samples to 16-bit PCM reproduces the bundled audio payload exactly. This conversion
does not make the two Wispr Flow cues original compositions. The four Console/Air
files below were separately regenerated from our synthesis code and matched the
bundled files byte for byte.

The owner confirmed on 2026-10-03 that the current Wispr Flow cues should remain.
Files and the default choice are preserved. The owner also approved distribution
without waiting for separate vendor permission; this is recorded as an
[owner decision](../../docs/sound-distribution-owner-decision-2026-10-03.json), not a
license grant. An optional [permission request draft](../../docs/SOUND-PERMISSION-REQUEST.md)
has been prepared; it has not been sent and no permission has been received.

Visual reference for the custom Qt painting:
https://cdn.prod.website-files.com/682f84b3838c89f8ff7667db/6a4f5ff3f6da81ba30f2416b_Flowbar.svg
(linked by the Wispr Flow home page on the same date). 97 × 28 viewBox, ten bars, gray cancel circle, white confirm circle. The application uses its own painting code, not the vendor's app or recording service.

## Original Console and Air themes

`console-start.wav`, `console-insert.wav`, `air-start.wav`, and `air-insert.wav` are original sounds created for this app. They contain no PlayStation samples or melodies. Console uses warm consonant plucks and short stereo reflections, inspired by the restrained sound of game-console interfaces; Air uses shorter quiet tones with a filtered breath. Reproducible synthesis is in `create_original_cues.py` (NumPy, run manually). The application plays the bundled WAV files without synthesizing anything while recording.

Latest user instruction keeps the previous ordinary 252×44 and mini 184×48 capsule dimensions and both green/red wave colors. Only inner controls and styling follow the Flow visual reference; pixel-identical whole-capsule geometry is no longer the goal.
