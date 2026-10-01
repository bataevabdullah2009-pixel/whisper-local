"""Local cleanup options and an ephemeral preview of the dictation text pipeline."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QPlainTextEdit,
                              QVBoxLayout, QWidget)

from dictionary_ui import caption
from text_cleanup import DEFAULTS, TextCleanup
from user_dictionary import UserDictionary


class CleanupPage(QWidget):
    changed = Signal()

    def __init__(self, config):
        super().__init__()
        self.config = config
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(10)
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
        layout.addWidget(caption("Словарь имеет приоритет. Пример работает без микрофона и модели; "
            "введённый текст не сохраняется.", "detail"))
        layout.addStretch()

        self.enabled.toggled.connect(self.commit)
        self.spacing.toggled.connect(self.commit)
        self.fillers.toggled.connect(self.commit)
        self.test_input.textChanged.connect(self.preview)
        self.refresh_controls()
        self.preview()

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
