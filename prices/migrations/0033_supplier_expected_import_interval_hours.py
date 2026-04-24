from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("prices", "0032_userpreference_supplier_front_filters"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="expected_import_interval_hours",
            field=models.PositiveIntegerField(default=72),
        ),
    ]
