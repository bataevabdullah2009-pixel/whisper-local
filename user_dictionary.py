"""Local, literal replacements after ASR; no I/O or transcript retention."""
from __future__ import annotations

import re
import unicodedata

MAX_RULES = 500
MAX_TERM_LENGTH = 200


def validate_rule(source, replacement):
    """Return a canonical rule or a content-free error for the settings page."""
    if not isinstance(source, str) or not isinstance(replacement, str):
        raise ValueError("Обе части правила должны быть текстом.")
    source = unicodedata.normalize("NFC", source.strip())
    replacement = unicodedata.normalize("NFC", replacement.strip())
    if not source or not replacement:
        raise ValueError("Заполните обе части правила.")
    if any(unicodedata.category(char).startswith("C") for char in source + replacement):
        raise ValueError("Правило должно быть одной строкой без управляющих символов.")
    source = " ".join(source.split())
    if max(len(source), len(replacement)) > MAX_TERM_LENGTH:
        raise ValueError(f"В каждой части правила допускается до {MAX_TERM_LENGTH} символов.")
    return {"source": source, "replacement": replacement}


def validate_rules(rules):
    if not isinstance(rules, list):
        raise ValueError("Словарь должен содержать список правил.")
    if len(rules) > MAX_RULES:
        raise ValueError(f"В словаре допускается до {MAX_RULES} правил.")
    result, seen = [], set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("Проверьте обе части каждого правила.")
        item = validate_rule(rule.get("source"), rule.get("replacement"))
        key = item["source"].casefold()
        if key in seen:
            raise ValueError("Для такого распознавания уже есть правило. Отредактируйте его.")
        seen.add(key)
        result.append(item)
    return result


def normalize_config(config):
    """Migrate old configs and keep valid rules from a manually damaged config."""
    enabled = config.get("dictionary_enabled", True)
    config["dictionary_enabled"] = enabled if isinstance(enabled, bool) else True
    rules = config.get("dictionary_rules", [])
    valid, seen = [], set()
    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            try:
                item = validate_rule(rule.get("source"), rule.get("replacement"))
            except ValueError:
                continue
            key = item["source"].casefold()
            if key not in seen:
                seen.add(key)
                valid.append(item)
            if len(valid) == MAX_RULES:
                break
    config["dictionary_rules"] = valid


class UserDictionary:
    def __init__(self, rules, enabled=True):
        self.rules = validate_rules(rules)
        self.enabled = enabled
        self.pattern = None
        self.replacements = {}
        if not enabled or not self.rules:
            return
        # Longest phrase wins at the same position. Substitution scans the original
        # text once, so replacements can never trigger another rule or a cycle.
        alternatives = []
        for index, rule in enumerate(sorted(self.rules, key=lambda item: len(item["source"]), reverse=True)):
            name = f"r{index}"
            literal = r"[^\S\r\n]+".join(re.escape(word) for word in rule["source"].split(" "))
            alternatives.append(f"(?P<{name}>{literal})")
            self.replacements[name] = rule["replacement"]
        self.pattern = re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.IGNORECASE)

    def apply(self, text):
        if self.pattern is None:
            return text
        return self.pattern.sub(lambda match: self.replacements[match.lastgroup], text)
