from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0043_seed_cyrillic_brand_aliases"),
    ]

    operations = [
        migrations.AddField(
            model_name="normalizationstatssnapshot",
            name="decant_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
