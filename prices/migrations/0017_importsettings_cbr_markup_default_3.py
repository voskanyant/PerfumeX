from decimal import Decimal

from django.db import migrations, models


def bump_default_markup(apps, schema_editor):
    ImportSettings = apps.get_model("prices", "ImportSettings")
    ImportSettings.objects.filter(cbr_markup_percent=Decimal("2.700")).update(
        cbr_markup_percent=Decimal("3.000")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("prices", "0016_pricesnapshot_price_rub_price_usd"),
    ]

    operations = [
        migrations.AlterField(
            model_name="importsettings",
            name="cbr_markup_percent",
            field=models.DecimalField(decimal_places=3, default=3.0, max_digits=6),
        ),
        migrations.RunPython(bump_default_markup, migrations.RunPython.noop),
    ]
