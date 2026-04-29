from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0023_seed_dunhill_signature_collection_alias"),
    ]

    operations = [
        migrations.AddField(
            model_name="normalizationstatssnapshot",
            name="bag_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="normalizationstatssnapshot",
            name="cosmetic_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="normalizationstatssnapshot",
            name="deodorant_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="normalizationstatssnapshot",
            name="manual_review_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
