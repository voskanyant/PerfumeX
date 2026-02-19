from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('prices', '0021_supplier_last_email_check_at_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='importsettings',
            name='supplier_timeout_minutes',
            field=models.PositiveIntegerField(default=5),
        ),
    ]
