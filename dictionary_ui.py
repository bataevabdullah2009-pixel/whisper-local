"""Settings and ephemeral text preview for the local user dictionary."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHeaderView, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QSizePolicy, QVBoxLayout, QWidget)

from user_dictionary import UserDictionary, validate_rule, validate_rules


def caption(text, kind="description"):
    widget = QLabel(text)
    widget.setObjectName(kind)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


class DictionaryPage(QWidget):
    changed = Signal()

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.editing_row = None
        self.dictionary = UserDictionary(config.get("dictionary_rules", []), config.get("dictionary_enabled", True))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(8)
        self.enabled = QCheckBox("Использовать замены при диктовке")
        self.enabled.setChecked(self.dictionary.enabled)
        layout.addWidget(self.enabled)
        layout.addWidget(caption("Имена, компании и термины: укажите, что распознаёт Whisper и как нужно писать. "
            "Целые слова и фразы, без учёта регистра. Всё хранится на этом компьютере."))

        self.empty = caption("Пока нет правил. Добавьте первое ниже.")
        layout.addWidget(self.empty)
        self.table = QTableWidget(0, 2)
        self.table.setAccessibleName("Правила пользовательского словаря")
        self.table.setHorizontalHeaderLabels(["Whisper распознаёт", "Как нужно писать"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setMinimumHeight(100)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        layout.addWidget(self.table, 1)

        fields = QHBoxLayout()
        self.source, self.replacement = QLineEdit(), QLineEdit()
        for title, field, placeholder in (("Whisper распознаёт", self.source, "Например: опен ай"),
                                         ("Как нужно писать", self.replacement, "OpenAI")):
            column = QVBoxLayout()
            name = caption(title, "section")
            name.setBuddy(field)
            field.setAccessibleName(title)
            field.setPlaceholderText(placeholder)
            column.addWidget(name)
            column.addWidget(field)
            fields.addLayout(column)
        layout.addLayout(fields)
        buttons = QHBoxLayout()
        self.new = QPushButton("Новое правило")
        self.delete = QPushButton("Удалить")
        self.save = QPushButton("Добавить правило")
        self.save.setObjectName("primary")
        buttons.addWidget(self.new)
        buttons.addWidget(self.delete)
        buttons.addStretch()
        buttons.addWidget(self.save)
        layout.addLayout(buttons)
        self.message = caption("Выберите правило в списке, чтобы изменить его.", "detail")
        layout.addWidget(self.message)

        layout.addWidget(caption("Проверка сохранённых правил", "section"))
        preview = QHBoxLayout()
        self.test_input, self.test_output = QPlainTextEdit(), QPlainTextEdit()
        self.test_input.setAccessibleName("Текст для проверки словаря")
        self.test_input.setPlaceholderText("Введите пример распознанного текста…")
        self.test_output.setAccessibleName("Результат проверки словаря")
        self.test_output.setPlaceholderText("Результат замен появится здесь")
        self.test_output.setReadOnly(True)
        for title, field in (("Исходный текст", self.test_input), ("После замен", self.test_output)):
            column = QVBoxLayout()
            column.addWidget(caption(title, "detail"))
            field.setFixedHeight(80)
            column.addWidget(field)
            preview.addLayout(column)
        layout.addLayout(preview)
        self.preview_note = caption("Проверка не включает микрофон. Этот текст не сохраняется.", "detail")
        layout.addWidget(self.preview_note)

        self.refresh_table()
        self.reset_editor()
        self.table.itemSelectionChanged.connect(self.select_rule)
        self.enabled.toggled.connect(self.toggle)
        self.new.clicked.connect(self.reset_editor)
        self.delete.clicked.connect(self.delete_rule)
        self.save.clicked.connect(self.save_rule)
        self.source.returnPressed.connect(self.save_rule)
        self.replacement.returnPressed.connect(self.save_rule)
        self.test_input.textChanged.connect(self.preview)

    def refresh_table(self, selected=None):
        self.table.blockSignals(True)
        self.table.clearContents()
        rules = self.config.get("dictionary_rules", [])
        self.table.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            for column, key in enumerate(("source", "replacement")):
                self.table.setItem(row, column, QTableWidgetItem(rule[key]))
        self.table.clearSelection()
        if selected is not None:
            self.table.selectRow(selected)
        self.table.blockSignals(False)
        self.empty.setVisible(not rules)

    def reset_editor(self):
        self.table.clearSelection()
        self.editing_row = None
        self.source.clear()
        self.replacement.clear()
        self.save.setText("Добавить правило")
        self.delete.setEnabled(False)
        self.message.setStyleSheet("")
        self.message.setText("Заполните обе части и нажмите «Добавить правило».")

    def select_rule(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.reset_editor()
            return
        self.editing_row = rows[0].row()
        rule = self.config["dictionary_rules"][self.editing_row]
        self.source.setText(rule["source"])
        self.replacement.setText(rule["replacement"])
        self.save.setText("Сохранить правило")
        self.delete.setEnabled(True)
        self.message.setStyleSheet("")
        self.message.setText("Измените правило и нажмите «Сохранить правило».")

    def save_rule(self):
        rules = [dict(rule) for rule in self.config.get("dictionary_rules", [])]
        try:
            rule = validate_rule(self.source.text(), self.replacement.text())
            row = self.editing_row
            if row is None:
                row = len(rules)
                rules.append(rule)
            else:
                rules[row] = rule
            rules = validate_rules(rules)
        except ValueError as error:
            self.message.setText(str(error))
            self.message.setStyleSheet("color:#A44332;")
            return
        self.config["dictionary_rules"] = rules
        self.refresh_table(row)
        self.select_rule()
        self.commit()
        self.message.setText("Правило применено. Изменения сохраняются автоматически.")

    def delete_rule(self):
        if self.editing_row is None:
            return
        rules = list(self.config["dictionary_rules"])
        del rules[self.editing_row]
        self.config["dictionary_rules"] = rules
        self.refresh_table()
        self.reset_editor()
        self.commit()
        self.message.setText("Правило удалено. Изменения сохраняются автоматически.")

    def toggle(self, enabled):
        self.config["dictionary_enabled"] = enabled
        self.commit()

    def commit(self):
        self.dictionary = UserDictionary(self.config["dictionary_rules"], self.config["dictionary_enabled"])
        self.preview()
        self.changed.emit()

    def preview(self):
        self.test_output.setPlainText(self.dictionary.apply(self.test_input.toPlainText()))
        self.preview_note.setText("Проверка не включает микрофон. Этот текст не сохраняется." if self.enabled.isChecked()
            else "Замены отключены. Правила сохранены; результат совпадает с исходным текстом.")
