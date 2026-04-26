from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0018_seed_cap_preprocess_rule"),
    ]

    operations = [
        migrations.AddField(
            model_name="parsedsupplierproduct",
            name="release_year",
            field=models.PositiveSmallIntegerField(blank=True, db_index=True, null=True),
        ),
    ]
