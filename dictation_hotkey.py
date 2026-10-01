"""Shortcut validation and state transitions. No typed text is retained."""
from dataclasses import dataclass, field


MODIFIERS = ("ctrl", "alt", "shift", "cmd")
WINDOWS_KEYS = {**{chr(n).lower(): n for n in range(65, 91)},
                **{str(n): 48 + n for n in range(10)},
                **{f"f{n}": 111 + n for n in range(1, 20)}, "space": 32}
MAC_KEYS = dict(zip("abcdefghijklmnopqrstuvwxyz",
    (0, 11, 8, 2, 14, 3, 5, 4, 34, 38, 40, 37, 46, 45, 31, 35, 12, 15, 1, 17, 32, 9, 13, 7, 16, 6)))
MAC_KEYS.update(dict(zip("0123456789", (29, 18, 19, 20, 21, 23, 22, 26, 28, 25))))
MAC_KEYS.update(dict(zip((f"f{n}" for n in range(1, 20)),
    (122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111, 105, 107, 113, 106, 64, 79, 80))))
MAC_KEYS["space"] = 49
WINDOWS_MODIFIERS = {0xA0: "shift_l", 0xA1: "shift_r", 0xA2: "ctrl_l", 0xA3: "ctrl_r",
                     0xA4: "alt_l", 0xA5: "alt_r", 0x5B: "cmd_l", 0x5C: "cmd_r"}
MAC_MODIFIERS = {56: "shift_l", 60: "shift_r", 59: "ctrl_l", 62: "ctrl_r",
                 58: "alt_l", 61: "alt_r", 55: "cmd_l", 54: "cmd_r", 63: "fn"}


@dataclass(frozen=True)
class Shortcut:
    key: str
    modifiers: frozenset = frozenset()
    default: bool = False

    @property
    def value(self):
        return "default" if self.default else "+".join((*[m for m in MODIFIERS if m in self.modifiers], self.key))

    def title(self, is_mac=False):
        if self.default:
            return "правый Option" if is_mac else "левый Alt"
        names = {"ctrl": "Ctrl", "alt": "Option" if is_mac else "Alt", "shift": "Shift", "cmd": "⌘ Command"}
        return " + ".join((*[names[m] for m in MODIFIERS if m in self.modifiers],
                           "Пробел" if self.key == "space" else self.key.upper()))


def parse_shortcut(value, is_mac=False):
    if value == "default":
        return Shortcut("alt_r" if is_mac else "alt_l", default=True)
    if not isinstance(value, str):
        raise ValueError("Выберите сочетание клавиш.")
    parts = value.lower().split("+")
    key, modifiers = parts[-1], frozenset(parts[:-1])
    if key in ("escape", "esc"):
        raise ValueError("Esc используется для отмены диктовки. Выберите другую клавишу.")
    if (key not in WINDOWS_KEYS or not modifiers.issubset(MODIFIERS)
            or len(modifiers) != len(parts) - 1):
        raise ValueError("Выберите букву, цифру, пробел или функциональную клавишу.")
    if not is_mac and "cmd" in modifiers:
        raise ValueError("Сочетания с Windows зарезервированы системой.")
    if not modifiers and not key.startswith("f"):
        raise ValueError("Добавьте Ctrl, Alt или Shift: обычная клавиша нужна для ввода текста.")
    if modifiers == {"shift"} and not key.startswith("f"):
        raise ValueError("Добавьте Ctrl или Alt: Shift с этой клавишей нужен для ввода текста.")
    if not is_mac and key == "f12":
        raise ValueError("F12 зарезервирована Windows для отладчика. Выберите другую клавишу.")
    # Common editing/navigation/system shortcuts should never be swallowed by dictation.
    editing = "cmd" if is_mac else "ctrl"
    common = key in "acvxyzsfpnowtlq" and modifiers in ({editing}, {editing, "shift"})
    system = (key == "space" and modifiers in ({"alt"}, {"ctrl"}, {"cmd"}, {"cmd", "alt"}))
    system |= not is_mac and key == "f4" and modifiers in ({"alt"}, {"ctrl"})
    system |= not is_mac and key == "f10" and modifiers == {"shift"}
    system |= is_mac and key in ("h", "m") and modifiers in ({"cmd"}, {"cmd", "alt"})
    if common or system:
        raise ValueError("Это сочетание используется для ввода или управления системой. Выберите другое.")
    return Shortcut(key, modifiers)


def normalize_config(config, is_mac=False):
    try:
        config["hotkey"] = parse_shortcut(config.get("hotkey", "default"), is_mac).value
    except ValueError:
        config["hotkey"] = "default"
    if config.get("dictation_mode") not in ("hold", "toggle"):
        config["dictation_mode"] = "hold"


@dataclass
class HotkeyState:
    shortcut: Shortcut
    threshold: float = .18
    mode: str = "hold"
    replay_default: bool = False
    held: bool = False
    canceled: bool = False
    forwarded: bool = False
    started: float = 0.0
    escape_down: bool = False
    owned_primary: bool = False
    primary_down: bool = False
    pressed_modifiers: set = field(default_factory=set)

    def process(self, key, down, now, *, enabled=True, escape_enabled=False):
        """Return (suppress event, notification, replay default Alt down/tap)."""
        if key == "escape":
            if not down and self.escape_down:
                self.escape_down = False
                return True, None, None
            if down and (self.escape_down or escape_enabled or (self.held and not self.forwarded)):
                first = not self.escape_down
                self.escape_down, self.canceled = True, True
                return True, "cancel" if first else None, None
            return False, None, None
        modifier = key.rsplit("_", 1)[0]
        if modifier in MODIFIERS or key == "fn":
            if down:
                self.pressed_modifiers.add(key)
            else:
                self.pressed_modifiers.discard(key)
        active_modifiers = {m.rsplit("_", 1)[0] for m in self.pressed_modifiers}
        required = set(self.shortcut.modifiers)
        if self.shortcut.default:
            required.add("alt")
            # Do not capture both Option/Alt keys together.
            matches = self.pressed_modifiers == {self.shortcut.key}
        else:
            matches = active_modifiers == required
        if key == self.shortcut.key:
            if down:
                if self.primary_down:
                    return self.owned_primary and not self.forwarded and (self.replay_default or not self.shortcut.default), None, None
                self.primary_down = True
                if not enabled or not matches:
                    return False, None, None
                self.held, self.owned_primary = True, True
                self.canceled, self.forwarded, self.started = False, False, now
                return self.replay_default or not self.shortcut.default, "down", None
            self.primary_down = False
            if not self.owned_primary:
                return False, None, None
            self.owned_primary, self.held = False, False
            suppress = not self.forwarded and (self.replay_default or not self.shortcut.default)
            if self.canceled or self.forwarded:
                return suppress, None, None
            if self.mode == "hold" and now - self.started < self.threshold:
                return suppress, "cancel", "tap" if self.replay_default else None
            return suppress, "up", None
        if self.held:
            if not self.canceled and not down and modifier in required and modifier not in active_modifiers:
                self.held, self.canceled = False, True
                return False, "up", None
            if down and (not self.canceled or (self.replay_default and not self.forwarded)):
                self.canceled = True
                if self.replay_default:
                    self.forwarded = True
                    return True, "cancel", "down"
                return False, "cancel", None
        return False, None, None
