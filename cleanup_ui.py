"""Local cleanup options and an ephemeral preview of the dictation text pipeline."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QPlainTextEdit,
                              QVBoxLayout, QWidget, QPushButton, QProgressBar)
from pathlib import Path

from dictionary_ui import caption
from text_cleanup import DEFAULTS, TextCleanup
from user_dictionary import UserDictionary


class CleanupPage(QWidget):
    changed = Signal()
    editorDownloadRequested = Signal()
    editorImportRequested = Signal()
    editorCancelRequested = Signal()
    editorPreviewRequested = Signal(str)
    copyOriginalRequested = Signal()

    def __init__(self, config):
        super().__init__()
        self.config = config
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(caption("Автоматический редактор", "section"))
        self.editor_enabled = QCheckBox("Исправлять ошибки, повторы и пунктуацию во всей фразе")
        self.editor_enabled.setChecked(config.get("editor_enabled", False))
        layout.addWidget(self.editor_enabled)
        layout.addWidget(caption("Отдельная модель редактирует текст на вашем компьютере. "
            "Список замен заполнять не нужно. Скачайте редактор один раз; после этого интернет не требуется."))
        self.editor_status = caption("", "detail")
        layout.addWidget(self.editor_status)
        self.editor_progress = QProgressBar()
        self.editor_progress.setAccessibleName("Загрузка редактора")
        self.editor_progress.hide()
        layout.addWidget(self.editor_progress)
        editor_buttons = QHBoxLayout()
        self.editor_import = QPushButton("Выбрать скачанную модель…")
        self.editor_import.clicked.connect(self.editorImportRequested.emit)
        self.editor_download = QPushButton("Скачать редактор · 1,8 ГБ")
        self.editor_download.setObjectName("primary")
        self.editor_download.clicked.connect(self.editorDownloadRequested.emit)
        self.editor_cancel = QPushButton("Отменить загрузку")
        self.editor_cancel.clicked.connect(self.editorCancelRequested.emit)
        self.editor_cancel.hide()
        for button in (self.editor_import, self.editor_cancel, self.editor_download):
            editor_buttons.addWidget(button)
        layout.addLayout(editor_buttons)
        self.copy_original = QPushButton("Скопировать последний текст без редактора")
        self.copy_original.setEnabled(False)
        self.copy_original.clicked.connect(self.copyOriginalRequested.emit)
        layout.addWidget(self.copy_original)
        layout.addWidget(caption("Простая очистка", "section"))
        self.enabled = QCheckBox("Очищать текст после распознавания")
        self.enabled.setChecked(config.get("cleanup_enabled", DEFAULTS["cleanup_enabled"]))
        layout.addWidget(self.enabled)
        layout.addWidget(caption("Локальные правила приводят текст в порядок перед вставкой и копированием. "
            "Выберите нужные изменения и проверьте их на примере ниже."))

        self.spacing = QCheckBox("Исправлять пробелы у знаков препинания")
        self.spacing.setChecked(config.get("cleanup_spacing", DEFAULTS["cleanup_spacing"]))
        layout.addWidget(self.spacing)
        layout.addWidget(caption("Убирает лишние пробелы в обычном тексте. Переносы строк, "
            "отступы, ссылки и текст в кавычках сохраняются.", "detail"))

        self.fillers = QCheckBox("Убирать междометия «эээ», «эм», «uh», «um»")
        self.fillers.setChecked(config.get("cleanup_fillers", DEFAULTS["cleanup_fillers"]))
        layout.addWidget(self.fillers)
        layout.addWidget(caption("Удаляет отдельные междометия и запятые при них. "
            "Слова «ну», «вот», «как бы» остаются. Включайте, когда междометия не нужны.", "detail"))

        layout.addWidget(caption("Проверка результата", "section"))
        preview = QHBoxLayout()
        self.test_input, self.test_output = QPlainTextEdit(), QPlainTextEdit()
        self.test_input.setAccessibleName("Текст для проверки очистки")
        self.test_input.setPlaceholderText("Например: эээ,  привет , мир!")
        self.test_output.setAccessibleName("Результат очистки и словаря")
        self.test_output.setPlaceholderText("Здесь появится результат")
        self.test_output.setReadOnly(True)
        for title, field in (("Исходный текст", self.test_input), ("Для вставки", self.test_output)):
            column = QVBoxLayout()
            column.addWidget(caption(title, "detail"))
            field.setFixedHeight(105)
            column.addWidget(field)
            preview.addLayout(column)
        layout.addLayout(preview)
        self.preview_note = caption("", "detail")
        layout.addWidget(self.preview_note)
        self.editor_preview = QPushButton("Проверить редактор на примере")
        self.editor_preview.clicked.connect(lambda: self.editorPreviewRequested.emit(self.test_input.toPlainText()))
        layout.addWidget(self.editor_preview)
        layout.addWidget(caption("Словарь имеет приоритет. Простая очистка работает без микрофона и модели; "
            "введённый текст не сохраняется.", "detail"))
        layout.addStretch()

        self.enabled.toggled.connect(self.commit)
        self.spacing.toggled.connect(self.commit)
        self.fillers.toggled.connect(self.commit)
        self.test_input.textChanged.connect(self.preview)
        self.editor_enabled.toggled.connect(self.commit_editor)
        self.refresh_controls()
        self.refresh_editor()
        self.preview()

    def commit_editor(self):
        self.config["editor_enabled"] = self.editor_enabled.isChecked()
        self.preview()
        self.changed.emit()

    def refresh_editor(self, message=""):
        ready = Path(self.config.get("editor_model_path", "")).is_file()
        self.editor_status.setText(message or ("Редактор готов. Обработка фразы может занять несколько секунд."
            if ready else "Модель ещё не подготовлена. Загрузка начнётся только по кнопке."))
        self.editor_preview.setEnabled(ready)

    def set_editor_busy(self, busy, message=""):
        self.editor_download.setEnabled(not busy)
        self.editor_import.setEnabled(not busy)
        self.editor_download.setVisible(not busy)
        self.editor_import.setVisible(not busy)
        self.editor_cancel.setVisible(busy)
        self.editor_progress.setVisible(busy)
        if busy:
            self.editor_progress.setRange(0, 0)
        self.refresh_editor(message)

    def update_editor_progress(self, event):
        self.editor_progress.setRange(0, 100)
        self.editor_progress.setValue(int(event.get("done", 0) * 100 / max(1, event.get("total", 1))))
        self.editor_status.setText("Проверяем файлы редактора…" if event.get("phase") == "verify" else "Скачиваем редактор…")

    def refresh_controls(self):
        self.spacing.setEnabled(self.enabled.isChecked())
        self.fillers.setEnabled(self.enabled.isChecked())

    def commit(self):
        self.config.update(cleanup_enabled=self.enabled.isChecked(),
                           cleanup_spacing=self.spacing.isChecked(),
                           cleanup_fillers=self.fillers.isChecked())
        self.refresh_controls()
        self.preview()
        self.changed.emit()

    def preview(self):
        dictionary = UserDictionary(self.config.get("dictionary_rules", []),
                                    self.config.get("dictionary_enabled", True))
        # Match the controller's existing trimming of worker results.
        text = self.test_input.toPlainText().strip()
        text = TextCleanup(self.config).apply(text, dictionary.pattern)
        self.test_output.setPlainText(dictionary.apply(text))
        self.preview_note.setText("Очистка включена. Словарь учтён."
            if self.enabled.isChecked() else "Очистка отключена. В результате остаются включённые правила словаря.")
        if self.editor_enabled.isChecked():
            self.preview_note.setText("Показана простая очистка и словарь. Для автоматического редактирования нажмите «Проверить редактор на примере».")
