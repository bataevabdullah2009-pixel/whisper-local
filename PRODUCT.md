# Whisper Local

<!-- impeccable:product-schema 1 -->

## Platform

Windows desktop, native Qt interface.

## Users

Русскоязычный пользователь Windows, который диктует в VS Code и другие приложения.

## Product Purpose

Удерживание левого Alt открывает компактное окно и записывает речь. Отпускание завершает запись, локальный Whisper распознаёт речь на GPU, результат вставляется в исходное поле.

## Capabilities and Constraints

Используется уже установленная Faster Whisper large-v3-turbo и RTX 5060 Ti. Во время записи пользователь хочет только волну, без предварительного текста. Готовый текст показывается после отпускания Alt. Esc отменяет запись. Сочетания с Alt сохраняются. Окно не забирает фокус.

## Brand Commitments

Пользователь отверг первоначальную широкую панель и мятную палитру. Требует оформления как у Wispr Flow: маленькая чёрная капсула записи, белое окно с боковым меню для настроек. Волна — зелёная как Spotify или красная; по умолчанию выбран зелёный, оба цвета доступны в настройках. Русские подписи.

## Implementation decisions

Native Python/Qt chosen for Windows focus preservation, global hotkey, system tray, GPU worker isolation and crisp DPI scaling. Compact black floating capsule with measured green waveform. No audio files or transcript history are retained. These are implementation choices, not additional user requirements.
