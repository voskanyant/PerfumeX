"""
Add a concurrent lookup index for Fragrantica linking suggestions.

The catalogue linking workbench repeatedly looks up staged Fragrantica rows by
normalized brand, link status, and normalized scent name. The table is large in
production, so the database index must be created concurrently.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("assistant_linking", "0059_seed_maison_alhambra_short_alias"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                        "alink_frag_bstat_name_idx "
                        "ON assistant_linking_fragranticaproduct "
                        "(normalized_brand_name, match_status, normalized_name);"
                    ),
                    reverse_sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS "
                        "alink_frag_bstat_name_idx;"
                    ),
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="fragranticaproduct",
                    index=models.Index(
                        fields=[
                            "normalized_brand_name",
                            "match_status",
                            "normalized_name",
                        ],
                        name="alink_frag_bstat_name_idx",
                    ),
                ),
            ],
        ),
    ]
