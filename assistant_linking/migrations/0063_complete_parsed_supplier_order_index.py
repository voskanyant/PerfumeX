"""
Add an indexed order path for the Complete parsed products queue.

The live table can contain many saved parses. The page should return the first
visible rows through an indexed deterministic order instead of sorting by joined
supplier names before rendering.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("assistant_linking", "0062_parsed_queue_lookup_indexes"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                        "alink_parse_complete_sp_idx "
                        "ON assistant_linking_parsedsupplierproduct "
                        "(supplier_product_id, id) "
                        "WHERE normalized_brand_id IS NOT NULL "
                        "AND product_name_text <> '' "
                        "AND concentration <> '' "
                        "AND size_ml IS NOT NULL "
                        "AND NOT is_set;"
                    ),
                    reverse_sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS "
                        "alink_parse_complete_sp_idx;"
                    ),
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="parsedsupplierproduct",
                    index=models.Index(
                        fields=["supplier_product", "id"],
                        name="alink_parse_complete_sp_idx",
                        condition=(
                            models.Q(normalized_brand__isnull=False)
                            & ~models.Q(product_name_text="")
                            & ~models.Q(concentration="")
                            & models.Q(size_ml__isnull=False)
                            & models.Q(is_set=False)
                        ),
                    ),
                ),
            ],
        ),
    ]
