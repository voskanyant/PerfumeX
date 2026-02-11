from django.db import migrations, models
import re


def _normalize_identity(sku: str, name: str) -> str:
    if sku:
        return str(sku).strip()
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


def backfill_identity_key(apps, schema_editor):
    SupplierProduct = apps.get_model("prices", "SupplierProduct")
    for product in SupplierProduct.objects.all().iterator():
        key = _normalize_identity(product.supplier_sku, product.name)
        SupplierProduct.objects.filter(pk=product.pk).update(identity_key=key)


class Migration(migrations.Migration):
    dependencies = [
        ("prices", "0012_ourproduct_and_link"),
    ]

    operations = [
        migrations.AlterField(
            model_name="supplierproduct",
            name="supplier_sku",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="supplierproduct",
            name="identity_key",
            field=models.CharField(blank=True, db_index=True, max_length=255),
        ),
        migrations.AlterUniqueTogether(
            name="supplierproduct",
            unique_together=set(),
        ),
        migrations.RunPython(backfill_identity_key, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="supplierproduct",
            constraint=models.UniqueConstraint(
                fields=("supplier", "identity_key"),
                name="uniq_supplier_identity_key",
            ),
        ),
    ]
