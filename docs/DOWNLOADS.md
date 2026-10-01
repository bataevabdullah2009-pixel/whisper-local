# Continuing model downloads

Downloading requires an explicit click on **Скачать и настроить** or
**Продолжить загрузку**. Starting the app never resumes network traffic by itself.
Recording and transcription remain offline.

The selected model, pinned revision, recognition backend and device are saved in
`download-state.json` in the app's data directory before the worker starts. After
an interruption, cancellation or app restart, the model page offers that selection
again. Hardware recommendations cannot replace it. Selecting a different model
starts its own download; earlier partial files remain available when it is selected
again. Existing active model settings change only after successful activation.

Model files use `.part` files inside the directory for the pinned revision. The
worker continues with an HTTP byte range, validates response metadata, and verifies
the catalogue checksum before atomically replacing the destination. Verified files
are reused. A server that ignores Range safely restarts the affected file instead
of appending a second full copy. Corrupt complete files are downloaded again.

Temporary network failures have bounded retries. Exhausting them leaves the saved
bytes for a later explicit attempt. Cancellation and terminating the application
also retain partial files. An operating-system lock prevents a worker left alive
after an app crash from sharing partial files with another downloader. Its lock is
released when the worker exits; a competing attempt asks the user to wait and retry.
Invalid or stale intent metadata is ignored locally.

Automated coverage includes interrupted connections, retry exhaustion, HTTP ranges,
checksum failures, restarting a downloader process, saved selection after restarting
the controller, cancellation, hardware recommendations, and a failed metadata write.
These checks use synthetic model bytes and do not open a microphone.

Before release, also check the packaged app on Windows, Apple Silicon and Intel:

1. Start a model download, interrupt the connection, then restore it and continue.
2. Cancel partway through, exit the app, open it again and click **Продолжить загрузку**.
3. Verify the same model/backend/device is selected and the saved bytes are reused.
4. Confirm the model starts only after verification; the previous working model
   remains configured if download or verification fails.
5. With networking disabled, restart the app and confirm it displays the pending
   choice without contacting the model host.
