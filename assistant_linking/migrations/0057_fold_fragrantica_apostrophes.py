import re
import unicodedata

from django.db import migrations


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
        "’": "'",
        "‘": "'",
        "‛": "'",
        "ʼ": "'",
        "ʻ": "'",
        "ʹ": "'",
        "′": "'",
        "´": "'",
        "`": "'",
    }
)


def fold_catalogue_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.translate(TEXT_TRANSLATION)


def normalize_alias_value(value: str) -> str:
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


def normalized_brand_name(value: str) -> str:
    return normalize_alias_value(fold_catalogue_text(value).replace("&", " and "))


def normalized_product_name(brand_name: str, scent_name: str) -> str:
    text = fold_catalogue_text(strip_leading_brand(brand_name, scent_name))
    return normalize_alias_value(text).replace("&", "and")


def refresh_fragrantica_normalized_fields(apps, schema_editor):
    FragranticaProduct = apps.get_model("assistant_linking", "FragranticaProduct")
    rows = FragranticaProduct.objects.all().only(
        "id",
        "brand_name",
        "name",
        "normalized_brand_name",
        "normalized_name",
    )
    for row in rows.iterator(chunk_size=1000):
        desired_brand = normalized_brand_name(row.brand_name)
        desired_name = normalized_product_name(row.brand_name, row.name)
        update_fields = []
        if row.normalized_brand_name != desired_brand:
            row.normalized_brand_name = desired_brand
            update_fields.append("normalized_brand_name")
        if row.normalized_name != desired_name:
            row.normalized_name = desired_name
            update_fields.append("normalized_name")
        if update_fields:
            row.save(update_fields=update_fields)


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0056_fold_fragrantica_latin_diacritics"),
    ]

    operations = [
        migrations.RunPython(
            refresh_fragrantica_normalized_fields,
            migrations.RunPython.noop,
        ),
    ]
