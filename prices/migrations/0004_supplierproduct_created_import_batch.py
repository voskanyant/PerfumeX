from django.db import migrations, models
import django.db.models.deletion


def backfill_created_import_batch(apps, schema_editor):
    SupplierProduct = apps.get_model("prices", "SupplierProduct")
    SupplierProduct.objects.filter(
        created_import_batch__isnull=True, last_import_batch__isnull=False
    ).update(created_import_batch=models.F("last_import_batch"))


class Migration(migrations.Migration):
    dependencies = [
        ("prices", "0003_supplierproduct_last_import_batch"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplierproduct",
            name="created_import_batch",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="created_products",
                to="prices.importbatch",
            ),
        ),
        migrations.RunPython(backfill_created_import_batch, migrations.RunPython.noop),
    ]
