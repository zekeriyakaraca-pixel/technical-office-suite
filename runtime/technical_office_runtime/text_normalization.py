from __future__ import annotations

import unicodedata


_TURKISH_TRANSLATION = str.maketrans(
    {
        "\u0131": "i",
        "\u0130": "i",
        "\u011f": "g",
        "\u011e": "g",
        "\u00fc": "u",
        "\u00dc": "u",
        "\u015f": "s",
        "\u015e": "s",
        "\u00f6": "o",
        "\u00d6": "o",
        "\u00e7": "c",
        "\u00c7": "c",
    }
)

_MOJIBAKE_MARKERS = (
    "Ã",
    "Ä",
    "Å",
    "Â",
    "â",
    "\ufffd",
)


def repair_text(value: str) -> str:
    """Repair common UTF-8-as-Latin mojibake without over-normalizing valid text."""
    if not isinstance(value, str) or not value:
        return value
    candidates = [value]
    for encoding in ("cp1252", "latin-1"):
        try:
            repaired = value.encode(encoding).decode("utf-8")
        except UnicodeError:
            continue
        candidates.append(repaired)
    return min(candidates, key=_repair_score)


def normalize_search_text(value: str) -> str:
    repaired = repair_text(value)
    lowered = repaired.lower().translate(_TURKISH_TRANSLATION)
    normalized = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _repair_score(value: str) -> tuple[int, int, int]:
    marker_hits = sum(value.count(marker) for marker in _MOJIBAKE_MARKERS)
    replacement_hits = value.count("\ufffd")
    return (marker_hits, replacement_hits, len(value))
