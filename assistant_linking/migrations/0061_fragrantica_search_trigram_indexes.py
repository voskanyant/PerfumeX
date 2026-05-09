"""
Add concurrent trigram indexes for Fragrantica review/search pages.

FragranticaProduct is large in production and the catalogue review/linking UI
uses icontains search over the staged brand, scent, and collection fields. Keep
these indexes database-only because they depend on PostgreSQL pg_trgm opclasses.
"""

from django.db import migrations


INDEXES = (
    ("alink_frag_brand_trgm_idx", "brand_name"),
    ("alink_frag_nbrand_trgm_idx", "normalized_brand_name"),
    ("alink_frag_name_trgm_idx", "name"),
    ("alink_frag_nname_trgm_idx", "normalized_name"),
    ("alink_frag_coll_trgm_idx", "collection_name"),
)


def create_trigram_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    for index_name, column_name in INDEXES:
        schema_editor.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} "
            "ON assistant_linking_fragranticaproduct "
            f"USING gin ({column_name} gin_trgm_ops);"
        )


def drop_trigram_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for index_name, _column_name in reversed(INDEXES):
        schema_editor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name};")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("assistant_linking", "0060_fragrantica_lookup_index"),
    ]

    operations = [
        migrations.RunPython(create_trigram_indexes, drop_trigram_indexes),
    ]
