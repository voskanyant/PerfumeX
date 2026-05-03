from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0044_normalizationstatssnapshot_decant_count"),
    ]

    operations = [
        migrations.AddField(
            model_name="normalizationstatssnapshot",
            name="vintage_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
