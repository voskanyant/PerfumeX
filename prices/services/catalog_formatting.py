from __future__ import annotations

import re

from assistant_linking.models import TITLECASE_APOSTROPHE_SUFFIXES
from assistant_linking.models import TITLECASE_LOWER_WORDS
from assistant_linking.utils.text import fold_latin_diacritics


COLLECTION_TITLECASE_ACRONYMS = {
    "DNA",
    "VIP",
    "WB",
}
ROMAN_NUMERAL_RE = re.compile(r"^[IVXLCDM]+$")
TITLE_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+(?:'[A-Za-zÀ-ÖØ-öø-ÿ]+)?")


def _catalogue_collection_title_word(word: str, index: int) -> str:
    if not word:
        return word
    lower_word = word.lower()
    if index > 0 and lower_word in TITLECASE_LOWER_WORDS:
        return lower_word
    if word.upper() in COLLECTION_TITLECASE_ACRONYMS:
        return word.upper()
    if word.isupper() and ROMAN_NUMERAL_RE.match(word):
        return word
    return lower_word[:1].upper() + lower_word[1:]


def normalize_catalogue_collection_name(value: str) -> str:
    """Normalize reviewed external collection names before storing locally."""

    text = re.sub(r"\s+", " ", fold_latin_diacritics(value or "").strip())
    if not text:
        return ""

    word_index = 0

    def replace_word(match: re.Match[str]) -> str:
        nonlocal word_index
        raw_word = match.group(0)
        apostrophe_parts = raw_word.split("'")
        normalized_parts = []
        for part_index, part in enumerate(apostrophe_parts):
            if part_index > 0 and part.lower() in TITLECASE_APOSTROPHE_SUFFIXES:
                normalized_parts.append(part.lower())
                continue
            normalized_parts.append(
                _catalogue_collection_title_word(
                    part,
                    word_index if part_index == 0 else 0,
                )
            )
        word_index += 1
        return "'".join(normalized_parts)

    return TITLE_WORD_RE.sub(replace_word, text)


def _catalogue_text_needs_title_case(value: str) -> bool:
    letters = [char for char in value if char.isalpha()]
    return bool(letters) and all(char.isupper() for char in letters)


def normalize_catalogue_perfume_name(value: str) -> str:
    """Normalize reviewed external perfume names before storing/displaying locally."""

    text = re.sub(r"\s+", " ", fold_latin_diacritics(value or "").strip())
    if not text or not _catalogue_text_needs_title_case(text):
        return text

    word_index = 0

    def replace_word(match: re.Match[str]) -> str:
        nonlocal word_index
        raw_word = match.group(0)
        normalized = _catalogue_collection_title_word(raw_word, word_index)
        word_index += 1
        return normalized

    return TITLE_WORD_RE.sub(replace_word, text)
