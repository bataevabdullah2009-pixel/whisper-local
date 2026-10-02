"""Conservative offline cleanup. No I/O, learned model or transcript retention.

Only ordinary prose spacing and explicitly enabled hesitation removal change.
Protected spans are copied verbatim; ambiguous punctuation is left alone.
"""
from __future__ import annotations

import re

DEFAULTS = {"cleanup_enabled": False, "cleanup_spacing": True, "cleanup_fillers": False}
SPACE = " \u00a0\u202f"
HESITATION = re.compile(r"(?<![\w'’\-‐-―])(?:э{2,}|эм+|uh+|um+)(?![\w'’\-‐-―])", re.IGNORECASE)

# Copy technical tokens and quoted/code spans without substituting placeholders.
# Quotes have no linguistic interpretation, and code detection is conservative.
PROTECTED = re.compile(
    r"```[\s\S]*?(?:```|\Z)|~~~[\s\S]*?(?:~~~|\Z)"
    r"|`[^`\r\n]*(?:`|(?=\r?$))"
    r'|"[^"\r\n]*(?:"|(?=\r?$))|«[^»\r\n]*(?:»|(?=\r?$))'
    r"|“[^”\r\n]*(?:”|(?=\r?$))|‘[^’\r\n]*(?:’|(?=\r?$))"
    r"|(?<!\w)'[^'\r\n]*'(?!\w)"
    r'|(?:[A-Za-z][A-Za-z0-9+.-]*://|www\.)[^\s<>"«»`]+|[\w.+-]+@[\w.-]+\.[\w-]+'
    r'|(?:[A-Za-z]:[\\/]|\\\\)[^\s<>"«»`]+|(?:[\w.-]+[\\/])+[\w.-]+'
    r'|(?<!\w)/(?:[\w.-]+/)*[\w.-]+'
    r"|(?<!\w)\.[A-Za-z][\w-]*|(?:[\w-]+\.)+[\w-]+(?:[/?#][^\s<>\"«»`]+)?"
    r"|\d+(?:[.,:]\d+)+",
    re.IGNORECASE | re.MULTILINE,
)
CODE_LINE = re.compile(
    r"[{}\[\]]|(?:==|!=|=>|:=)|\w\s*=\s*\S|\w+\([^)]*\)"
    r"|^\s*(?:def |class |import |from \w+ import |(?:git|python[\d.]*|npm|pip) )"
)


def normalize_config(config):
    for key, default in DEFAULTS.items():
        if not isinstance(config.get(key), bool):
            config[key] = default


def _merge(spans):
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        elif end > start:
            merged.append((start, end))
    return merged


def _remove_fillers(text, previous, following):
    spans = []
    for match in HESITATION.finditer(text):
        start, end = match.span()
        while start > 0 and text[start - 1] in SPACE:
            start -= 1
        while end < len(text) and text[end] in SPACE:
            end += 1
        if end < len(text) and text[end] == ",":
            end += 1
            while end < len(text) and text[end] in SPACE:
                end += 1
        # A final hesitation must not leave an orphan comma at the line end.
        next_char = text[end:end + 1] or following
        if (not next_char or next_char in "\r\n") and start > 0 and text[start - 1] == ",":
            start -= 1
            while start > 0 and text[start - 1] in SPACE:
                start -= 1
        spans.append((start, end))
    parts, cursor = [], 0
    for start, end in _merge(spans):
        parts.append(text[cursor:start])
        left = text[start - 1:start] if start else previous
        right = text[end:end + 1] or following
        if (left and right and not left.isspace() and not right.isspace()
                and left not in "([{«“" and right not in ",.;:!?…)]}»”"):
            parts.append(" ")
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts), bool(spans)


class TextCleanup:
    def __init__(self, config):
        options = {key: config.get(key) for key in DEFAULTS}
        normalize_config(options)
        self.enabled = options["cleanup_enabled"]
        self.spacing = options["cleanup_spacing"]
        self.fillers = options["cleanup_fillers"]

    def apply(self, text, protected_pattern=None):
        if not self.enabled or not (self.spacing or self.fillers) or not text:
            return text
        spans = [match.span() for match in PROTECTED.finditer(text)]
        for line in re.finditer(r"[^\r\n]+", text):
            if CODE_LINE.search(line.group()):
                spans.append(line.span())
            else:
                indent = re.match(r"[ \t\u00a0\u202f]+", line.group())
                if indent:
                    spans.append((line.start(), line.start() + indent.end()))
        if protected_pattern is not None:
            spans.extend(match.span() for match in protected_pattern.finditer(text))
        pieces, cursor, removed = [], 0, False
        for start, end in _merge(spans) + [(len(text), len(text))]:
            plain = text[cursor:start]
            previous, following = text[cursor - 1:cursor] if cursor else "", text[start:start + 1]
            if self.fillers:
                plain, changed = _remove_fillers(plain, previous, following)
                removed |= changed
            if self.spacing:
                plain = re.sub(r"[ \u00a0\u202f]{2,}", " ", plain)
                plain = re.sub(r"[ \u00a0\u202f]+(?=[,.;:!?…])", "", plain)
                plain = re.sub(r"(?<=[,;!?])(?=[^\W\d_])", " ", plain)
                if plain and plain[-1] in ",;!?" and following.isalpha():
                    plain += " "
            pieces.extend((plain, text[start:end]))
            cursor = end
        result = "".join(pieces)
        if removed and re.fullmatch(r"[ \t\r\n\u00a0\u202f,.;:!?…]*", result):
            return ""
        return result
