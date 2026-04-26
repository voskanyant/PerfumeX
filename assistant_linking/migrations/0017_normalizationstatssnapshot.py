from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("assistant_linking", "0016_seed_chloe_atelier_collection_aliases"),
    ]

    operations = [
        migrations.CreateModel(
            name="NormalizationStatsSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("parser_version", models.CharField(db_index=True, max_length=40)),
                ("scope_key", models.CharField(db_index=True, max_length=80)),
                ("hidden_keywords_hash", models.CharField(blank=True, db_index=True, max_length=64)),
                ("hidden_keywords", models.JSONField(blank=True, default=list)),
                ("parsed_count", models.PositiveIntegerField(default=0)),
                ("unparsed_count", models.PositiveIntegerField(default=0)),
                ("low_confidence_count", models.PositiveIntegerField(default=0)),
                ("missing_brand_count", models.PositiveIntegerField(default=0)),
                ("missing_name_count", models.PositiveIntegerField(default=0)),
                ("missing_concentration_count", models.PositiveIntegerField(default=0)),
                ("missing_size_count", models.PositiveIntegerField(default=0)),
                ("modifier_count", models.PositiveIntegerField(default=0)),
                ("garbage_count", models.PositiveIntegerField(default=0)),
                ("tester_sample_count", models.PositiveIntegerField(default=0)),
                ("set_count", models.PositiveIntegerField(default=0)),
                ("recent_parse_ids", models.JSONField(blank=True, default=list)),
                ("generated_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("is_stale", models.BooleanField(db_index=True, default=False)),
            ],
            options={
                "ordering": ("-generated_at", "-updated_at"),
            },
        ),
        migrations.AddConstraint(
            model_name="normalizationstatssnapshot",
            constraint=models.UniqueConstraint(
                fields=("parser_version", "scope_key"),
                name="uniq_normalization_stats_snapshot_scope",
            ),
        ),
    ]
