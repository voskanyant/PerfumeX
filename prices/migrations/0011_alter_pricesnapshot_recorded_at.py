from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("prices", "0010_supplier_email_search_days"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pricesnapshot",
            name="recorded_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
    ]
