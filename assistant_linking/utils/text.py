from __future__ import annotations

import re
import unicodedata


def normalize_alias_value(value: str) -> str:
    """Normalize supplier alias text for deterministic matching.

    Examples:
        >>> normalize_alias_value("DG_EDT100ml")
        'dg edt 100ml'
        >>> normalize_alias_value("Eau de Parfum50")
        'eau de parfum 50'
    """
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"\b(edp|edt|edc)(?=\d)", r"\1 ", text)
    text = re.sub(
        r"\b(eau de parfum|eau de toilette|eau de cologne|extrait de parfum|extrait|parfum)(?=\d)",
        r"\1 ",
        text,
    )
    text = re.sub(r"[\u00a0_/,;:|()\[\]{}]+", " ", text)
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
