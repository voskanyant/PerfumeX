from django.db import migrations
from django.db import models


INDEX_NAME = "catalog_perf_link_order_idx"


def create_linking_order_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} "
        "ON catalog_perfume "
        "(brand_id, collection_name, name, concentration, id);"
    )


def drop_linking_order_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME};")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("catalog", "0004_catalog_search_trigram_indexes"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    create_linking_order_index,
                    drop_linking_order_index,
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="perfume",
                    index=models.Index(
                        fields=[
                            "brand",
                            "collection_name",
                            "name",
                            "concentration",
                            "id",
                        ],
                        name=INDEX_NAME,
                    ),
                ),
            ],
        ),
    ]
