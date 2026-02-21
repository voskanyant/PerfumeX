from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("prices", "0022_importsettings_supplier_timeout_minutes"),
    ]

    operations = [
        migrations.AddField(
            model_name="mailbox",
            name="last_all_mail_uid",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="mailbox",
            name="last_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mailbox",
            name="last_inbox_uid",
            field=models.BigIntegerField(default=0),
        ),
    ]

