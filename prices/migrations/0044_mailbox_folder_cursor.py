from django.db import migrations, models
import django.db.models.deletion


def seed_folder_cursors(apps, schema_editor):
    Mailbox = apps.get_model("prices", "Mailbox")
    MailboxFolderCursor = apps.get_model("prices", "MailboxFolderCursor")
    for mailbox in Mailbox.objects.all():
        if mailbox.last_inbox_uid or mailbox.last_checked_at:
            MailboxFolderCursor.objects.update_or_create(
                mailbox=mailbox,
                folder="INBOX",
                defaults={
                    "last_uid": mailbox.last_inbox_uid or 0,
                    "last_checked_at": mailbox.last_checked_at,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ("prices", "0043_diag_mailbox_folder_uid_idx"),
    ]

    operations = [
        migrations.CreateModel(
            name="MailboxFolderCursor",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("folder", models.CharField(max_length=255)),
                ("last_uid", models.BigIntegerField(default=0)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "mailbox",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="folder_cursors",
                        to="prices.mailbox",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="mailboxfoldercursor",
            constraint=models.UniqueConstraint(
                fields=("mailbox", "folder"), name="uniq_mailbox_folder_cursor"
            ),
        ),
        migrations.AddIndex(
            model_name="mailboxfoldercursor",
            index=models.Index(
                fields=["mailbox", "folder"], name="prices_mfc_mailbox_folder_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="mailboxfoldercursor",
            index=models.Index(fields=["last_checked_at"], name="prices_mfc_checked_idx"),
        ),
        migrations.RunPython(seed_folder_cursors, migrations.RunPython.noop),
    ]
