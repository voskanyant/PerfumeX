"""
Create assistant-linking lookup indexes concurrently.

These tables can exceed 100k rows in production. Building indexes with
CREATE INDEX CONCURRENTLY avoids taking write-blocking table locks during
normal operator workflows.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("assistant_linking", "0005_manuallinkdecisionaudit"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS alink_parsed_locked_idx "
                        "ON assistant_linking_parsedsupplierproduct (locked_by_human);"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS alink_parsed_locked_idx;",
                ),
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS alink_sugg_product_status_idx "
                        "ON assistant_linking_linksuggestion (supplier_product_id, status);"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS alink_sugg_product_status_idx;",
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="linksuggestion",
                    index=models.Index(
                        fields=["supplier_product", "status"],
                        name="alink_sugg_product_status_idx",
                    ),
                ),
            ],
        ),
    ]
