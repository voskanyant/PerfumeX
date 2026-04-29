from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("prices", "0044_mailbox_folder_cursor"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="emailattachmentdiagnostic",
            name="prices_diag_mailbox_uid_idx",
        ),
    ]
