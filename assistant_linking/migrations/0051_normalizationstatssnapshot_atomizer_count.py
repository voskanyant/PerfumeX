from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0050_seed_tv_eau_de_toilette_aliases"),
    ]

    operations = [
        migrations.AddField(
            model_name="normalizationstatssnapshot",
            name="atomizer_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
