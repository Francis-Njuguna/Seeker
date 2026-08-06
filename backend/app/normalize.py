"""Canonical forms used for exact matching, plus redaction for previews.

Matching is exact only — no fuzzy or semantic search. "Exact" is applied to a
normalized form so that cosmetic differences in how the same value was typed
(spacing, dashes, case) do not defeat a genuine match.
"""
from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_WHITESPACE = re.compile(r"\s+")
_NAME_PUNCT = re.compile(r"[^A-Za-z0-9\s'\-]+")


def normalize_id_number(value: str) -> str:
    """Uppercase and strip every non-alphanumeric character.

    ``AB-123 456`` and ``ab123456`` both normalize to ``AB123456``.
    """
    return _NON_ALNUM.sub("", value or "").upper()


def normalize_name(value: str) -> str:
    """Uppercase, drop punctuation, collapse whitespace.

    ``  John  O'Brien-Smith `` -> ``JOHN O'BRIEN-SMITH``. Token order is
    preserved, so this stays an exact match, not a fuzzy one.
    """
    cleaned = _NAME_PUNCT.sub(" ", value or "")
    return _WHITESPACE.sub(" ", cleaned).strip().upper()


def redact_name(name: str) -> str:
    """``John Michael Smith`` -> ``J*** M****** S****``.

    Keeps the first letter and length of each token, which is enough for a
    seeker to recognise their own name without exposing it to a stranger.
    """
    parts = [p for p in _WHITESPACE.split((name or "").strip()) if p]
    out = []
    for part in parts:
        if len(part) == 1:
            out.append(part.upper())
        else:
            out.append(part[0].upper() + "*" * (len(part) - 1))
    return " ".join(out)


def redact_id_number(id_number: str, visible_suffix: int = 2) -> str:
    """``AB123456`` -> ``******56``.

    Only the final characters are shown. A seeker who holds the real number can
    confirm the tail; someone guessing cannot recover the rest.
    """
    normalized = normalize_id_number(id_number)
    if not normalized:
        return ""
    if len(normalized) <= visible_suffix:
        return "*" * len(normalized)
    suffix = normalized[-visible_suffix:]
    return "*" * (len(normalized) - visible_suffix) + suffix
