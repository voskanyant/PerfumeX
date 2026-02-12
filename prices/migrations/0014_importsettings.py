from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("prices", "0013_supplierproduct_identity_key"),
    ]

    operations = [
        migrations.CreateModel(
            name="ImportSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enabled", models.BooleanField(default=True)),
                ("interval_minutes", models.PositiveIntegerField(default=120)),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
            ],
        ),
    ]
