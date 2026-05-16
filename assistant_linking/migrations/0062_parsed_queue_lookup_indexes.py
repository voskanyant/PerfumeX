"""
Add concurrent lookup indexes for normalization queue pages.

Saved parsed rows can be large in production. These indexes keep the complete
and missing-field assistant queues from scanning the full parse table before
count-free pagination can return the visible rows.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("assistant_linking", "0061_fragrantica_search_trigram_indexes"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                        "alink_parse_complete_page_idx "
                        "ON assistant_linking_parsedsupplierproduct "
                        "(normalized_brand_id, concentration, size_ml, supplier_product_id) "
                        "WHERE product_name_text <> '' "
                        "AND concentration <> '' "
                        "AND size_ml IS NOT NULL "
                        "AND NOT is_set;"
                    ),
                    reverse_sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS "
                        "alink_parse_complete_page_idx;"
                    ),
                ),
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                        "alink_parse_missing_brand_idx "
                        "ON assistant_linking_parsedsupplierproduct "
                        "(supplier_product_id, id) "
                        "WHERE normalized_brand_id IS NULL;"
                    ),
                    reverse_sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS "
                        "alink_parse_missing_brand_idx;"
                    ),
                ),
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                        "alink_parse_missing_name_idx "
                        "ON assistant_linking_parsedsupplierproduct "
                        "(supplier_product_id, id) "
                        "WHERE product_name_text = '';"
                    ),
                    reverse_sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS "
                        "alink_parse_missing_name_idx;"
                    ),
                ),
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                        "alink_parse_missing_conc_idx "
                        "ON assistant_linking_parsedsupplierproduct "
                        "(supplier_product_id, id) "
                        "WHERE concentration = '';"
                    ),
                    reverse_sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS "
                        "alink_parse_missing_conc_idx;"
                    ),
                ),
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                        "alink_parse_missing_size_idx "
                        "ON assistant_linking_parsedsupplierproduct "
                        "(supplier_product_id, id) "
                        "WHERE size_ml IS NULL;"
                    ),
                    reverse_sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS "
                        "alink_parse_missing_size_idx;"
                    ),
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="parsedsupplierproduct",
                    index=models.Index(
                        fields=[
                            "normalized_brand",
                            "concentration",
                            "size_ml",
                            "supplier_product",
                        ],
                        name="alink_parse_complete_page_idx",
                        condition=(
                            ~models.Q(product_name_text="")
                            & ~models.Q(concentration="")
                            & models.Q(size_ml__isnull=False)
                            & models.Q(is_set=False)
                        ),
                    ),
                ),
                migrations.AddIndex(
                    model_name="parsedsupplierproduct",
                    index=models.Index(
                        fields=["supplier_product", "id"],
                        name="alink_parse_missing_brand_idx",
                        condition=models.Q(normalized_brand__isnull=True),
                    ),
                ),
                migrations.AddIndex(
                    model_name="parsedsupplierproduct",
                    index=models.Index(
                        fields=["supplier_product", "id"],
                        name="alink_parse_missing_name_idx",
                        condition=models.Q(product_name_text=""),
                    ),
                ),
                migrations.AddIndex(
                    model_name="parsedsupplierproduct",
                    index=models.Index(
                        fields=["supplier_product", "id"],
                        name="alink_parse_missing_conc_idx",
                        condition=models.Q(concentration=""),
                    ),
                ),
                migrations.AddIndex(
                    model_name="parsedsupplierproduct",
                    index=models.Index(
                        fields=["supplier_product", "id"],
                        name="alink_parse_missing_size_idx",
                        condition=models.Q(size_ml__isnull=True),
                    ),
                ),
            ],
        ),
    ]
