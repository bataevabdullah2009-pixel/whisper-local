# Whisper Local

<!-- impeccable:product-schema 1 -->

## Platform

Windows and macOS desktop, native Qt interface.

## Users

Русскоязычный пользователь Windows или Mac, который диктует в редакторы и другие приложения.

## Product Purpose

Первый запуск предлагает подходящую модель, скачивает и проверяет её. По умолчанию удерживание левого Alt в Windows или правого Option на Mac записывает речь. В «Основных» можно выбрать сочетание и режим удержания или нажатия для начала и остановки. После окончания записи локальный Whisper распознаёт речь, результат вставляется в исходное поле.

## Capabilities and Constraints

Faster Whisper работает на CPU или NVIDIA CUDA в Windows и на CPU в macOS. Экспериментальный whisper.cpp / Metal на Mac остаётся отдельным черновым PR до физических проверок скорости, русской диктовки, микрофона и вставки. Модели base, small и large-v3-turbo загружаются по выбору пользователя. Во время записи видна только волна; готовый текст появляется после окончания записи. Esc и потеря фокуса известного исходного поля отменяют запись. Окно записи не забирает фокус.

## Brand Commitments

Пользователь отверг первоначальную широкую панель и мятную палитру. Требует оформления как у Wispr Flow: маленькая чёрная капсула записи, белое окно с боковым меню для настроек. Волна — зелёная как Spotify или красная; по умолчанию выбран зелёный, оба цвета доступны в настройках. Русские подписи.

## Implementation decisions

Native Python/Qt chosen for Windows focus preservation, global hotkey, system tray, GPU worker isolation and crisp DPI scaling. Compact black floating capsule with measured green waveform. No audio files or transcript history are retained. These are implementation choices, not additional user requirements.
