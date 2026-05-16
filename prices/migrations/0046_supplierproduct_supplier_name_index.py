"""
Add a concurrent supplier/name index for large supplier-product queues.

Assistant normalization pages render parsed rows ordered by supplier product.
This index supports that stable ordering without taking write-blocking locks
on live supplier-product imports.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("prices", "0045_remove_emailattachmentdiagnostic_prices_diag_mailbox_uid_idx"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                        "prices_sp_supplier_name_idx "
                        "ON prices_supplierproduct (supplier_id, name, id);"
                    ),
                    reverse_sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS "
                        "prices_sp_supplier_name_idx;"
                    ),
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="supplierproduct",
                    index=models.Index(
                        fields=["supplier", "name", "id"],
                        name="prices_sp_supplier_name_idx",
                    ),
                ),
            ],
        ),
    ]
