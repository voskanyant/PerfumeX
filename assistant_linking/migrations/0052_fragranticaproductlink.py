from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("assistant_linking", "0051_normalizationstatssnapshot_atomizer_count"),
        ("catalog", "0003_collection_perfume_collection_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="FragranticaProductLink",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "link_type",
                    models.CharField(
                        choices=[
                            ("primary", "Primary"),
                            ("manual_extra", "Manual extra"),
                        ],
                        db_index=True,
                        default="manual_extra",
                        max_length=20,
                    ),
                ),
                ("note", models.CharField(blank=True, max_length=255)),
                (
                    "perfume",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fragrantica_review_links",
                        to="catalog.perfume",
                    ),
                ),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="review_links",
                        to="assistant_linking.fragranticaproduct",
                    ),
                ),
            ],
            options={
                "ordering": (
                    "source__brand_name",
                    "source__collection_name",
                    "source__name",
                ),
            },
        ),
        migrations.AddConstraint(
            model_name="fragranticaproductlink",
            constraint=models.UniqueConstraint(
                fields=("source", "perfume"),
                name="uniq_fragrantica_product_review_link",
            ),
        ),
        migrations.AddIndex(
            model_name="fragranticaproductlink",
            index=models.Index(
                fields=["perfume", "link_type"],
                name="assistant_l_perfume_ba3ba2_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="fragranticaproductlink",
            index=models.Index(
                fields=["source", "link_type"],
                name="assistant_l_source__95c2dc_idx",
            ),
        ),
    ]
