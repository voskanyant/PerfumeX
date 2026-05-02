from __future__ import annotations

import re
import unicodedata


CYRILLIC_LATIN_LOOKALIKE_TRANSLATION = str.maketrans(
    {
        "\u0410": "A",
        "\u0430": "a",
        "\u0412": "B",
        "\u0432": "b",
        "\u0415": "E",
        "\u0435": "e",
        "\u0401": "E",
        "\u0451": "e",
        "\u041a": "K",
        "\u043a": "k",
        "\u041c": "M",
        "\u043c": "m",
        "\u041d": "H",
        "\u043d": "h",
        "\u041e": "O",
        "\u043e": "o",
        "\u0420": "P",
        "\u0440": "p",
        "\u0421": "C",
        "\u0441": "c",
        "\u0422": "T",
        "\u0442": "t",
        "\u0425": "X",
        "\u0445": "x",
        "\u0423": "Y",
        "\u0443": "y",
        "\u0406": "I",
        "\u0456": "i",
    }
)


def normalize_mixed_script_latin_lookalikes(value: str) -> str:
    """Convert Cyrillic lookalikes only inside tokens that also contain Latin.

    Supplier files sometimes mix alphabets inside a Latin scent name, for
    example Cyrillic ``\u0421`` in ``\u0421iel``. Fully Cyrillic words stay
    untouched so Russian notes such as country or packaging comments remain
    available to Russian term rules.
    """

    def normalize_token(match: re.Match[str]) -> str:
        token = match.group(0)
        has_latin = bool(re.search(r"[A-Za-z]", token))
        has_cyrillic = bool(re.search(r"[\u0400-\u04ff]", token))
        if has_latin and has_cyrillic:
            return token.translate(CYRILLIC_LATIN_LOOKALIKE_TRANSLATION)
        return token

    return re.sub(r"[\w\u0400-\u04ff]+", normalize_token, value or "")


def normalize_alias_value(value: str) -> str:
    """Normalize supplier alias text for deterministic matching.

    Examples:
        >>> normalize_alias_value("DG_EDT100ml")
        'dg edt 100ml'
        >>> normalize_alias_value("Eau de Parfum50")
        'eau de parfum 50'
    """
    text = unicodedata.normalize("NFKC", value or "")
    text = normalize_mixed_script_latin_lookalikes(text).lower()
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
