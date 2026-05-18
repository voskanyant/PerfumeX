"""
Store the Complete parsed queue membership as an indexed flag.

The complete parsed page is a live operator queue. Recomputing the full
complete-parse predicate on every GET is too expensive on production-sized
parse tables, so saves maintain this boolean and the list page reads it through
an index.
"""

from django.db import migrations, models


COMPLETE_SQL = """
normalized_brand_id IS NOT NULL
AND product_name_text <> ''
AND concentration <> ''
AND size_ml IS NOT NULL
AND NOT is_set
AND NOT (modifiers @> '["garbage"]'::jsonb)
AND NOT (modifiers @> '["manual_review"]'::jsonb)
AND NOT (modifiers @> '["bag"]'::jsonb OR variant_type = 'bag')
AND NOT (modifiers @> '["cosmetic_poudre"]'::jsonb OR variant_type = 'poudre')
AND NOT (modifiers @> '["deodorant"]'::jsonb OR variant_type = 'deodorant')
AND NOT (modifiers @> '["decant"]'::jsonb OR variant_type = 'decant')
AND NOT (modifiers @> '["vintage"]'::jsonb OR variant_type = 'vintage')
AND NOT (modifiers @> '["atomizer"]'::jsonb OR variant_type = 'atomizer')
"""


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("assistant_linking", "0063_complete_parsed_supplier_order_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="parsedsupplierproduct",
            name="is_complete_parse",
            field=models.BooleanField(default=False),
        ),
        migrations.RunSQL(
            sql=(
                "UPDATE assistant_linking_parsedsupplierproduct "
                f"SET is_complete_parse = ({COMPLETE_SQL});"
            ),
            reverse_sql=(
                "UPDATE assistant_linking_parsedsupplierproduct "
                "SET is_complete_parse = FALSE;"
            ),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS "
                        "alink_parse_complete_sp_idx;"
                    ),
                    reverse_sql=(
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
                ),
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                        "alink_parse_complete_sp_idx "
                        "ON assistant_linking_parsedsupplierproduct "
                        "(supplier_product_id, id) "
                        "WHERE is_complete_parse;"
                    ),
                    reverse_sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS "
                        "alink_parse_complete_sp_idx;"
                    ),
                ),
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                        "alink_parse_complete_flag_idx "
                        "ON assistant_linking_parsedsupplierproduct "
                        "(is_complete_parse, supplier_product_id, id);"
                    ),
                    reverse_sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS "
                        "alink_parse_complete_flag_idx;"
                    ),
                ),
            ],
            state_operations=[
                migrations.RemoveIndex(
                    model_name="parsedsupplierproduct",
                    name="alink_parse_complete_sp_idx",
                ),
                migrations.AddIndex(
                    model_name="parsedsupplierproduct",
                    index=models.Index(
                        fields=["supplier_product", "id"],
                        name="alink_parse_complete_sp_idx",
                        condition=models.Q(is_complete_parse=True),
                    ),
                ),
                migrations.AddIndex(
                    model_name="parsedsupplierproduct",
                    index=models.Index(
                        fields=["is_complete_parse", "supplier_product", "id"],
                        name="alink_parse_complete_flag_idx",
                    ),
                ),
            ],
        ),
    ]
