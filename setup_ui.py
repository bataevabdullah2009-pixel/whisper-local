"""The first-run model page uses the same native controls as settings."""
from pathlib import Path
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton, QProgressBar
from model_manager import CATALOG, MODELS, model_size
import platform_native as native


def text(value, role="description"):
    widget = QLabel(value)
    widget.setWordWrap(True)
    widget.setObjectName(role)
    return widget


class ModelPage(QWidget):
    downloadRequested = Signal(str, str)
    importRequested = Signal(str)
    cancelRequested = Signal()
    practiceRequested = Signal()

    def __init__(self, config):
        super().__init__()
        self.user_selected = bool(config.get("model_path"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(text("Подготовим диктовку", "section"))
        layout.addWidget(text("Скачайте модель один раз. После этого голос распознаётся на вашем компьютере, без интернета."))
        self.hardware = text("Проверяем ваш компьютер…", "detail")
        layout.addWidget(self.hardware)
        self.model = QComboBox()
        self.model.setAccessibleName("Модель распознавания")
        for model in CATALOG:
            self.model.addItem(f"{model['title']} · {model_size(model['id']) / 1024**2:.0f} МБ", model["id"])
        self.model.setCurrentIndex(max(0, self.model.findData(config.get("model_id", "small"))))
        layout.addWidget(self.model)
        self.description = text("")
        layout.addWidget(self.description)
        self.model.currentIndexChanged.connect(self._describe)
        self.model.activated.connect(self._selected)
        self._describe()
        row = QHBoxLayout()
        row.addWidget(text("Обработка", "section"))
        self.device = QComboBox()
        self.device.setAccessibleName("Где распознавать речь")
        self.device.addItem("Автоматически", "auto")
        self.device.addItem("Процессор", "cpu")
        if not native.IS_MAC:
            self.device.addItem("Видеокарта NVIDIA", "cuda")
        self.device.setCurrentIndex(max(0, self.device.findData(config.get("device", "auto"))))
        row.addWidget(self.device, 1)
        layout.addLayout(row)
        self.message = text("Выберите модель или используйте уже скачанную папку.")
        layout.addWidget(self.message)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("Загрузка модели")
        self.progress.setRange(0, 100)
        self.progress.hide()
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.local = QPushButton("Выбрать папку…")
        self.local.clicked.connect(lambda: self.importRequested.emit(self.device.currentData()))
        self.download = QPushButton("Скачать и настроить")
        self.download.setObjectName("primary")
        self.download.clicked.connect(lambda: self.downloadRequested.emit(self.model.currentData(), self.device.currentData()))
        self.cancel = QPushButton("Отменить загрузку")
        self.cancel.clicked.connect(self.cancelRequested)
        self.cancel.hide()
        buttons.addWidget(self.local)
        buttons.addStretch()
        buttons.addWidget(self.cancel)
        buttons.addWidget(self.download)
        layout.addLayout(buttons)
        layout.addStretch()
        self.practice = QPushButton("Проверить диктовку")
        self.practice.setObjectName("primary")
        self.practice.clicked.connect(self.practiceRequested)
        self.practice.hide()
        layout.addWidget(self.practice)

    def _selected(self, index):
        self.user_selected = True

    def _describe(self):
        model = MODELS[self.model.currentData()]
        self.description.setText(f"{model['description']} Whisper {model['id']}.")

    def set_hardware(self, info):
        if native.IS_MAC:
            chip = "Apple Silicon" if info.get("architecture") == "arm64" else "Intel"
            self.hardware.setText(f"Mac · {chip} · распознавание на процессоре")
        else:
            self.hardware.setText("Windows · NVIDIA обнаружена; ускорение проверим при запуске модели"
                                  if info.get("cuda") else "Windows · распознавание на процессоре")
        if not self.user_selected:
            self.model.setCurrentIndex(max(0, self.model.findData(info.get("recommended_model", "small"))))
        if info.get("ram_gb"):
            self.hardware.setText(self.hardware.text() + f" · {info['ram_gb']} ГБ памяти")

    def set_busy(self, busy, message=""):
        for widget in (self.model, self.device, self.local, self.download):
            widget.setEnabled(not busy)
        self.cancel.setVisible(busy)
        self.progress.setVisible(busy)
        if message:
            self.message.setText(message)
        if busy:
            self.progress.setValue(0)

    def update_progress(self, event):
        done, total = event.get("done", 0), max(1, event.get("total", 1))
        self.progress.setValue(min(100, int(100 * done / total)))
        phase = "Проверяем файлы" if event.get("phase") == "verify" else "Скачиваем модель"
        self.message.setText(f"{phase} · {done / 1024**2:.0f} из {total / 1024**2:.0f} МБ")

    def set_ready(self, path, device):
        self.set_busy(False)
        self.message.setText(f"Модель готова · {device}. Можно проверить микрофон и первую фразу.")
        self.message.setToolTip(str(Path(path)))
        self.practice.show()
