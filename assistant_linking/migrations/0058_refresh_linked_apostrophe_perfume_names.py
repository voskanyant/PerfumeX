import re
import unicodedata

from django.db import migrations


APOSTROPHE_MARKS = {
    "\u2019",
    "\u2018",
    "\u201b",
    "\u02bc",
    "\u02bb",
    "\u02b9",
    "\u2032",
    "\u00b4",
    "`",
}

TEXT_TRANSLATION = str.maketrans(
    {
        "Æ": "AE",
        "æ": "ae",
        "Œ": "OE",
        "œ": "oe",
        "Ø": "O",
        "ø": "o",
        "Ð": "D",
        "ð": "d",
        "Đ": "D",
        "đ": "d",
        "Ł": "L",
        "ł": "l",
        "Þ": "Th",
        "þ": "th",
        "ß": "ss",
        "ẞ": "SS",
        "ı": "i",
        "\u2019": "'",
        "\u2018": "'",
        "\u201b": "'",
        "\u02bc": "'",
        "\u02bb": "'",
        "\u02b9": "'",
        "\u2032": "'",
        "\u00b4": "'",
        "`": "'",
    }
)


def fold_catalogue_text(value: str) -> str:
    text = (value or "").translate(TEXT_TRANSLATION)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.translate(TEXT_TRANSLATION)


def normalize_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"\b(edp|edt|edc)(?=\d)", r"\1 ", text)
    text = re.sub(
        r"\b(eau de parfum|eau de toilette|eau de cologne|extrait de parfum|extrait|parfum)(?=\d)",
        r"\1 ",
        text,
    )
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"(?<!\d)\.(?!\d)", " ", text)
    text = re.sub(r"[\u00a0_&'\",.;:|/\\()\[\]{}]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_leading_brand(brand_name: str, scent_name: str) -> str:
    scent = re.sub(r"\s+", " ", (scent_name or "").strip())
    brand = re.sub(r"\s+", " ", (brand_name or "").strip())
    if not scent or not brand:
        return scent
    match = re.match(rf"^{re.escape(brand)}(?:[\s/\-:]+)", scent, flags=re.I)
    if not match:
        return scent
    cleaned = scent[match.end() :].strip()
    return cleaned or scent


def reviewed_name(brand_name: str, scent_name: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        fold_catalogue_text(strip_leading_brand(brand_name, scent_name)).strip(),
    )


def refresh_linked_apostrophe_perfume_names(apps, schema_editor):
    FragranticaProduct = apps.get_model("assistant_linking", "FragranticaProduct")
    linked_rows = (
        FragranticaProduct.objects.filter(
            matched_perfume__isnull=False,
            match_status="linked",
        )
        .select_related("matched_perfume")
        .only("brand_name", "name", "matched_perfume__name")
    )
    for source in linked_rows.iterator(chunk_size=500):
        if not any(mark in (source.name or "") for mark in APOSTROPHE_MARKS):
            continue
        desired_name = reviewed_name(source.brand_name, source.name)
        perfume = source.matched_perfume
        if (
            not desired_name
            or perfume.name == desired_name
            or normalize_key(perfume.name) != normalize_key(desired_name)
        ):
            continue
        perfume.name = desired_name
        perfume.save(update_fields=["name", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0057_fold_fragrantica_apostrophes"),
        ("catalog", "0003_collection_perfume_collection_and_more"),
    ]

    operations = [
        migrations.RunPython(
            refresh_linked_apostrophe_perfume_names,
            migrations.RunPython.noop,
        ),
    ]
