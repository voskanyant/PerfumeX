"""
Add concurrent trigram indexes for catalogue search pages.

Our Products and catalogue linking search use icontains filters across
canonical brand, perfume, collection, concentration, and variant text fields.
These database-only indexes keep those live searches fast without cached result
pages.
"""

from django.db import migrations


INDEXES = (
    ("catalog_brand_name_trgm_idx", "catalog_brand", "name"),
    ("catalog_perf_name_trgm_idx", "catalog_perfume", "name"),
    ("catalog_perf_coll_trgm_idx", "catalog_perfume", "collection_name"),
    ("catalog_perf_conc_trgm_idx", "catalog_perfume", "concentration"),
    ("catalog_perf_aud_trgm_idx", "catalog_perfume", "audience"),
    ("catalog_var_size_trgm_idx", "catalog_perfumevariant", "size_label"),
    ("catalog_var_pack_trgm_idx", "catalog_perfumevariant", "packaging"),
    ("catalog_var_type_trgm_idx", "catalog_perfumevariant", "variant_type"),
    ("catalog_var_sku_trgm_idx", "catalog_perfumevariant", "sku"),
    ("catalog_var_ean_trgm_idx", "catalog_perfumevariant", "ean"),
)


def create_trigram_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    for index_name, table_name, column_name in INDEXES:
        schema_editor.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} "
            f"ON {table_name} USING gin ({column_name} gin_trgm_ops);"
        )


def drop_trigram_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for index_name, _table_name, _column_name in reversed(INDEXES):
        schema_editor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name};")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("catalog", "0003_collection_perfume_collection_and_more"),
    ]

    operations = [
        migrations.RunPython(create_trigram_indexes, drop_trigram_indexes),
    ]
