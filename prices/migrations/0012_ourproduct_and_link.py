from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("prices", "0011_alter_pricesnapshot_recorded_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="OurProduct",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=300)),
                ("brand", models.CharField(blank=True, max_length=200)),
                ("size", models.CharField(blank=True, max_length=100)),
                ("notes", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "unique_together": {("name", "brand", "size")},
            },
        ),
        migrations.AddField(
            model_name="supplierproduct",
            name="our_product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="prices.ourproduct",
            ),
        ),
    ]
