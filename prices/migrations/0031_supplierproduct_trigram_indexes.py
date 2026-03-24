from django.db import migrations


def create_trigram_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    schema_editor.execute(
        "CREATE INDEX IF NOT EXISTS prices_sp_name_trgm_idx "
        "ON prices_supplierproduct USING gin (name gin_trgm_ops);"
    )
    schema_editor.execute(
        "CREATE INDEX IF NOT EXISTS prices_sp_sku_trgm_idx "
        "ON prices_supplierproduct USING gin (supplier_sku gin_trgm_ops);"
    )
    schema_editor.execute(
        "CREATE INDEX IF NOT EXISTS prices_supplier_name_trgm_idx "
        "ON prices_supplier USING gin (name gin_trgm_ops);"
    )


def drop_trigram_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP INDEX IF EXISTS prices_supplier_name_trgm_idx;")
    schema_editor.execute("DROP INDEX IF EXISTS prices_sp_sku_trgm_idx;")
    schema_editor.execute("DROP INDEX IF EXISTS prices_sp_name_trgm_idx;")


class Migration(migrations.Migration):
    dependencies = [
        ("prices", "0030_supplierproduct_prices_sp_name_idx_and_more"),
    ]

    operations = [
        migrations.RunPython(create_trigram_indexes, drop_trigram_indexes),
    ]

