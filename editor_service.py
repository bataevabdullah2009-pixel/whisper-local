"""One ephemeral offline CLI process per phrase. Stopping it releases all model/text memory."""
import os
from pathlib import Path
import sys

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from phrase_editor import (MAX_CHARACTERS, SYSTEM_PROMPT, TIMEOUT_SECONDS, TOKEN,
                          accept_edit, protect_text, validate_editor_model)
from runtime import ROOT
from editor_transport import PromptPipe


def editor_executable():
    root = ROOT / "native/editor" if getattr(sys, "frozen", False) else ROOT / "build/native/editor"
    return root / ("llama-cli.exe" if sys.platform == "win32" else "llama-cli")


def editor_prompt(text):
    return ("<|im_start|>system\n" + SYSTEM_PROMPT + "<|im_end|>\n<|im_start|>user\n"
            + text + "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n")


def editor_command(model, pipe=""):
    # The command line contains fixed instructions and a model path, never dictated text.
    return [str(editor_executable()), "--model", str(model), "--no-conversation",
            "--single-turn", "--simple-io", "--no-display-prompt",
            "--no-escape", "--log-verbosity", "0", "--no-perf", "--no-warmup",
            "--ctx-size", "4096", "--predict", "1536", "--temp", "0", "--seed", "0",
            "--threads", str(max(1, min(8, (os.cpu_count() or 2) // 2))),
            "--gpu-layers", "0", "--file", pipe]


class PhraseEditorService(QObject):
    finished = Signal(str, str, bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.prompt_pipe = None
        self.identifier = self.original = self.source = ""
        self.protected = []
        self.output = bytearray()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self.complete(None, "Редактор отвечает слишком долго. Сохранён текст Whisper."))

    def edit(self, identifier, text, model, dictionary_pattern=None):
        self.stop()
        self.identifier, self.original = identifier, text
        if len(text) > MAX_CHARACTERS or TOKEN.search(text) or "<|" in text:
            self.complete(None, "Эта запись сохранена без редактора.")
            return
        try:
            model = validate_editor_model(model)
            if not editor_executable().is_file():
                raise ValueError()
        except (OSError, ValueError):
            self.complete(None, "Редактор не подготовлен. Сохранён текст Whisper.")
            return
        self.source, self.protected = protect_text(text, dictionary_pattern)
        if not any(char.isalpha() for char in TOKEN.sub("", self.source)):
            self.complete(None, "Технический текст сохранён точно.")
            return
        try:
            self.prompt_pipe = PromptPipe(editor_prompt(self.source), self)
        except OSError:
            self.complete(None, "Редактор недоступен. Сохранён текст Whisper.")
            return
        process = self.process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        # Ignore external llama flags: no remote models, prompt caches or log files.
        for key in environment.keys():
            if key.startswith(("LLAMA_", "GGML_")):
                environment.remove(key)
        environment.insert("HF_HUB_OFFLINE", "1")
        environment.insert("HF_HUB_DISABLE_TELEMETRY", "1")
        environment.insert("DO_NOT_TRACK", "1")
        process.setProcessEnvironment(environment)
        process.setWorkingDirectory(str(editor_executable().parent))
        command = editor_command(model, self.prompt_pipe.path)
        process.setProgram(command[0])
        process.setArguments(command[1:])
        process.readyReadStandardOutput.connect(self.read_output)
        process.readyReadStandardError.connect(lambda: process.readAllStandardError())
        process.errorOccurred.connect(lambda _error: self.complete(None, "Редактор недоступен. Сохранён текст Whisper."))
        process.finished.connect(self.process_finished)
        self.timer.start(TIMEOUT_SECONDS * 1000)
        process.start()

    def read_output(self):
        if self.process:
            self.output.extend(bytes(self.process.readAllStandardOutput()))
            if len(self.output) > 64 * 1024:
                self.complete(None, "Ответ редактора отклонён. Сохранён текст Whisper.")

    def process_finished(self, code, status):
        self.read_output()
        if not self.process:
            return
        candidate = None
        if code == 0 and status == QProcess.ExitStatus.NormalExit:
            try:
                output = bytes(self.output).decode("utf-8").rstrip()
                # The pinned CLI emits this only at EOG. Token/context exhaustion is rejected.
                ending = " [end of text]"
                if output.endswith(ending):
                    candidate = accept_edit(self.source, output[:-len(ending)], self.protected)
            except (UnicodeError, ValueError, IndexError):
                pass
        self.complete(candidate, "" if candidate is not None else "Ответ редактора отклонён. Сохранён текст Whisper.")

    def complete(self, candidate, reason):
        identifier, original = self.identifier, self.original
        self.stop()
        if identifier:
            self.finished.emit(identifier, candidate if candidate is not None else original,
                               candidate is not None and candidate != original, reason)

    def stop(self):
        self.timer.stop()
        process, self.process = self.process, None
        if process:
            # Disconnect before kill so no old completion can reach a later request.
            for signal in (process.readyReadStandardOutput, process.readyReadStandardError,
                           process.errorOccurred, process.finished):
                signal.disconnect()
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(2000)
            process.deleteLater()
        self.identifier = self.original = self.source = ""
        self.protected = []
        self.output.clear()
        if self.prompt_pipe:
            self.prompt_pipe.stop()
            self.prompt_pipe.deleteLater()
            self.prompt_pipe = None
