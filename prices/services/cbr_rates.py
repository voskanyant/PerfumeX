from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from prices import models


def fetch_cbr_usd_rub_rate(rate_date) -> Decimal:
    query_date = rate_date.strftime("%d/%m/%Y")
    url = "https://www.cbr.ru/scripts/XML_daily.asp?" + urllib.parse.urlencode(
        {"date_req": query_date}
    )
    with urllib.request.urlopen(url, timeout=15) as response:
        payload = response.read()
    root = ET.fromstring(payload)

    usd_value = None
    usd_nominal = None
    for valute in root.findall("Valute"):
        char_code = (valute.findtext("CharCode") or "").strip().upper()
        if char_code != "USD":
            continue
        value_text = (valute.findtext("Value") or "").strip().replace(",", ".")
        nominal_text = (valute.findtext("Nominal") or "").strip().replace(",", ".")
        usd_value = Decimal(value_text)
        usd_nominal = Decimal(nominal_text)
        break

    if usd_value is None or not usd_nominal:
        raise RuntimeError(f"CBR USD rate not found for {rate_date.isoformat()}")

    return (usd_value / usd_nominal).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def upsert_cbr_markup_rates(rate_date, markup_percent: Decimal):
    source = f"CBR + {Decimal(markup_percent):.3f}%"
    existing = (
        models.ExchangeRate.objects.filter(
            rate_date=rate_date,
            from_currency=models.Currency.USD,
            to_currency=models.Currency.RUB,
            source=source,
        )
        .order_by("-id")
        .first()
    )
    if existing:
        return existing.rate

    raw_rate = fetch_cbr_usd_rub_rate(rate_date)
    multiplier = Decimal("1") + (Decimal(markup_percent) / Decimal("100"))
    usd_to_rub = (raw_rate * multiplier).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    models.ExchangeRate.objects.update_or_create(
        rate_date=rate_date,
        from_currency=models.Currency.USD,
        to_currency=models.Currency.RUB,
        defaults={"rate": usd_to_rub, "source": source},
    )
    models.ExchangeRate.objects.filter(
        rate_date=rate_date,
        from_currency=models.Currency.RUB,
        to_currency=models.Currency.USD,
        source__startswith="CBR + ",
    ).delete()
    return usd_to_rub


def upsert_cbr_markup_rates_range(start_date, end_date, markup_percent: Decimal):
    current = start_date
    total_days = 0
    synced_days = 0
    errors = []
    while current <= end_date:
        total_days += 1
        try:
            upsert_cbr_markup_rates(current, markup_percent)
            synced_days += 1
        except Exception as exc:
            errors.append(f"{current.isoformat()}: {exc}")
        current = current + timedelta(days=1)
    return {
        "total_days": total_days,
        "synced_days": synced_days,
        "errors": errors,
    }
