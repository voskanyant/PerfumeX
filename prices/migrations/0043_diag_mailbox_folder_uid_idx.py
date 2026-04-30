from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("prices", "0042_emailimportrun_prices_eir_sup_start_idx_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="emailattachmentdiagnostic",
            index=models.Index(
                fields=["mailbox", "message_folder", "message_uid"],
                name="prices_diag_mailbox_uid_idx",
            ),
        ),
    ]
