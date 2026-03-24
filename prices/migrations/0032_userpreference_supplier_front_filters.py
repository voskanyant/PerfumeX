from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("prices", "0031_supplierproduct_trigram_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="userpreference",
            name="supplier_front_filters",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]

