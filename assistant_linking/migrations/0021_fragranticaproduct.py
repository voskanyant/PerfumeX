from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_expand_concentration_labels"),
        ("assistant_linking", "0020_seed_van_cleef_collection_extraordinaire"),
    ]

    operations = [
        migrations.CreateModel(
            name="FragranticaProduct",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("brand_name", models.CharField(db_index=True, max_length=200)),
                ("normalized_brand_name", models.CharField(db_index=True, max_length=255)),
                ("name", models.CharField(db_index=True, max_length=220)),
                ("normalized_name", models.CharField(db_index=True, max_length=255)),
                ("collection_name", models.CharField(blank=True, db_index=True, max_length=180)),
                ("audience", models.CharField(blank=True, db_index=True, max_length=80)),
                ("release_year", models.PositiveSmallIntegerField(blank=True, db_index=True, null=True)),
                ("source_path", models.CharField(blank=True, max_length=500)),
                ("source_url", models.URLField(blank=True)),
                ("source_domain", models.CharField(db_index=True, default="fragrantica.com", max_length=160)),
                (
                    "match_status",
                    models.CharField(
                        choices=[
                            ("unlinked", "Unlinked"),
                            ("linked", "Linked"),
                            ("ignored", "Ignored"),
                        ],
                        db_index=True,
                        default="unlinked",
                        max_length=20,
                    ),
                ),
                (
                    "matched_perfume",
                    models.ForeignKey(
                        blank=True,
                        db_index=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="fragrantica_products",
                        to="catalog.perfume",
                    ),
                ),
            ],
            options={
                "ordering": ("brand_name", "collection_name", "name"),
            },
        ),
        migrations.AddConstraint(
            model_name="fragranticaproduct",
            constraint=models.UniqueConstraint(
                fields=("normalized_brand_name", "normalized_name", "source_path"),
                name="uniq_fragrantica_product_source",
            ),
        ),
    ]
