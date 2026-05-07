import io
import hashlib
import shutil
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock, call, patch

from django.contrib.messages import get_messages
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.test.utils import override_settings
from django.template import Context, Template
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from assistant_linking.models import (
    BrandAlias,
    FragranticaProduct,
    FragranticaProductLink,
    ParsedSupplierProduct,
)
from assistant_linking.models import ProductAlias
from catalog.models import Brand, Perfume, PerfumeVariant, Source
from prices import forms, models
from prices.management.commands.import_emails import (
    _get_supplier_latest_batch_time,
    _should_skip_recent_run,
)
from prices.services.email_importer import (
    _advance_mailbox_uid_cursor,
    _imap_fetch,
    _imap_search,
    _is_non_price_filename,
    _is_unnamed_body_part,
    _reason_from_error,
    run_import,
    _validate_spreadsheet_payload,
)
from prices.services import link_importer
from prices.services.background import run_in_background
from prices.services.autoimport_status import (
    build_autoimport_scan_status as _build_autoimport_scan_status,
)
from prices.services.catalog_review import catalogue_linking_perfume_label
from prices.services.import_scheduler import (
    build_cron_line as _build_cron_line,
    get_cron_status as _get_cron_status,
    render_runner_script as _render_runner_script,
)
from prices.services.supplier_board import (
    batch_activity_datetime as _batch_activity_datetime,
    build_email_run_status as _build_email_run_status,
    build_supplier_board_row as _build_supplier_board_row,
    collect_latest_successful_imports as _collect_latest_successful_imports,
    format_local_datetime as _format_local_datetime,
    summarize_latest_files as _summarize_latest_files,
)
from prices.services.import_operations import (
    process_supplier_price_payload as _process_supplier_price_payload,
)


class SharedUiComponentTests(TestCase):
    def test_page_query_preserves_filters_and_replaces_page_param(self):
        request = RequestFactory().get(
            "/admin/products/", {"q": "mango", "page": "2", "supplier": "7"}
        )
        rendered = Template("{% load prices_extras %}{% page_query 3 %}").render(
            Context({"request": request})
        )

        self.assertIn("q=mango", rendered)
        self.assertIn("supplier=7", rendered)
        self.assertIn("page=3", rendered)
        self.assertNotIn("page=2", rendered)

    def test_page_query_supports_custom_page_param(self):
        request = RequestFactory().get(
            "/admin/linking/", {"q": "mango", "sp_page": "2"}
        )
        rendered = Template(
            "{% load prices_extras %}{% page_query 4 'sp_page' %}"
        ).render(Context({"request": request}))

        self.assertIn("q=mango", rendered)
        self.assertIn("sp_page=4", rendered)
        self.assertNotIn("sp_page=2", rendered)


class FrontendHardeningTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="frontend-staff",
            password="pass",
            is_staff=True,
        )
        self.client.force_login(user)

    def test_supplier_list_escapes_script_tag(self):
        models.Supplier.objects.create(name="<script>alert(1)</script>")

        response = self.client.get(reverse("prices:supplier_list"), secure=True)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)

    def test_supplier_list_renders_img_payload_as_text(self):
        payload = "<img src=x onerror=alert(1)>"
        models.Supplier.objects.create(name=payload)

        response = self.client.get(reverse("prices:supplier_list"), secure=True)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertNotIn(payload, html)

    def test_supplier_list_labels_inactive_supplier_rows(self):
        models.Supplier.objects.create(name="Active Supplier", is_active=True)
        models.Supplier.objects.create(name="Inactive Supplier", is_active=False)

        response = self.client.get(reverse("prices:supplier_list"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Inactive suppliers")
        self.assertNotContains(response, "Inactive products")

    def test_product_filter_supplier_fixture_escapes_img_payload(self):
        payload = "<img src=x onerror=alert(1)>"
        supplier = models.Supplier.objects.create(name=payload)
        models.SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="xss-fixture-product",
            name="XSS Fixture Product",
        )

        response = self.client.get(reverse("prices:product_list"), secure=True)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertNotIn(payload, html)

    def test_supplier_product_page_hides_inactive_supplier_products_by_default(self):
        active_supplier = models.Supplier.objects.create(name="Active Supplier")
        inactive_supplier = models.Supplier.objects.create(
            name="Inactive Supplier",
            is_active=False,
        )
        models.SupplierProduct.objects.create(
            supplier=active_supplier,
            name="Visible Active Supplier Product",
        )
        models.SupplierProduct.objects.create(
            supplier=inactive_supplier,
            name="Hidden Inactive Supplier Product",
        )

        response = self.client.get(reverse("prices:product_list"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visible Active Supplier Product")
        self.assertNotContains(response, "Hidden Inactive Supplier Product")
        self.assertContains(response, "Inactive products")
        self.assertContains(response, "Show inactive product rows")
        self.assertContains(response, "Products from inactive suppliers")
        self.assertContains(response, "Show inactive supplier products")

        response = self.client.get(
            reverse("prices:product_list"),
            {"include_inactive_suppliers": "1"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hidden Inactive Supplier Product")

    def test_supplier_import_page_renders_server_side_tabs(self):
        supplier = models.Supplier.objects.create(
            name="Workbench Supplier",
            from_address_pattern="supplier@example.com",
        )

        response = self.client.get(
            reverse("prices:supplier_import", args=[supplier.pk]), secure=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="tabs supplier-source-tabs"')
        self.assertContains(response, "?source=email")
        self.assertContains(response, "?source=link")
        self.assertContains(response, "?source=file")
        self.assertContains(response, "Update from email")
        self.assertContains(response, "Update from link")
        self.assertContains(response, "Price file")
        self.assertContains(response, "Automatic mailbox scans use these rules")
        self.assertContains(response, "supplier-import-workbench")
        self.assertContains(response, "Mapping preview")
        self.assertContains(response, "These settings are used by email attachments")

    def test_supplier_import_file_tab_uses_workbench_layout(self):
        supplier = models.Supplier.objects.create(
            name="Workbench Supplier",
            from_address_pattern="supplier@example.com",
        )

        response = self.client.get(
            reverse("prices:supplier_import", args=[supplier.pk]),
            {"source": "file"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "supplier-import-workbench")
        self.assertContains(response, "supplier-upload-box")
        self.assertContains(response, "Mapping preview")
        self.assertNotContains(response, "Automatic mailbox scans use these rules")
        self.assertNotContains(response, "<p><label")

    def test_supplier_import_page_exposes_link_sources(self):
        supplier = models.Supplier.objects.create(
            name="Link Supplier",
            from_address_pattern="link@example.com",
        )
        models.SupplierPriceSource.objects.create(
            supplier=supplier,
            source_type=models.PriceSourceType.FIXED_LINK,
            provider=models.PriceSourceProvider.YANDEX_DISK,
            url="https://disk.yandex.ru/d/example",
            file_pattern="price",
        )

        response = self.client.get(
            reverse("prices:supplier_import", args=[supplier.pk]),
            {"source": "link"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Update from link")
        self.assertContains(response, "https://disk.yandex.ru/d/example")
        self.assertContains(response, "Import now")
        self.assertContains(response, "supplier-import-workbench")
        self.assertContains(response, "These settings are used by email attachments")


class MailboxPasswordSecurityTests(TestCase):
    def test_mailbox_password_round_trip(self):
        mailbox = models.Mailbox.objects.create(
            name="secure-mailbox",
            host="imap.example.com",
            username="secure@example.com",
            password="plain-secret-value",
        )

        mailbox.refresh_from_db()

        self.assertEqual(mailbox.password, "plain-secret-value")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT password FROM prices_mailbox WHERE id = %s", [mailbox.pk]
            )
            stored_password = cursor.fetchone()[0]
        self.assertNotEqual(stored_password, "plain-secret-value")

    def test_mailbox_flags_unreadable_encrypted_token(self):
        mailbox = models.Mailbox.objects.create(
            name="broken-mailbox",
            host="imap.example.com",
            username="broken@example.com",
            password="plain-secret-value",
        )
        encrypted_looking_value = "gAAAA" + ("x" * 115)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE prices_mailbox SET password = %s WHERE id = %s",
                [encrypted_looking_value, mailbox.pk],
            )

        mailbox.refresh_from_db()

        self.assertTrue(mailbox.password_requires_reset())

    def test_mailbox_form_requires_password_when_saved_value_is_unreadable(self):
        mailbox = models.Mailbox.objects.create(
            name="broken-form-mailbox",
            host="imap.example.com",
            username="broken-form@example.com",
            password="plain-secret-value",
        )
        encrypted_looking_value = "gAAAA" + ("x" * 115)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE prices_mailbox SET password = %s WHERE id = %s",
                [encrypted_looking_value, mailbox.pk],
            )
        mailbox.refresh_from_db()

        form = forms.MailboxForm(
            data={
                "protocol": models.Mailbox.IMAP,
                "name": mailbox.name,
                "host": mailbox.host,
                "port": mailbox.port,
                "username": mailbox.username,
                "password": "",
                "use_ssl": "on",
                "is_active": "on",
                "priority": mailbox.priority,
                "last_inbox_uid": mailbox.last_inbox_uid,
                "last_all_mail_uid": mailbox.last_all_mail_uid,
            },
            instance=mailbox,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)

    def test_mailbox_password_not_in_admin_html(self):
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        mailbox = models.Mailbox.objects.create(
            name="admin-mailbox",
            host="imap.example.com",
            username="admin-mailbox@example.com",
            password="html-secret-value",
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("admin:prices_mailbox_change", args=[mailbox.pk]),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "html-secret-value")


class LinkImporterTests(TestCase):
    def test_extract_links_from_email_body(self):
        message = EmailMessage()
        message.set_content(
            "Price link: https://disk.yandex.ru/d/abc123 and https://example.com/price.xlsx"
        )

        links = link_importer.extract_links_from_email(message)

        self.assertEqual(
            links,
            ["https://disk.yandex.ru/d/abc123", "https://example.com/price.xlsx"],
        )

    def test_source_matches_email_link_by_supplier_and_url_pattern(self):
        supplier = models.Supplier.objects.create(
            name="Ashot",
            from_address_pattern="ashot@example.com",
            price_subject_pattern="price",
        )
        source = models.SupplierPriceSource.objects.create(
            supplier=supplier,
            source_type=models.PriceSourceType.EMAIL_LINK,
            provider=models.PriceSourceProvider.YANDEX_DISK,
            url_pattern="disk.yandex.ru/d/",
        )

        matches = link_importer.source_matches_email(
            source,
            from_addr="ashot@example.com",
            subject="fresh price",
            links=["https://disk.yandex.ru/d/abc", "https://example.com/file.xlsx"],
        )

        self.assertEqual(matches, ["https://disk.yandex.ru/d/abc"])

    def test_direct_download_rejects_non_spreadsheet_filename(self):
        source = models.SupplierPriceSource(
            provider=models.PriceSourceProvider.DIRECT_URL,
            url="https://example.com/invoice.pdf",
        )
        with patch("prices.services.link_importer._http_get") as http_get:
            http_get.return_value = (b"data", "application/pdf", "", source.url)

            with self.assertRaises(link_importer.LinkImportError):
                link_importer.download_price_source(source)


class EmailImporterCursorTests(TestCase):
    def setUp(self):
        self.supplier = models.Supplier.objects.create(
            name="Cursor Supplier",
            code="cursor-supplier",
            from_address_pattern="supplier@example.com",
            price_subject_pattern="price",
            price_filename_pattern="prices",
        )
        self.mailbox = models.Mailbox.objects.create(
            name="cursor-mailbox",
            host="imap.example.com",
            username="cursor@example.com",
            password="secret",
        )

    def test_imap_helpers_use_uid_ids_for_search_and_fetch(self):
        class FakeUidClient:
            def __init__(self):
                self.calls = []

            def uid(self, command, *args):
                self.calls.append((command, args))
                if command == "SEARCH":
                    return "OK", [b"347326"]
                if command == "FETCH":
                    return "OK", [(b"347326 (RFC822.SIZE 100)", b"payload")]
                return "OK", []

        client = FakeUidClient()

        search_status, search_data, search_client = _imap_search(
            client,
            self.mailbox,
            ["SINCE", "29-Apr-2026"],
            logger=None,
        )
        fetch_status, fetch_data, fetch_client = _imap_fetch(
            client,
            self.mailbox,
            b"347326",
            "(BODY.PEEK[])",
            logger=None,
        )

        self.assertEqual(search_status, "OK")
        self.assertEqual(search_data, [b"347326"])
        self.assertIs(search_client, client)
        self.assertEqual(fetch_status, "OK")
        self.assertEqual(fetch_data, [(b"347326 (RFC822.SIZE 100)", b"payload")])
        self.assertIs(fetch_client, client)
        self.assertEqual(
            client.calls,
            [
                ("SEARCH", (None, "SINCE", "29-Apr-2026")),
                ("FETCH", (b"347326", "(BODY.PEEK[])")),
            ],
        )

    def test_import_batch_unique_constraint_enforced_for_mailbox_message_id(self):
        models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<unique@example.com>",
            status=models.ImportStatus.PROCESSED,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                models.ImportBatch.objects.create(
                    supplier=self.supplier,
                    mailbox=self.mailbox,
                    message_id="<unique@example.com>",
                    status=models.ImportStatus.PENDING,
                )

        models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="",
            status=models.ImportStatus.PENDING,
        )
        models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="",
            status=models.ImportStatus.PENDING,
        )
        self.assertEqual(
            models.ImportBatch.objects.filter(
                mailbox=self.mailbox, message_id=""
            ).count(),
            2,
        )

    def test_uid_cursor_only_advances_after_commit(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                models.ImportBatch.objects.create(
                    supplier=self.supplier,
                    mailbox=self.mailbox,
                    message_id="<rollback@example.com>",
                    status=models.ImportStatus.PENDING,
                )
                _advance_mailbox_uid_cursor(self.mailbox.pk, "last_inbox_uid", 42)
                raise RuntimeError("rollback transaction")

        self.mailbox.refresh_from_db()
        self.assertEqual(self.mailbox.last_inbox_uid, 0)
        self.assertFalse(
            models.ImportBatch.objects.filter(
                message_id="<rollback@example.com>"
            ).exists()
        )

        with transaction.atomic():
            models.ImportBatch.objects.create(
                supplier=self.supplier,
                mailbox=self.mailbox,
                message_id="<commit@example.com>",
                status=models.ImportStatus.PENDING,
            )
            _advance_mailbox_uid_cursor(self.mailbox.pk, "last_inbox_uid", 42)

        self.mailbox.refresh_from_db()
        self.assertEqual(self.mailbox.last_inbox_uid, 42)
        cursor = models.MailboxFolderCursor.objects.get(
            mailbox=self.mailbox, folder="INBOX"
        )
        self.assertEqual(cursor.last_uid, 42)
        self.assertTrue(
            models.ImportBatch.objects.filter(
                message_id="<commit@example.com>"
            ).exists()
        )

    def test_uid_cursor_is_scoped_by_mailbox_folder(self):
        self.mailbox.last_all_mail_uid = 500
        self.mailbox.save(update_fields=["last_all_mail_uid"])

        with transaction.atomic():
            advanced = _advance_mailbox_uid_cursor(self.mailbox.pk, "Archive", 42)

        self.assertTrue(advanced)
        archive_cursor = models.MailboxFolderCursor.objects.get(
            mailbox=self.mailbox,
            folder="Archive",
        )
        self.assertEqual(archive_cursor.last_uid, 42)
        self.mailbox.refresh_from_db()
        self.assertEqual(self.mailbox.last_all_mail_uid, 500)

    def test_reactivating_mailbox_resets_folder_cursors(self):
        models.MailboxFolderCursor.objects.create(
            mailbox=self.mailbox,
            folder="Archive",
            last_uid=42,
            last_checked_at=timezone.now(),
        )
        self.mailbox.is_active = False
        self.mailbox.save(update_fields=["is_active"])
        self.mailbox.last_inbox_uid = 100
        self.mailbox.last_all_mail_uid = 500
        self.mailbox.last_checked_at = timezone.now()
        self.mailbox.save(
            update_fields=["last_inbox_uid", "last_all_mail_uid", "last_checked_at"]
        )

        self.mailbox.is_active = True
        self.mailbox.save(
            update_fields=[
                "is_active",
                "last_inbox_uid",
                "last_all_mail_uid",
                "last_checked_at",
            ]
        )

        self.mailbox.refresh_from_db()
        self.assertEqual(self.mailbox.last_inbox_uid, 0)
        self.assertEqual(self.mailbox.last_all_mail_uid, 0)
        self.assertIsNone(self.mailbox.last_checked_at)
        self.assertFalse(
            models.MailboxFolderCursor.objects.filter(mailbox=self.mailbox).exists()
        )

    def test_gmail_all_mail_cursor_seeds_from_legacy_all_mail_uid(self):
        self.mailbox.last_all_mail_uid = 500
        self.mailbox.save(update_fields=["last_all_mail_uid"])

        with transaction.atomic():
            advanced = _advance_mailbox_uid_cursor(
                self.mailbox.pk,
                "[Gmail]/All Mail",
                450,
            )

        self.assertFalse(advanced)
        gmail_cursor = models.MailboxFolderCursor.objects.get(
            mailbox=self.mailbox,
            folder="[Gmail]/All Mail",
        )
        self.assertEqual(gmail_cursor.last_uid, 500)

    def test_duplicate_message_id_skipped_not_crashed(self):
        payload = b"sku,price\nA,1\n"
        existing_batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<duplicate@example.com>",
            status=models.ImportStatus.PROCESSED,
        )
        models.ImportFile.objects.create(
            import_batch=existing_batch,
            file_kind=models.FileKind.PRICE,
            filename="prices.csv",
            content_hash=hashlib.sha256(payload).hexdigest(),
            status=models.ImportStatus.PROCESSED,
        )
        message = EmailMessage()
        message["Subject"] = "Daily price"
        message["From"] = "supplier@example.com"
        message["Message-ID"] = "<duplicate@example.com>"
        message["Date"] = "Sat, 25 Apr 2026 10:00:00 +0000"
        message.set_content("attached")
        message.add_attachment(
            payload,
            maintype="text",
            subtype="csv",
            filename="prices.csv",
        )

        class FakeImapClient:
            def search(self, charset, *criteria):
                return "OK", [b"7"]

            def fetch(self, msg_id, query):
                if "RFC822.SIZE" in query:
                    return "OK", [
                        (
                            b'7 (RFC822.SIZE 100 INTERNALDATE "25-Apr-2026 10:00:00 +0000")',
                            b"",
                        )
                    ]
                return "OK", [(b"7 (RFC822 {100}", message.as_bytes())]

            def logout(self):
                return "BYE", []

        with patch(
            "prices.services.email_importer._connect_imap",
            return_value=FakeImapClient(),
        ):
            summary = run_import([self.mailbox], use_uid_cursor=True)

        self.mailbox.refresh_from_db()
        self.assertEqual(summary["skipped_duplicates"], 1)
        self.assertEqual(models.ImportBatch.objects.count(), 1)
        self.assertEqual(models.ImportFile.objects.count(), 1)
        self.assertEqual(self.mailbox.last_inbox_uid, 7)

    def test_uid_cursor_recovers_unseen_recent_uid_below_cursor_once(self):
        payload = b"sku,price\nA,1\n"
        self.mailbox.last_inbox_uid = 10
        self.mailbox.save(update_fields=["last_inbox_uid"])
        existing_batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<existing@example.com>",
            received_at=timezone.make_aware(datetime(2026, 4, 29, 0, 0, 0)),
            status=models.ImportStatus.PROCESSED,
        )
        models.ImportFile.objects.create(
            import_batch=existing_batch,
            file_kind=models.FileKind.PRICE,
            filename="prices.csv",
            content_hash=hashlib.sha256(payload).hexdigest(),
            status=models.ImportStatus.PROCESSED,
            processed_at=timezone.make_aware(datetime(2026, 4, 29, 0, 1, 0)),
        )
        message = EmailMessage()
        message["Subject"] = "Daily price"
        message["From"] = "supplier@example.com"
        message["Message-ID"] = "<late-low-uid@example.com>"
        message["Date"] = "Wed, 29 Apr 2026 00:24:00 +0000"
        message.set_content("attached")
        message.add_attachment(
            payload,
            maintype="text",
            subtype="csv",
            filename="prices.csv",
        )

        class FakeImapClient:
            def search(self, charset, *criteria):
                return "OK", [b"9"]

            def fetch(self, msg_id, query):
                if "RFC822.SIZE" in query:
                    return "OK", [
                        (
                            b'9 (RFC822.SIZE 100 INTERNALDATE "29-Apr-2026 00:24:00 +0000")',
                            b"",
                        )
                    ]
                return "OK", [(b"9 (RFC822 {100}", message.as_bytes())]

            def logout(self):
                return "BYE", []

        with patch(
            "prices.services.email_importer._connect_imap",
            return_value=FakeImapClient(),
        ):
            first_summary = run_import([self.mailbox], use_uid_cursor=True)

        self.mailbox.refresh_from_db()
        self.assertEqual(first_summary["messages_scanned"], 1)
        self.assertEqual(first_summary["skipped_duplicates"], 1)
        self.assertEqual(self.mailbox.last_inbox_uid, 10)
        self.assertTrue(
            models.EmailAttachmentDiagnostic.objects.filter(
                mailbox=self.mailbox,
                message_folder="INBOX",
                message_uid="9",
                decision=models.AttachmentDecision.DUPLICATE,
            ).exists()
        )

        with patch(
            "prices.services.email_importer._connect_imap",
            return_value=FakeImapClient(),
        ):
            second_summary = run_import([self.mailbox], use_uid_cursor=True)

        self.assertEqual(second_summary["messages_scanned"], 0)
        self.assertEqual(second_summary["skipped_duplicates"], 0)

    def test_same_day_duplicate_attachment_does_not_order_by_missing_import_file_created_at(
        self,
    ):
        payload = b"sku,price\nA,1\n"
        existing_batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<same-day-existing@example.com>",
            received_at=timezone.make_aware(datetime(2026, 4, 29, 9, 0, 0)),
            status=models.ImportStatus.PROCESSED,
        )
        models.ImportFile.objects.create(
            import_batch=existing_batch,
            file_kind=models.FileKind.PRICE,
            filename="prices.csv",
            content_hash=hashlib.sha256(payload).hexdigest(),
            status=models.ImportStatus.PROCESSED,
            processed_at=timezone.make_aware(datetime(2026, 4, 29, 9, 1, 0)),
        )
        message = EmailMessage()
        message["Subject"] = "Daily price"
        message["From"] = "supplier@example.com"
        message["Message-ID"] = "<same-day-new-copy@example.com>"
        message["Date"] = "Wed, 29 Apr 2026 10:00:00 +0000"
        message.set_content("attached")
        message.add_attachment(
            payload,
            maintype="text",
            subtype="csv",
            filename="prices.csv",
        )

        class FakeImapClient:
            def search(self, charset, *criteria):
                return "OK", [b"11"]

            def fetch(self, msg_id, query):
                if "RFC822.SIZE" in query:
                    return "OK", [
                        (
                            b'11 (RFC822.SIZE 100 INTERNALDATE "29-Apr-2026 10:00:00 +0000")',
                            b"",
                        )
                    ]
                return "OK", [(b"11 (RFC822 {100}", message.as_bytes())]

            def logout(self):
                return "BYE", []

        with patch(
            "prices.services.email_importer._connect_imap",
            return_value=FakeImapClient(),
        ):
            summary = run_import(
                [self.mailbox],
                use_uid_cursor=True,
                dedupe_same_day_only=True,
            )

        self.assertEqual(summary["skipped_duplicates"], 1)
        self.assertEqual(models.ImportBatch.objects.count(), 1)
        self.assertEqual(models.ImportFile.objects.count(), 1)

    def test_supplier_specific_run_updates_check_state_when_no_email_found(self):
        class EmptyImapClient:
            def search(self, charset, *criteria):
                return "OK", [b""]

            def logout(self):
                return "BYE", []

        with patch(
            "prices.services.email_importer._connect_imap",
            return_value=EmptyImapClient(),
        ):
            summary = run_import([self.mailbox], supplier_id=self.supplier.id)

        self.supplier.refresh_from_db()
        self.assertEqual(summary["messages_found"], 0)
        self.assertIsNotNone(self.supplier.last_email_check_at)
        self.assertEqual(self.supplier.last_email_matched, 0)
        self.assertEqual(self.supplier.last_email_processed, 0)
        self.assertEqual(
            self.supplier.last_email_last_message,
            "Supplier-specific check found no price email.",
        )


class BulkMutationPermissionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="bulk-user",
            password="password",
        )
        self.client.force_login(self.user)
        self.supplier = models.Supplier.objects.create(
            name="Bulk Supplier",
            code="bulk-supplier",
            from_address_pattern="supplier@example.com",
        )

    def test_non_staff_user_cannot_post_bulk_mutation_endpoints(self):
        endpoints = [
            ("prices:import_delete_bulk", {}),
            ("prices:product_cleanup", {}),
            ("prices:product_cleanup_inactive", {}),
            ("prices:product_bulk_delete", {}),
            (
                "prices:supplier_import_email_backfill_bulk",
                {"supplier_ids": [str(self.supplier.id)], "start_date": "2026-04-01"},
            ),
            ("prices:supplier_rates_recalculate", {}),
            ("prices:supplier_reimport_all_prices", {}),
            ("prices:currency_rate_delete_bulk", {}),
        ]

        for url_name, data in endpoints:
            with self.subTest(url_name=url_name):
                response = self.client.post(reverse(url_name), data, secure=True)
                self.assertEqual(response.status_code, 403)

    def test_mapping_preview_requires_csrf_and_accepts_token(self):
        staff = get_user_model().objects.create_user(
            username="preview-staff",
            password="password",
            is_staff=True,
        )
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(staff)

        response = csrf_client.get(
            reverse("prices:supplier_import", args=[self.supplier.pk]),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "csrfmiddlewaretoken")
        token = csrf_client.cookies["csrftoken"].value

        missing_token_response = csrf_client.post(
            reverse("prices:supplier_mapping_preview", args=[self.supplier.pk]),
            {"file": SimpleUploadedFile("prices.csv", b"sku,name,price\n1,A,10\n")},
            secure=True,
        )
        self.assertEqual(missing_token_response.status_code, 403)

        ok_response = csrf_client.post(
            reverse("prices:supplier_mapping_preview", args=[self.supplier.pk]),
            {
                "csrfmiddlewaretoken": token,
                "file": SimpleUploadedFile("prices.csv", b"sku,name,price\n1,A,10\n"),
            },
            HTTP_X_CSRFTOKEN=token,
            HTTP_REFERER="https://testserver"
            + reverse("prices:supplier_import", args=[self.supplier.pk]),
            secure=True,
        )
        self.assertEqual(ok_response.status_code, 200)
        self.assertIn("rows", ok_response.json())


class AdminPanelReadOnlyAccessTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="readonly-user",
            password="password",
            is_staff=False,
        )
        self.client.force_login(self.user)
        self.supplier = models.Supplier.objects.create(
            name="Viewer Supplier",
            from_address_pattern="supplier@example.com",
        )
        self.mapping = models.SupplierFileMapping.objects.create(
            supplier=self.supplier,
            file_kind=models.FileKind.PRICE,
            header_row=1,
            column_map={"sku": 1, "name": 2, "price": 3},
        )

    def test_non_staff_user_can_view_import_prices_board(self):
        response = self.client.get(reverse("viewer_import_prices"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Supplier import board")
        self.assertContains(response, "Scan mailboxes now")
        self.assertContains(response, 'aria-label="Quick upload"')
        self.assertContains(response, 'aria-label="Update from email"')
        self.assertNotContains(response, 'aria-label="Edit mapping"')

    def test_non_staff_user_can_poll_import_prices_board_status(self):
        response = self.client.get(reverse("prices:supplier_import_email_status_all"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")

    def test_non_staff_user_cannot_view_other_admin_pages(self):
        response = self.client.get(reverse("prices:import_settings"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")

    def test_non_staff_user_can_start_mailbox_scan_from_board(self):
        with patch(
            "prices.views_imports.run_supplier_board_mailbox_scan_action",
            return_value=SimpleNamespace(
                message_level="info",
                message="Mailbox scan started.",
            ),
        ) as scan_action:
            response = self.client.post(
                reverse("prices:supplier_import_email_all"),
                {"next": reverse("viewer_import_prices")},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("viewer_import_prices"))
        scan_action.assert_called_once_with()

    def test_non_staff_user_can_start_supplier_email_update_from_board(self):
        with patch(
            "prices.views_imports.run_supplier_email_import_action",
            return_value=SimpleNamespace(
                message_level="info",
                message="Email import started.",
            ),
        ) as import_action:
            response = self.client.post(
                reverse("prices:supplier_import_email", args=[self.supplier.pk]),
                {"next": reverse("viewer_import_prices")},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("viewer_import_prices"))
        import_action.assert_called_once_with(self.supplier)

    def test_non_staff_user_can_quick_upload_from_board(self):
        upload = SimpleUploadedFile("viewer.csv", b"sku,name,price\n1,Item,10\n")
        with patch(
            "prices.views_imports.run_supplier_quick_upload_action",
            return_value=SimpleNamespace(
                message_level="success",
                message="Upload imported.",
                redirect_source="",
            ),
        ) as upload_action:
            response = self.client.post(
                reverse("prices:supplier_quick_upload", args=[self.supplier.pk]),
                {"next": reverse("viewer_import_prices"), "file": upload},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("viewer_import_prices"))
        upload_action.assert_called_once()


class BackgroundRunSafetyTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="background-staff",
            password="password",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)
        self.supplier = models.Supplier.objects.create(name="Background Supplier")

    def test_background_failure_marks_email_run_failed(self):
        run = models.EmailImportRun.objects.create(
            supplier=self.supplier,
            status=models.EmailImportStatus.RUNNING,
        )

        def failing_task():
            raise RuntimeError("background broke")

        with patch("prices.services.background.logger.exception"):
            thread = run_in_background(failing_task, run_id=run.id, label="test-task")
            thread.join(timeout=5)

        run.refresh_from_db()
        self.assertEqual(run.status, models.EmailImportStatus.FAILED)
        self.assertIn("background broke", run.last_message)

    def test_stuck_runs_view_lists_and_marks_failed(self):
        old_activity = timezone.now() - timezone.timedelta(minutes=45)
        run = models.EmailImportRun.objects.create(
            supplier=self.supplier,
            status=models.EmailImportStatus.RUNNING,
            last_message="still running",
        )
        models.EmailImportRun.objects.filter(id=run.id).update(updated_at=old_activity)

        response = self.client.get(
            reverse("prices:stuck_email_import_runs"), secure=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Background Supplier")
        self.assertContains(response, "still running")

        response = self.client.post(
            reverse("prices:stuck_email_import_runs"),
            {"run_id": str(run.id)},
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        run.refresh_from_db()
        self.assertEqual(run.status, models.EmailImportStatus.FAILED)
        self.assertEqual(run.last_message, "Marked failed from stuck-run recovery.")

    def test_scan_all_starts_mailbox_cursor_import_not_supplier_runs(self):
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.save(update_fields=["from_address_pattern"])

        with patch(
            "prices.views_imports.run_supplier_board_mailbox_scan_action",
            return_value=SimpleNamespace(
                message_level="info",
                message="Mailbox scan started.",
            ),
        ) as scan_action:
            response = self.client.post(
                reverse("prices:supplier_import_email_all"),
                secure=True,
            )

        self.assertEqual(response.status_code, 302)
        scan_action.assert_called_once_with()
        self.assertFalse(models.EmailImportRun.objects.exists())

    def test_cbr_range_sync_failure_is_visible(self):
        with patch(
            "prices.views_currency._upsert_cbr_markup_rates_range",
            side_effect=RuntimeError("CBR down"),
        ):
            response = self.client.post(
                reverse("prices:currency_rates"),
                {
                    "action": "sync_cbr_range",
                    "start_date": "2026-04-24",
                    "end_date": "2026-04-25",
                },
                secure=True,
            )

        self.assertEqual(response.status_code, 302)
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(
            any("Failed to sync CBR range: CBR down" in message for message in messages)
        )


class JobQueueTests(SimpleTestCase):
    @patch("prices.services.job_queue.close_old_connections")
    @patch("prices.services.job_queue.call_command")
    def test_run_management_command_closes_connections_around_success(
        self,
        mock_call_command,
        mock_close_old_connections,
    ):
        from prices.services.job_queue import _run_management_command

        _run_management_command(
            "import_emails",
            ("--force",),
            {"supplier_id": 7},
        )

        mock_call_command.assert_called_once_with(
            "import_emails",
            "--force",
            supplier_id=7,
        )
        self.assertEqual(mock_close_old_connections.call_count, 2)

    @patch("prices.services.job_queue.close_old_connections")
    @patch("prices.services.job_queue.call_command", side_effect=RuntimeError("boom"))
    def test_run_management_command_closes_connections_after_failure(
        self,
        mock_call_command,
        mock_close_old_connections,
    ):
        from prices.services.job_queue import _run_management_command

        with self.assertRaisesMessage(RuntimeError, "boom"):
            _run_management_command("import_emails", (), {})

        mock_call_command.assert_called_once_with("import_emails")
        self.assertEqual(mock_close_old_connections.call_count, 2)

    @override_settings(PERFUMEX_RQ_SYNC=True, RQ_DEFAULT_QUEUE="test-queue")
    @patch("prices.services.job_queue.call_command")
    def test_enqueue_management_command_runs_synchronously_when_configured(
        self, mock_call_command
    ):
        from prices.services.job_queue import enqueue_management_command

        result = enqueue_management_command(
            "refresh_normalization_stats",
            all_user_scopes=True,
            description="Refresh stats",
        )

        mock_call_command.assert_called_once_with(
            "refresh_normalization_stats",
            all_user_scopes=True,
        )
        self.assertFalse(result.queued)
        self.assertEqual(result.queue_name, "test-queue")
        self.assertEqual(result.status, "finished")
        self.assertEqual(result.description, "Refresh stats")

    @override_settings(
        PERFUMEX_RQ_SYNC=False,
        REDIS_URL="redis://redis.example/0",
        RQ_DEFAULT_QUEUE="async-queue",
        RQ_JOB_TIMEOUT_SECONDS=123,
    )
    @patch("prices.services.job_queue._import_rq")
    def test_enqueue_management_command_queues_when_async(self, mock_import_rq):
        from prices.services.job_queue import enqueue_management_command

        captured = {}

        class FakeRedis:
            @staticmethod
            def from_url(url):
                captured["redis_url"] = url
                return "redis-connection"

        class FakeQueue:
            def __init__(self, name, connection):
                captured["queue_name"] = name
                captured["connection"] = connection

            def enqueue(self, *args, **kwargs):
                captured["enqueue_args"] = args
                captured["enqueue_kwargs"] = kwargs
                return SimpleNamespace(id="job-123")

        mock_import_rq.return_value = (
            SimpleNamespace(Redis=FakeRedis),
            SimpleNamespace(Queue=FakeQueue),
        )

        result = enqueue_management_command(
            "import_emails",
            "--force",
            description="Import emails",
        )

        self.assertTrue(result.queued)
        self.assertEqual(result.job_id, "job-123")
        self.assertEqual(result.queue_name, "async-queue")
        self.assertEqual(result.status, "queued")
        self.assertEqual(captured["redis_url"], "redis://redis.example/0")
        self.assertEqual(captured["queue_name"], "async-queue")
        self.assertEqual(captured["connection"], "redis-connection")
        self.assertEqual(captured["enqueue_args"][1], "import_emails")
        self.assertEqual(captured["enqueue_args"][2], ("--force",))
        self.assertEqual(captured["enqueue_args"][3], {})
        self.assertEqual(captured["enqueue_kwargs"]["job_timeout"], 123)
        self.assertEqual(captured["enqueue_kwargs"]["description"], "Import emails")

    @override_settings(
        PERFUMEX_RQ_SYNC=False,
        REDIS_URL="redis://redis.example/0",
        RQ_DEFAULT_QUEUE="async-queue",
    )
    @patch("prices.services.job_queue._import_rq")
    def test_enqueue_management_command_fails_without_worker(self, mock_import_rq):
        from prices.services.job_queue import enqueue_management_command

        class FakeRedis:
            @staticmethod
            def from_url(_url):
                return "redis-connection"

        class FakeWorker:
            @staticmethod
            def all(connection):
                self.assertEqual(connection, "redis-connection")
                return []

        mock_import_rq.return_value = (
            SimpleNamespace(Redis=FakeRedis),
            SimpleNamespace(Queue=Mock(), Worker=FakeWorker),
        )

        with self.assertRaisesMessage(RuntimeError, "No active RQ worker"):
            enqueue_management_command("import_emails", description="Import emails")

    @override_settings(
        PERFUMEX_RQ_SYNC=False,
        REDIS_URL="redis://redis.example/0",
        RQ_DEFAULT_QUEUE="async-queue",
    )
    @patch("prices.services.job_queue._import_rq")
    def test_enqueue_management_command_accepts_worker_for_queue(self, mock_import_rq):
        from prices.services.job_queue import enqueue_management_command

        captured = {}

        class FakeRedis:
            @staticmethod
            def from_url(_url):
                return "redis-connection"

        class FakeWorker:
            @staticmethod
            def all(connection):
                self.assertEqual(connection, "redis-connection")
                return [SimpleNamespace(queue_names=lambda: ["async-queue"])]

        class FakeQueue:
            def __init__(self, name, connection):
                captured["queue_name"] = name
                captured["connection"] = connection

            def enqueue(self, *args, **kwargs):
                return SimpleNamespace(id="job-123")

        mock_import_rq.return_value = (
            SimpleNamespace(Redis=FakeRedis),
            SimpleNamespace(Queue=FakeQueue, Worker=FakeWorker),
        )

        result = enqueue_management_command(
            "import_emails", description="Import emails"
        )

        self.assertTrue(result.queued)
        self.assertEqual(result.job_id, "job-123")
        self.assertEqual(captured["queue_name"], "async-queue")


class SupplierBoardServiceTests(SimpleTestCase):
    def test_business_interval_skips_weekends_for_daily_cadence(self):
        from prices.services.supplier_board import add_business_interval

        friday = timezone.make_aware(datetime(2026, 4, 24, 10, 0, 0))
        deadline = add_business_interval(friday, 24)

        self.assertEqual(deadline.weekday(), 0)
        self.assertEqual(deadline.day, 27)

    def test_expected_cadence_defaults_to_daily_weekdays(self):
        from prices.services.supplier_board import format_expected_cadence

        supplier = SimpleNamespace(expected_import_interval_hours=0)

        self.assertEqual(format_expected_cadence(supplier), "daily, weekdays")

    def test_short_relative_datetime_formats_recent_past(self):
        from prices.services.supplier_board import short_relative_datetime

        now = timezone.make_aware(datetime(2026, 4, 30, 12, 0, 0))
        value = now - timedelta(hours=2)

        with patch("prices.services.supplier_board.timezone.now", return_value=now):
            self.assertEqual(short_relative_datetime(value), "2h ago")

    def test_benign_skipped_diagnostic_summarizes_as_ignored(self):
        from prices.services.supplier_board import summarize_latest_files

        supplier = SimpleNamespace(last_email_check_at=None)
        diagnostic = SimpleNamespace(
            decision=models.AttachmentDecision.SKIPPED,
            reason_code=models.AttachmentReason.INVOICE_OR_REPORT,
        )

        self.assertEqual(
            summarize_latest_files(
                supplier, latest_run=None, latest_diagnostic=diagnostic
            ),
            "Ignored non-price file",
        )

    def test_problem_note_surfaces_failed_diagnostic_reason(self):
        from prices.services.supplier_board import build_problem_note

        diagnostic = SimpleNamespace(
            decision=models.AttachmentDecision.QUARANTINED,
            reason_code=models.AttachmentReason.MAPPING_MISSING,
            message="Mapping is missing.",
            filename="daily.xlsx",
        )

        note = build_problem_note(
            supplier=SimpleNamespace(),
            latest_check={"code": "failed", "note": "failed"},
            health={"code": "critical", "note": "critical"},
            latest_diagnostic=diagnostic,
        )

        self.assertIn("daily.xlsx", note)
        self.assertIn("Mapping is missing", note)

    def test_supplier_board_summary_counts_health_and_running_rows(self):
        from prices.services.supplier_board import build_supplier_board_summary

        summary = build_supplier_board_summary(
            [
                {"is_running": True, "health_code": "fresh"},
                {"is_running": False, "health_code": "warning"},
                {"is_running": False, "health_code": "warning"},
            ]
        )

        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["updating"], 1)
        self.assertEqual(summary["fresh"], 1)
        self.assertEqual(summary["warning"], 2)

    def test_serialize_supplier_email_status_row_keeps_json_contract_keys(self):
        from prices.services.supplier_board import (
            SUPPLIER_EMAIL_STATUS_ROW_KEYS,
            serialize_supplier_email_status_row,
        )

        row = {key: key for key in SUPPLIER_EMAIL_STATUS_ROW_KEYS}
        row["supplier"] = object()
        row["latest_log_url"] = "/admin/import/logs/"

        serialized = serialize_supplier_email_status_row(row)

        self.assertEqual(set(serialized), set(SUPPLIER_EMAIL_STATUS_ROW_KEYS))
        self.assertNotIn("supplier", serialized)
        self.assertNotIn("latest_log_url", serialized)

    def test_build_supplier_email_status_payload_builds_rows_and_summary(self):
        from prices.services.supplier_board import (
            SUPPLIER_EMAIL_STATUS_ROW_KEYS,
            build_supplier_email_status_payload,
        )

        supplier = SimpleNamespace(id=12)
        row = {key: "" for key in SUPPLIER_EMAIL_STATUS_ROW_KEYS}
        row.update(
            {
                "is_running": True,
                "has_email_route": True,
                "health_code": "fresh",
            }
        )

        with patch(
            "prices.services.supplier_board.build_supplier_board_row",
            return_value=row,
        ) as mock_build_row:
            payload = build_supplier_email_status_payload(
                [supplier],
                latest_successful_imports={12: "batch"},
                latest_failed_import_files={12: "failed-file"},
                latest_attachment_diagnostics={12: "diagnostic"},
                latest_runs={12: "run"},
                run_streaks={12: 3},
                scanner_status={"cron_status": "private", "label": "ready"},
                worker_busy=True,
            )

        mock_build_row.assert_called_once_with(
            supplier=supplier,
            successful_batch="batch",
            latest_run="run",
            streak_count=3,
            latest_failed_file="failed-file",
            latest_diagnostic="diagnostic",
        )
        self.assertEqual(payload["rows"]["12"]["health_code"], "fresh")
        self.assertEqual(payload["summary"]["total"], 1)
        self.assertEqual(payload["summary"]["updating"], 1)
        self.assertEqual(payload["summary"]["fresh"], 1)
        self.assertEqual(payload["scanner"], {"label": "ready"})
        self.assertTrue(payload["worker_busy"])

    def test_supplier_email_import_status_all_payload_coordinates_dependencies(self):
        from prices.services.supplier_board import (
            supplier_email_import_status_all_payload,
        )

        calls = []
        suppliers = [SimpleNamespace(id=2), SimpleNamespace(id=1)]

        class SupplierManager:
            def order_by(self, *fields):
                calls.append(("order_by", fields))
                return suppliers

        def expire():
            calls.append(("expire",))

        def scanner_status():
            calls.append(("scanner",))
            return {"label": "ready"}

        def worker_busy():
            calls.append(("worker",))
            return True

        def payload_builder(supplier_rows, *, scanner_status, worker_busy):
            calls.append(("payload", supplier_rows, scanner_status, worker_busy))
            return {"ok": True}

        payload = supplier_email_import_status_all_payload(
            supplier_manager=SupplierManager(),
            expire_func=expire,
            scanner_status_func=scanner_status,
            worker_busy_func=worker_busy,
            payload_builder=payload_builder,
        )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(
            calls,
            [
                ("expire",),
                ("order_by", ("name",)),
                ("scanner",),
                ("worker",),
                ("payload", suppliers, {"label": "ready"}, True),
            ],
        )

    def test_supplier_board_row_handles_supplier_without_email_route_without_database(
        self,
    ):
        from prices.services.supplier_board import build_supplier_board_row

        supplier = SimpleNamespace(
            id=12,
            name="No Mail Supplier",
            from_address_pattern="",
            expected_import_interval_hours=0,
            last_email_check_at=None,
            last_email_processed=0,
            last_email_matched=0,
        )

        row = build_supplier_board_row(
            supplier=supplier,
            successful_batch=None,
            latest_run=None,
        )

        self.assertFalse(row["has_email_route"])
        self.assertEqual(row["health_code"], "critical")
        self.assertEqual(row["last_import_relative"], "Never")
        self.assertIn("supplier=12", row["latest_log_url"])

    def test_business_health_warns_after_four_days_without_success(self):
        from prices.services.supplier_board import build_business_health_info

        supplier = SimpleNamespace(
            from_address_pattern="supplier@example.com",
            expected_import_interval_hours=24,
        )
        now = timezone.make_aware(datetime(2026, 4, 30, 12, 0, 0))
        last_import = {
            "source_code": "success",
            "datetime": now - timedelta(days=4),
            "sort_age_seconds": 4 * 24 * 60 * 60,
            "relative": "4d ago",
        }
        latest_check = {"code": "successful", "note": ""}

        with patch("prices.services.supplier_board.timezone.now", return_value=now):
            health = build_business_health_info(supplier, last_import, latest_check)

        self.assertEqual(health["code"], "warning")
        self.assertIn("4d since last successful import", health["note"])

    def test_last_import_info_formats_manual_upload(self):
        from prices.services.supplier_board import build_last_import_info

        now = timezone.make_aware(datetime(2026, 4, 30, 12, 0, 0))
        batch = SimpleNamespace(
            updated_at=now - timedelta(hours=1),
            created_at=now - timedelta(hours=2),
            received_at=now - timedelta(hours=3),
            mailbox_id=None,
        )

        with patch("prices.services.supplier_board.timezone.now", return_value=now):
            info = build_last_import_info(batch)

        self.assertEqual(info["relative"], "1h ago")
        self.assertEqual(info["source_code"], "manual")
        self.assertEqual(info["note"], "Manual upload / backfill")

    @patch("prices.services.supplier_board.models.SupplierFileMapping.objects")
    def test_active_price_mapping_collector_keeps_latest_per_supplier(
        self, mock_manager
    ):
        from prices.services.supplier_board import collect_active_price_mappings

        newer = SimpleNamespace(supplier_id=1, id=3)
        older = SimpleNamespace(supplier_id=1, id=2)
        other = SimpleNamespace(supplier_id=2, id=4)
        mock_manager.filter.return_value.select_related.return_value.order_by.return_value = [
            newer,
            older,
            other,
        ]

        mappings = collect_active_price_mappings()

        self.assertIs(mappings[1], newer)
        self.assertIs(mappings[2], other)

    def test_board_sort_key_prioritizes_oldest_then_severity_then_supplier_name(self):
        from prices.services.supplier_board import board_sort_key

        row = {
            "last_import_sort_age_seconds": 3600,
            "health_severity": 2,
            "supplier": SimpleNamespace(name="Zulu"),
        }

        self.assertEqual(board_sort_key(row), (-3600, 2, "zulu"))

    def test_running_email_run_status_uses_live_message_without_database(self):
        from prices.services.supplier_board import build_email_run_status

        run = SimpleNamespace(
            status=models.EmailImportStatus.RUNNING,
            total_messages=0,
            processed_messages=0,
            last_message="Importing Supplier: price_24_04.xlsx",
        )

        status = build_email_run_status(run)

        self.assertEqual(status["code"], "running")
        self.assertEqual(status["progress"], 8)
        self.assertIn("price_24_04.xlsx", status["note"])

    def test_finished_email_run_without_changes_is_neutral(self):
        from prices.services.supplier_board import build_email_run_status

        run = SimpleNamespace(
            status=models.EmailImportStatus.FINISHED,
            total_messages=0,
            processed_messages=0,
            last_message="No matching email",
            matched_files=0,
            processed_files=0,
            skipped_duplicates=0,
            errors=0,
        )

        status = build_email_run_status(run)

        self.assertEqual(status["code"], "no-change")
        self.assertEqual(status["class_name"], "is-neutral")

    def test_latest_check_prefers_newer_failed_diagnostic_over_older_run(self):
        from prices.services.supplier_board import build_latest_check_info

        now = timezone.make_aware(datetime(2026, 4, 30, 12, 0, 0))
        supplier = SimpleNamespace(
            from_address_pattern="",
            last_email_check_at=None,
            last_email_processed=0,
            last_email_matched=0,
        )
        run = SimpleNamespace(
            status=models.EmailImportStatus.FINISHED,
            finished_at=now - timedelta(hours=2),
            started_at=now - timedelta(hours=3),
            total_messages=1,
            processed_messages=1,
            last_message="Imported older file.",
            matched_files=1,
            processed_files=1,
            skipped_duplicates=0,
            errors=0,
        )
        diagnostic = SimpleNamespace(
            supplier=supplier,
            decision=models.AttachmentDecision.QUARANTINED,
            reason_code=models.AttachmentReason.MAPPING_MISSING,
            filename="broken.xlsx",
            message_date=None,
            created_at=now - timedelta(hours=1),
        )

        with patch("prices.services.supplier_board.timezone.now", return_value=now):
            info = build_latest_check_info(
                supplier,
                run,
                latest_diagnostic=diagnostic,
            )

        self.assertEqual(info["code"], "failed")
        self.assertEqual(info["class_name"], "is-warning")
        self.assertIn("broken.xlsx", info["note"])

    def test_latest_check_uses_supplier_fallback_when_no_run_or_diagnostic(self):
        from prices.services.supplier_board import build_latest_check_info

        now = timezone.make_aware(datetime(2026, 4, 30, 12, 0, 0))
        supplier = SimpleNamespace(
            from_address_pattern="",
            last_email_check_at=now - timedelta(minutes=30),
            last_email_processed=0,
            last_email_matched=0,
        )

        with patch("prices.services.supplier_board.timezone.now", return_value=now):
            info = build_latest_check_info(supplier, run=None)

        self.assertEqual(info["code"], "no-change")
        self.assertEqual(info["label"], "current")
        self.assertEqual(info["relative"], "30m ago")

    def test_clarify_latest_check_adds_last_success_context_for_failed_check(self):
        from prices.services.supplier_board import (
            clarify_latest_check_with_last_success,
        )

        latest_check = {"code": "failed", "note": "Parser failed"}
        last_import = {"source_code": "email", "relative": "2h ago"}

        clarified = clarify_latest_check_with_last_success(latest_check, last_import)

        self.assertEqual(clarified["note"], "Parser failed - last success 2h ago")


class ImportSchedulerServiceTests(SimpleTestCase):
    @patch("prices.services.import_scheduler.models.ImportSettings.get_solo")
    def test_cron_line_uses_configured_interval_without_database(self, mock_get_solo):
        from prices.services.import_scheduler import build_cron_line

        mock_get_solo.return_value = SimpleNamespace(interval_minutes=20)

        line = build_cron_line(Path("/opt/perfumex/run_import_emails.sh"))

        self.assertTrue(line.startswith("*/20 * * * * "))
        self.assertIn("/usr/bin/timeout 1800s", line)
        self.assertIn("PERFUMEX_IMPORT_CRON", line)

    def test_runner_script_does_not_require_var_log_venv_or_env_without_database(self):
        from prices.services.import_scheduler import render_runner_script

        script = render_runner_script()

        self.assertIn("perfumex_email_import.log", script)
        self.assertIn("if [ -f .env ]; then", script)
        self.assertNotIn("/var/log/perfumex_email_import.log", script)
        self.assertNotIn("source .venv/bin/activate", script)

    def test_install_import_scheduler_cron_replaces_existing_marker_line(self):
        from prices.services.import_scheduler import install_import_scheduler_cron

        writes = []
        script_path = Path("run_import_emails.sh")

        install_import_scheduler_cron(
            settings_obj=SimpleNamespace(interval_minutes=15),
            ensure_runner_script_func=lambda: script_path,
            read_crontab_lines_func=lambda: [
                "0 1 * * * echo keep",
                "* * * * old # PERFUMEX_IMPORT_CRON",
            ],
            write_crontab_lines_func=lambda lines: writes.append(lines),
            build_cron_line_func=lambda script_path, interval_minutes: (
                f"*/{interval_minutes} * * * * {script_path} # PERFUMEX_IMPORT_CRON"
            ),
        )

        self.assertEqual(
            writes,
            [
                [
                    "0 1 * * * echo keep",
                    f"*/15 * * * * {script_path} # PERFUMEX_IMPORT_CRON",
                ]
            ],
        )

    def test_remove_import_scheduler_cron_removes_marker_lines(self):
        from prices.services.import_scheduler import remove_import_scheduler_cron

        writes = []

        remove_import_scheduler_cron(
            read_crontab_lines_func=lambda: [
                "0 1 * * * echo keep",
                "* * * * old # PERFUMEX_IMPORT_CRON",
            ],
            write_crontab_lines_func=lambda lines: writes.append(lines),
        )

        self.assertEqual(writes, [["0 1 * * * echo keep"]])

    def test_run_import_scheduler_action_installs_scheduler(self):
        from prices.services.import_scheduler import run_import_scheduler_action

        calls = []

        result = run_import_scheduler_action(
            "install_cron",
            install_func=lambda: calls.append("install"),
        )

        self.assertEqual(calls, ["install"])
        self.assertEqual(result.message_level, "success")
        self.assertEqual(result.message, "Scheduler installed (cron + runner script).")
        self.assertTrue(result.handled)

    def test_run_import_scheduler_action_reports_install_failure(self):
        from prices.services.import_scheduler import run_import_scheduler_action

        def fail_install():
            raise RuntimeError("crontab unavailable")

        result = run_import_scheduler_action(
            "install_cron",
            install_func=fail_install,
        )

        self.assertEqual(result.message_level, "error")
        self.assertEqual(
            result.message,
            "Failed to install scheduler: crontab unavailable",
        )
        self.assertTrue(result.handled)

    def test_run_import_scheduler_action_removes_scheduler(self):
        from prices.services.import_scheduler import run_import_scheduler_action

        calls = []

        result = run_import_scheduler_action(
            "remove_cron",
            remove_func=lambda: calls.append("remove"),
        )

        self.assertEqual(calls, ["remove"])
        self.assertEqual(result.message_level, "success")
        self.assertEqual(result.message, "Scheduler cron entry removed.")
        self.assertTrue(result.handled)

    def test_run_import_scheduler_action_reports_remove_failure(self):
        from prices.services.import_scheduler import run_import_scheduler_action

        def fail_remove():
            raise RuntimeError("permission denied")

        result = run_import_scheduler_action(
            "remove_cron",
            remove_func=fail_remove,
        )

        self.assertEqual(result.message_level, "error")
        self.assertEqual(
            result.message, "Failed to remove scheduler: permission denied"
        )
        self.assertTrue(result.handled)

    def test_run_import_scheduler_action_ignores_unknown_action(self):
        from prices.services.import_scheduler import run_import_scheduler_action

        result = run_import_scheduler_action("run_now")

        self.assertFalse(result.handled)
        self.assertEqual(result.message_level, "")
        self.assertEqual(result.message, "")

    def test_run_import_settings_post_action_uses_scheduler_action_first(self):
        from prices.services.import_scheduler import (
            ImportSchedulerActionResult,
            run_import_settings_post_action,
        )

        calls = []

        result = run_import_settings_post_action(
            {"action": "install_cron"},
            scheduler_action_func=lambda action: calls.append(action)
            or ImportSchedulerActionResult("success", "installed"),
            manual_import_func=lambda: self.fail("manual import should not run"),
        )

        self.assertEqual(calls, ["install_cron"])
        self.assertEqual(result.message_level, "success")
        self.assertEqual(result.message, "installed")

    def test_run_import_settings_post_action_runs_manual_import(self):
        from prices.services.import_scheduler import (
            ImportSchedulerActionResult,
            run_import_settings_post_action,
        )

        result = run_import_settings_post_action(
            {"action": "run_now"},
            scheduler_action_func=lambda action: ImportSchedulerActionResult(
                "", "", handled=False
            ),
            manual_import_func=lambda: ImportSchedulerActionResult(
                "success", "manual started"
            ),
        )

        self.assertEqual(result.message_level, "success")
        self.assertEqual(result.message, "manual started")

    def test_run_import_settings_post_action_saves_valid_settings_form(self):
        from prices.services.import_scheduler import (
            ImportSchedulerActionResult,
            run_import_settings_post_action,
        )

        calls = []
        settings_obj = object()

        class FakeForm:
            def __init__(self, data, *, instance):
                calls.append(("init", data, instance))

            def is_valid(self):
                calls.append(("is_valid", None))
                return True

            def save(self):
                calls.append(("save", None))

        result = run_import_settings_post_action(
            {"action": "save"},
            settings_func=lambda: settings_obj,
            form_class=FakeForm,
            scheduler_action_func=lambda action: ImportSchedulerActionResult(
                "", "", handled=False
            ),
        )

        self.assertEqual(
            calls,
            [
                ("init", {"action": "save"}, settings_obj),
                ("is_valid", None),
                ("save", None),
            ],
        )
        self.assertEqual(result.message_level, "success")
        self.assertEqual(result.message, "Import settings updated.")

    def test_run_import_settings_post_action_reports_invalid_settings_form(self):
        from prices.services.import_scheduler import (
            ImportSchedulerActionResult,
            run_import_settings_post_action,
        )

        class FakeForm:
            def __init__(self, data, *, instance):
                pass

            def is_valid(self):
                return False

        result = run_import_settings_post_action(
            {"action": "save"},
            settings_func=object,
            form_class=FakeForm,
            scheduler_action_func=lambda action: ImportSchedulerActionResult(
                "", "", handled=False
            ),
        )

        self.assertEqual(result.message_level, "error")
        self.assertEqual(result.message, "Please fix the errors and try again.")

    def test_next_import_scheduler_run_at_adds_interval(self):
        from prices.services.import_scheduler import next_import_scheduler_run_at

        last_run_at = timezone.make_aware(datetime(2026, 4, 30, 12, 0, 0))
        settings_obj = SimpleNamespace(last_run_at=last_run_at, interval_minutes=25)

        self.assertEqual(
            next_import_scheduler_run_at(settings_obj),
            last_run_at + timedelta(minutes=25),
        )
        self.assertIsNone(
            next_import_scheduler_run_at(
                SimpleNamespace(last_run_at=None, interval_minutes=25)
            )
        )

    def test_build_import_settings_context_assembles_page_state(self):
        from prices.services.import_scheduler import build_import_settings_context

        form_calls = []
        settings_obj = SimpleNamespace(last_run_at="last-run", interval_minutes=15)

        class FakeImportSettingsForm:
            def __init__(self, *, instance):
                form_calls.append(instance)

        def read_crontab_lines_func():
            return ["cron"]

        context = build_import_settings_context(
            settings_obj=settings_obj,
            form_class=FakeImportSettingsForm,
            mailbox_options_func=lambda: ["mailbox"],
            supplier_options_func=lambda: ["supplier"],
            next_run_func=lambda value: ("next", value.interval_minutes),
            cron_status_func=lambda **kwargs: {"cron_kwargs": kwargs},
            read_crontab_lines_func=read_crontab_lines_func,
        )

        self.assertIsInstance(context["form"], FakeImportSettingsForm)
        self.assertEqual(form_calls, [settings_obj])
        self.assertIs(context["settings_obj"], settings_obj)
        self.assertEqual(context["mailboxes"], ["mailbox"])
        self.assertEqual(context["suppliers"], ["supplier"])
        self.assertEqual(context["next_run_at"], ("next", 15))
        self.assertIs(
            context["cron_status"]["cron_kwargs"]["read_crontab_lines_func"],
            read_crontab_lines_func,
        )
        self.assertEqual(context["import_section"], "settings")
        self.assertEqual(str(context["overview_url"]), "/admin/suppliers/overview/")
        self.assertEqual(
            str(context["detailed_logs_url"]),
            "/admin/suppliers/overview/detailed-logs/",
        )

    @patch("prices.services.import_scheduler.models.ImportSettings.get_solo")
    @patch("prices.services.import_scheduler.timezone.now")
    def test_cron_status_marks_late_scheduler_stale_without_database(
        self, mock_now, mock_get_solo
    ):
        from prices.services.import_scheduler import get_cron_status

        now = timezone.make_aware(datetime(2026, 4, 30, 12, 0, 0))
        mock_now.return_value = now
        mock_get_solo.return_value = SimpleNamespace(
            interval_minutes=20,
            last_run_at=now - timedelta(hours=1),
        )

        status = get_cron_status(
            read_crontab_lines_func=lambda: ["* * * * echo ok # PERFUMEX_IMPORT_CRON"]
        )

        self.assertTrue(status["stale"])
        self.assertGreaterEqual(status["late_by_minutes"], 30)


class AutoImportStatusServiceTests(SimpleTestCase):
    def test_parse_backlog_remaining_extracts_first_count(self):
        from prices.services.autoimport_status import parse_backlog_remaining

        self.assertEqual(
            parse_backlog_remaining("209 message(s) remain after this run."), 209
        )
        self.assertEqual(parse_backlog_remaining("No backlog"), 0)
        self.assertEqual(parse_backlog_remaining(""), 0)


class EmailImportRunServiceTests(SimpleTestCase):
    class FakeRunQuerySet:
        def __init__(self, calls, first_result=None):
            self.calls = calls
            self.first_result = first_result

        def select_related(self, *fields):
            self.calls.append(("select_related", fields))
            return self

        def filter(self, **kwargs):
            self.calls.append(("filter", kwargs))
            return self

        def order_by(self, *fields):
            self.calls.append(("order_by", fields))
            return self

        def first(self):
            self.calls.append(("first", None))
            return self.first_result

    def test_parse_import_date_range_accepts_optional_end_date(self):
        from prices.services.email_import_runs import parse_import_date_range

        result = parse_import_date_range(
            start_raw="2026-04-01",
            end_raw="2026-04-30",
            require_start=True,
        )

        self.assertTrue(result.is_valid)
        self.assertEqual(result.start_date.isoformat(), "2026-04-01")
        self.assertEqual(result.end_date.isoformat(), "2026-04-30")

    def test_parse_import_date_range_uses_custom_missing_start_message(self):
        from prices.services.email_import_runs import parse_import_date_range

        result = parse_import_date_range(
            start_raw="",
            end_raw="",
            require_start=True,
            missing_start_message="Start date is required for bulk backfill.",
        )

        self.assertFalse(result.is_valid)
        self.assertEqual(
            result.error_message, "Start date is required for bulk backfill."
        )

    def test_parse_import_date_range_reports_invalid_dates(self):
        from prices.services.email_import_runs import parse_import_date_range

        start_result = parse_import_date_range(
            start_raw="not-a-date",
            end_raw="",
        )
        end_result = parse_import_date_range(
            start_raw="2026-04-01",
            end_raw="not-a-date",
        )

        self.assertFalse(start_result.is_valid)
        self.assertEqual(start_result.error_message, "Start date is invalid.")
        self.assertFalse(end_result.is_valid)
        self.assertEqual(end_result.error_message, "End date is invalid.")

    def test_parse_import_date_range_optionally_validates_order(self):
        from prices.services.email_import_runs import parse_import_date_range

        result = parse_import_date_range(
            start_raw="2026-04-30",
            end_raw="2026-04-01",
            validate_order=True,
        )

        self.assertFalse(result.is_valid)
        self.assertEqual(
            result.error_message, "End date must be on or after start date."
        )

    def test_stuck_email_import_cutoff_uses_default_window(self):
        from prices.services.email_import_runs import stuck_email_import_cutoff

        now = timezone.make_aware(datetime(2026, 4, 30, 12, 0))

        self.assertEqual(
            stuck_email_import_cutoff(now=now),
            now - timezone.timedelta(minutes=30),
        )

    def test_stuck_email_import_runs_builds_ordered_running_queryset(self):
        from prices.services.email_import_runs import stuck_email_import_runs

        calls = []
        manager = self.FakeRunQuerySet(calls)
        cutoff = object()

        result = stuck_email_import_runs(cutoff=cutoff, run_manager=manager)

        self.assertIs(result.cutoff, cutoff)
        self.assertIs(result.runs, manager)
        self.assertEqual(
            calls,
            [
                ("select_related", ("supplier",)),
                (
                    "filter",
                    {
                        "status": models.EmailImportStatus.RUNNING,
                        "updated_at__lt": cutoff,
                    },
                ),
                ("order_by", ("updated_at", "started_at", "id")),
            ],
        )

    def test_build_stuck_email_import_runs_context_uses_standard_links(self):
        from prices.services.email_import_runs import (
            StuckEmailImportRuns,
            build_stuck_email_import_runs_context,
        )

        cutoff = object()
        runs = object()

        context = build_stuck_email_import_runs_context(
            stuck_runs_func=lambda: StuckEmailImportRuns(cutoff=cutoff, runs=runs)
        )

        self.assertIs(context["stuck_runs"], runs)
        self.assertIs(context["cutoff"], cutoff)
        self.assertEqual(context["import_section"], "stuck_runs")
        self.assertEqual(
            str(context["detailed_logs_url"]),
            "/admin/suppliers/overview/detailed-logs/",
        )
        self.assertEqual(str(context["overview_url"]), "/admin/suppliers/overview/")

    def test_latest_email_import_run_for_supplier_orders_newest_first(self):
        from prices.services.email_import_runs import (
            latest_email_import_run_for_supplier,
        )

        calls = []
        run = object()
        manager = self.FakeRunQuerySet(calls, first_result=run)

        result = latest_email_import_run_for_supplier(42, run_manager=manager)

        self.assertIs(result, run)
        self.assertEqual(
            calls,
            [
                ("filter", {"supplier_id": 42}),
                ("order_by", ("-started_at",)),
                ("first", None),
            ],
        )

    def test_build_email_import_status_payload_returns_idle_without_run(self):
        from prices.services.email_import_runs import build_email_import_status_payload

        self.assertEqual(build_email_import_status_payload(None), {"status": "idle"})

    def test_build_email_import_status_payload_includes_progress_and_log_tail(self):
        from prices.services.email_import_runs import build_email_import_status_payload

        run = SimpleNamespace(
            status=models.EmailImportStatus.RUNNING,
            total_messages=8,
            processed_messages=2,
            processed_files=3,
            errors=1,
            last_message="Importing price.xlsx",
            detailed_log=("prefix" + ("x" * 8100)),
        )

        payload = build_email_import_status_payload(run)

        self.assertEqual(payload["status"], models.EmailImportStatus.RUNNING)
        self.assertEqual(payload["progress"], 25)
        self.assertEqual(payload["processed_files"], 3)
        self.assertEqual(payload["errors"], 1)
        self.assertEqual(payload["last_message"], "Importing price.xlsx")
        self.assertEqual(len(payload["detailed_log"]), 8000)
        self.assertEqual(payload["detailed_log"], "x" * 8000)

    def test_build_email_import_status_payload_omits_progress_without_total(self):
        from prices.services.email_import_runs import build_email_import_status_payload

        run = SimpleNamespace(
            status=models.EmailImportStatus.RUNNING,
            total_messages=0,
            processed_messages=0,
            processed_files=0,
            errors=0,
            last_message="Starting",
            detailed_log="",
        )

        payload = build_email_import_status_payload(run)

        self.assertIsNone(payload["progress"])

    def test_supplier_email_import_status_payload_expires_before_lookup(self):
        from prices.services.email_import_runs import (
            supplier_email_import_status_payload,
        )

        calls = []
        run = SimpleNamespace(
            status=models.EmailImportStatus.FINISHED,
            total_messages=1,
            processed_messages=1,
            processed_files=1,
            errors=0,
            last_message="Done",
            detailed_log="Finished",
        )
        manager = self.FakeRunQuerySet(calls, first_result=run)

        def fake_expire():
            calls.append(("expire", None))

        payload = supplier_email_import_status_payload(
            7,
            run_manager=manager,
            expire_func=fake_expire,
        )

        self.assertEqual(payload["status"], models.EmailImportStatus.FINISHED)
        self.assertEqual(
            calls,
            [
                ("expire", None),
                ("filter", {"supplier_id": 7}),
                ("order_by", ("-started_at",)),
                ("first", None),
            ],
        )

    def test_build_process_email_runs_command_args_handles_single_run(self):
        from prices.services.email_import_runs import (
            build_process_email_runs_command_args,
        )

        self.assertEqual(
            build_process_email_runs_command_args([12]),
            ["process_email_runs", "--run-id", "12"],
        )

    def test_build_process_email_runs_command_args_handles_bulk_backfill(self):
        from prices.services.email_import_runs import (
            build_process_email_runs_command_args,
        )

        self.assertEqual(
            build_process_email_runs_command_args(
                [12, 34],
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 30),
            ),
            [
                "process_email_runs",
                "--run-id",
                "12",
                "--run-id",
                "34",
                "--start-date",
                "2026-04-01",
                "--end-date",
                "2026-04-30",
            ],
        )

    def test_enqueue_process_email_runs_delegates_to_queue_with_command_args(self):
        from prices.services.email_import_runs import enqueue_process_email_runs

        calls = []

        def fake_enqueue(command_name, *args, description):
            calls.append((command_name, args, description))
            return "queued"

        result = enqueue_process_email_runs(
            [12, 34],
            description="Bulk supplier email backfill",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 30),
            enqueue_func=fake_enqueue,
        )

        self.assertEqual(result, "queued")
        self.assertEqual(
            calls,
            [
                (
                    "process_email_runs",
                    (
                        "--run-id",
                        "12",
                        "--run-id",
                        "34",
                        "--start-date",
                        "2026-04-01",
                        "--end-date",
                        "2026-04-30",
                    ),
                    "Bulk supplier email backfill",
                )
            ],
        )

    def test_enqueue_forced_email_import_scan_delegates_to_queue(self):
        from prices.services.email_import_runs import enqueue_forced_email_import_scan

        calls = []

        def fake_enqueue(command_name, *args, description):
            calls.append((command_name, args, description))
            return "queued"

        result = enqueue_forced_email_import_scan(
            description="Supplier board mailbox scan",
            enqueue_func=fake_enqueue,
        )

        self.assertEqual(result, "queued")
        self.assertEqual(
            calls,
            [("import_emails", ("--force",), "Supplier board mailbox scan")],
        )

    def test_run_manual_email_import_action_reports_busy_without_enqueue(self):
        from prices.services.email_import_runs import (
            EMAIL_IMPORT_BUSY_MESSAGE,
            run_manual_email_import_action,
        )

        calls = []

        result = run_manual_email_import_action(
            has_running_func=lambda: True,
            enqueue_func=lambda **kwargs: calls.append(kwargs),
        )

        self.assertEqual(calls, [])
        self.assertEqual(result.message_level, "info")
        self.assertEqual(result.message, EMAIL_IMPORT_BUSY_MESSAGE)

    def test_run_manual_email_import_action_enqueues_manual_import(self):
        from prices.services.email_import_runs import run_manual_email_import_action

        calls = []

        result = run_manual_email_import_action(
            has_running_func=lambda: False,
            enqueue_func=lambda **kwargs: calls.append(kwargs),
        )

        self.assertEqual(calls, [{"description": "Manual email import"}])
        self.assertEqual(result.message_level, "success")
        self.assertEqual(result.message, "Email import started.")

    def test_run_manual_email_import_action_reports_enqueue_failure(self):
        from prices.services.email_import_runs import run_manual_email_import_action

        def fail_enqueue(**kwargs):
            raise RuntimeError("queue down")

        result = run_manual_email_import_action(
            has_running_func=lambda: False,
            enqueue_func=fail_enqueue,
            sync_import_func=lambda: (_ for _ in ()).throw(RuntimeError("sync down")),
        )

        self.assertEqual(result.message_level, "error")
        self.assertEqual(
            result.message,
            "Failed to start email import: queue down; synchronous fallback also failed: sync down",
        )

    def test_run_manual_email_import_action_runs_sync_when_queue_unavailable(self):
        from prices.services.email_import_runs import run_manual_email_import_action

        calls = []

        def fail_enqueue(**kwargs):
            raise RuntimeError("queue down")

        result = run_manual_email_import_action(
            has_running_func=lambda: False,
            enqueue_func=fail_enqueue,
            sync_import_func=lambda: calls.append("sync"),
        )

        self.assertEqual(calls, ["sync"])
        self.assertEqual(result.message_level, "success")
        self.assertEqual(
            result.message,
            "Email import ran now. Background queue was unavailable, so the scan ran synchronously.",
        )

    def test_run_supplier_board_mailbox_scan_action_reports_busy_without_enqueue(self):
        from prices.services.email_import_runs import (
            EMAIL_IMPORT_BUSY_MESSAGE,
            run_supplier_board_mailbox_scan_action,
        )

        calls = []

        result = run_supplier_board_mailbox_scan_action(
            has_running_func=lambda: True,
            enqueue_func=lambda **kwargs: calls.append(kwargs),
        )

        self.assertEqual(calls, [])
        self.assertEqual(result.message_level, "info")
        self.assertEqual(result.message, EMAIL_IMPORT_BUSY_MESSAGE)

    def test_run_supplier_board_mailbox_scan_action_enqueues_scan(self):
        from prices.services.email_import_runs import (
            run_supplier_board_mailbox_scan_action,
        )

        calls = []

        result = run_supplier_board_mailbox_scan_action(
            has_running_func=lambda: False,
            enqueue_func=lambda **kwargs: calls.append(kwargs),
        )

        self.assertEqual(calls, [{"description": "Supplier board mailbox scan"}])
        self.assertEqual(result.message_level, "info")
        self.assertEqual(result.message, "Mailbox scan started.")

    def test_run_supplier_board_mailbox_scan_action_reports_enqueue_failure(self):
        from prices.services.email_import_runs import (
            run_supplier_board_mailbox_scan_action,
        )

        def fail_enqueue(**kwargs):
            raise RuntimeError("queue down")

        result = run_supplier_board_mailbox_scan_action(
            has_running_func=lambda: False,
            enqueue_func=fail_enqueue,
            sync_import_func=lambda: (_ for _ in ()).throw(RuntimeError("sync down")),
        )

        self.assertEqual(result.message_level, "error")
        self.assertEqual(
            result.message,
            "Failed to start mailbox scan: queue down; synchronous fallback also failed: sync down",
        )

    def test_run_supplier_board_mailbox_scan_action_runs_sync_when_queue_unavailable(
        self,
    ):
        from prices.services.email_import_runs import (
            run_supplier_board_mailbox_scan_action,
        )

        calls = []

        def fail_enqueue(**kwargs):
            raise RuntimeError("queue down")

        result = run_supplier_board_mailbox_scan_action(
            has_running_func=lambda: False,
            enqueue_func=fail_enqueue,
            sync_import_func=lambda: calls.append("sync"),
        )

        self.assertEqual(calls, ["sync"])
        self.assertEqual(result.message_level, "info")
        self.assertEqual(
            result.message,
            "Mailbox scan ran now. Background queue was unavailable, so the scan ran synchronously.",
        )

    def test_active_email_backfill_suppliers_filters_selected_active_suppliers(self):
        from prices.services.email_import_runs import active_email_backfill_suppliers

        supplier_rows = [object(), object()]
        manager = SimpleNamespace()
        manager.filter = lambda **kwargs: supplier_rows

        result = active_email_backfill_suppliers(
            ["12", "34"],
            supplier_manager=manager,
        )

        self.assertEqual(result, supplier_rows)

    def test_active_email_backfill_suppliers_uses_expected_filter(self):
        from prices.services.email_import_runs import active_email_backfill_suppliers

        calls = []

        class SupplierManager:
            def filter(self, **kwargs):
                calls.append(kwargs)
                return []

        active_email_backfill_suppliers(
            ["12", "34"],
            supplier_manager=SupplierManager(),
        )

        self.assertEqual(calls, [{"id__in": ["12", "34"], "is_active": True}])

    def test_build_email_backfill_run_message_uses_today_for_open_end(self):
        from prices.services.email_import_runs import build_email_backfill_run_message

        self.assertEqual(
            build_email_backfill_run_message(
                "Backfill",
                start_date=date(2026, 4, 1),
            ),
            "Backfill 2026-04-01 to today",
        )

    def test_build_email_backfill_run_message_uses_end_date_when_present(self):
        from prices.services.email_import_runs import build_email_backfill_run_message

        self.assertEqual(
            build_email_backfill_run_message(
                "Bulk backfill",
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 30),
            ),
            "Bulk backfill 2026-04-01 to 2026-04-30",
        )

    @patch("prices.services.email_import_runs.models.EmailImportRun.objects")
    def test_create_email_import_run_sets_running_status(self, mock_manager):
        from prices.services.email_import_runs import create_email_import_run

        supplier = object()
        expected_run = object()
        mock_manager.create.return_value = expected_run

        result = create_email_import_run(supplier)

        self.assertIs(result, expected_run)
        mock_manager.create.assert_called_once_with(
            supplier=supplier,
            status=models.EmailImportStatus.RUNNING,
        )

    @patch("prices.services.email_import_runs.models.EmailImportRun.objects")
    def test_create_email_import_run_keeps_optional_last_message(self, mock_manager):
        from prices.services.email_import_runs import create_email_import_run

        supplier = object()
        expected_run = object()
        mock_manager.create.return_value = expected_run

        result = create_email_import_run(supplier, last_message="Backfill 2026-04-01")

        self.assertIs(result, expected_run)
        mock_manager.create.assert_called_once_with(
            supplier=supplier,
            status=models.EmailImportStatus.RUNNING,
            last_message="Backfill 2026-04-01",
        )

    def test_run_supplier_email_import_action_requires_sender(self):
        from prices.services.email_import_runs import run_supplier_email_import_action

        supplier = SimpleNamespace(from_address_pattern="")
        calls = []

        result = run_supplier_email_import_action(
            supplier,
            has_running_func=lambda: calls.append("busy"),
        )

        self.assertEqual(calls, [])
        self.assertEqual(result.message_level, "info")
        self.assertEqual(
            result.message,
            "Supplier has no sender email configured. Set From address pattern first.",
        )

    def test_run_supplier_email_import_action_reports_busy_without_creating_run(self):
        from prices.services.email_import_runs import (
            EMAIL_IMPORT_BUSY_MESSAGE,
            run_supplier_email_import_action,
        )

        supplier = SimpleNamespace(from_address_pattern="supplier@example.com")
        calls = []

        result = run_supplier_email_import_action(
            supplier,
            has_running_func=lambda: True,
            create_run_func=lambda supplier: calls.append(supplier),
        )

        self.assertEqual(calls, [])
        self.assertEqual(result.message_level, "info")
        self.assertEqual(result.message, EMAIL_IMPORT_BUSY_MESSAGE)

    def test_run_supplier_email_import_action_creates_and_enqueues_run(self):
        from prices.services.email_import_runs import run_supplier_email_import_action

        supplier = SimpleNamespace(
            name="Supplier A",
            from_address_pattern="supplier@example.com",
        )
        calls = []
        run = SimpleNamespace(id=42)

        def fake_create(create_supplier):
            calls.append(("create", create_supplier))
            return run

        def fake_enqueue(run_ids, *, description):
            calls.append(("enqueue", run_ids, description))

        result = run_supplier_email_import_action(
            supplier,
            has_running_func=lambda: False,
            create_run_func=fake_create,
            enqueue_func=fake_enqueue,
        )

        self.assertEqual(
            calls,
            [
                ("create", supplier),
                ("enqueue", [42], "Email import for Supplier A"),
            ],
        )
        self.assertEqual(result.message_level, "")
        self.assertEqual(result.message, "")
        self.assertEqual(result.updated, 1)

    def test_run_supplier_email_import_action_marks_run_failed_on_enqueue_error(self):
        from prices.services.email_import_runs import run_supplier_email_import_action

        supplier = SimpleNamespace(
            name="Supplier A",
            from_address_pattern="supplier@example.com",
        )
        run = SimpleNamespace(id=42)
        calls = []

        def fail_enqueue(run_ids, *, description):
            calls.append(("enqueue", run_ids, description))
            raise RuntimeError("queue down")

        def fake_mark_failed(run_ids, *, last_message):
            calls.append(("mark_failed", run_ids, last_message))

        result = run_supplier_email_import_action(
            supplier,
            has_running_func=lambda: False,
            create_run_func=lambda supplier: run,
            enqueue_func=fail_enqueue,
            mark_failed_func=fake_mark_failed,
        )

        self.assertEqual(
            calls,
            [
                ("enqueue", [42], "Email import for Supplier A"),
                (
                    "mark_failed",
                    [42],
                    "Failed to start background import: queue down",
                ),
            ],
        )
        self.assertEqual(result.message_level, "error")
        self.assertEqual(result.message, "Failed to start email import: queue down")

    def test_run_supplier_email_backfill_action_requires_sender(self):
        from prices.services.email_import_runs import run_supplier_email_backfill_action

        supplier = SimpleNamespace(from_address_pattern="")

        result = run_supplier_email_backfill_action(
            supplier,
            start_raw="2026-04-01",
            has_running_func=lambda: True,
        )

        self.assertEqual(result.message_level, "info")
        self.assertEqual(
            result.message,
            "Supplier has no sender email configured. Set From address pattern first.",
        )

    def test_run_supplier_email_backfill_action_reports_busy_before_date_parse(self):
        from prices.services.email_import_runs import (
            EMAIL_IMPORT_BUSY_MESSAGE,
            run_supplier_email_backfill_action,
        )

        supplier = SimpleNamespace(from_address_pattern="supplier@example.com")

        result = run_supplier_email_backfill_action(
            supplier,
            start_raw="",
            has_running_func=lambda: True,
        )

        self.assertEqual(result.message_level, "info")
        self.assertEqual(result.message, EMAIL_IMPORT_BUSY_MESSAGE)

    def test_run_supplier_email_backfill_action_requires_start_date(self):
        from prices.services.email_import_runs import run_supplier_email_backfill_action

        supplier = SimpleNamespace(from_address_pattern="supplier@example.com")

        result = run_supplier_email_backfill_action(
            supplier,
            start_raw="",
            has_running_func=lambda: False,
        )

        self.assertEqual(result.message_level, "info")
        self.assertEqual(result.message, "Start date is required for backfill.")

    def test_run_supplier_email_backfill_action_creates_run_with_backfill_message(self):
        from prices.services.email_import_runs import run_supplier_email_backfill_action

        supplier = SimpleNamespace(
            name="Supplier A",
            from_address_pattern="supplier@example.com",
        )
        calls = []
        run = SimpleNamespace(id=42)

        def fake_create_run(*, supplier, last_message):
            calls.append(("create", supplier, last_message))
            return run

        def fake_enqueue(run_ids, *, description, start_date, end_date):
            calls.append(("enqueue", run_ids, description, start_date, end_date))

        result = run_supplier_email_backfill_action(
            supplier,
            start_raw="2026-04-01",
            end_raw="2026-04-30",
            has_running_func=lambda: False,
            create_run_func=fake_create_run,
            enqueue_func=fake_enqueue,
        )

        self.assertEqual(
            calls,
            [
                ("create", supplier, "Backfill 2026-04-01 to 2026-04-30"),
                (
                    "enqueue",
                    [42],
                    "Email backfill for Supplier A",
                    date(2026, 4, 1),
                    date(2026, 4, 30),
                ),
            ],
        )
        self.assertEqual(result.message_level, "")
        self.assertEqual(result.message, "")
        self.assertEqual(result.updated, 1)

    def test_run_supplier_email_backfill_action_marks_run_failed_on_enqueue_error(self):
        from prices.services.email_import_runs import run_supplier_email_backfill_action

        supplier = SimpleNamespace(
            name="Supplier A",
            from_address_pattern="supplier@example.com",
        )
        run = SimpleNamespace(id=42)
        calls = []

        def fail_enqueue(run_ids, *, description, start_date, end_date):
            calls.append(("enqueue", run_ids, description, start_date, end_date))
            raise RuntimeError("queue down")

        def fake_mark_failed(run_ids, *, last_message):
            calls.append(("mark_failed", run_ids, last_message))

        result = run_supplier_email_backfill_action(
            supplier,
            start_raw="2026-04-01",
            has_running_func=lambda: False,
            create_run_func=lambda **kwargs: run,
            enqueue_func=fail_enqueue,
            mark_failed_func=fake_mark_failed,
        )

        self.assertEqual(
            calls,
            [
                (
                    "enqueue",
                    [42],
                    "Email backfill for Supplier A",
                    date(2026, 4, 1),
                    None,
                ),
                ("mark_failed", [42], "Failed to start backfill: queue down"),
            ],
        )
        self.assertEqual(result.message_level, "error")
        self.assertEqual(result.message, "Failed to start backfill: queue down")

    def test_run_bulk_email_backfill_action_reports_busy_before_selection(self):
        from prices.services.email_import_runs import (
            EMAIL_IMPORT_BUSY_MESSAGE,
            run_bulk_email_backfill_action,
        )

        result = run_bulk_email_backfill_action(
            [],
            start_raw="",
            has_running_func=lambda: True,
        )

        self.assertEqual(result.message_level, "info")
        self.assertEqual(result.message, EMAIL_IMPORT_BUSY_MESSAGE)

    def test_run_bulk_email_backfill_action_requires_supplier_selection(self):
        from prices.services.email_import_runs import run_bulk_email_backfill_action

        result = run_bulk_email_backfill_action(
            [],
            start_raw="2026-04-01",
            has_running_func=lambda: False,
        )

        self.assertEqual(result.message_level, "info")
        self.assertEqual(result.message, "Select at least one supplier for backfill.")

    def test_run_bulk_email_backfill_action_requires_start_date(self):
        from prices.services.email_import_runs import run_bulk_email_backfill_action

        result = run_bulk_email_backfill_action(
            ["1"],
            start_raw="",
            has_running_func=lambda: False,
        )

        self.assertEqual(result.message_level, "info")
        self.assertEqual(result.message, "Start date is required for bulk backfill.")

    def test_run_bulk_email_backfill_action_reports_invalid_suppliers(self):
        from prices.services.email_import_runs import run_bulk_email_backfill_action

        result = run_bulk_email_backfill_action(
            ["1"],
            start_raw="2026-04-01",
            has_running_func=lambda: False,
            active_suppliers_func=lambda supplier_ids: [],
        )

        self.assertEqual(result.message_level, "info")
        self.assertEqual(result.message, "No valid suppliers selected.")

    def test_run_bulk_email_backfill_action_reports_no_email_routes(self):
        from prices.services.email_import_runs import run_bulk_email_backfill_action

        supplier = SimpleNamespace(id=1)

        result = run_bulk_email_backfill_action(
            ["1"],
            start_raw="2026-04-01",
            has_running_func=lambda: False,
            active_suppliers_func=lambda supplier_ids: [supplier],
            create_runs_func=lambda suppliers, **kwargs: [],
        )

        self.assertEqual(result.message_level, "info")
        self.assertEqual(
            result.message,
            "No selected suppliers have sender email configured.",
        )

    def test_run_bulk_email_backfill_action_creates_runs_and_enqueues(self):
        from prices.services.email_import_runs import run_bulk_email_backfill_action

        suppliers = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        calls = []

        def fake_active_suppliers(supplier_ids):
            calls.append(("active_suppliers", supplier_ids))
            return suppliers

        def fake_create_runs(supplier_list, *, start_date, end_date):
            calls.append(("create_runs", supplier_list, start_date, end_date))
            return [101, 102]

        def fake_enqueue(run_ids, *, description, start_date, end_date):
            calls.append(("enqueue", run_ids, description, start_date, end_date))

        result = run_bulk_email_backfill_action(
            ["1", "2"],
            start_raw="2026-04-01",
            end_raw="2026-04-30",
            has_running_func=lambda: False,
            active_suppliers_func=fake_active_suppliers,
            create_runs_func=fake_create_runs,
            enqueue_func=fake_enqueue,
        )

        self.assertEqual(
            calls,
            [
                ("active_suppliers", ["1", "2"]),
                ("create_runs", suppliers, date(2026, 4, 1), date(2026, 4, 30)),
                (
                    "enqueue",
                    [101, 102],
                    "Bulk supplier email backfill",
                    date(2026, 4, 1),
                    date(2026, 4, 30),
                ),
            ],
        )
        self.assertEqual(result.message_level, "success")
        self.assertEqual(result.message, "Backfill queued for 2 supplier(s).")
        self.assertEqual(result.updated, 2)

    def test_run_bulk_email_backfill_action_marks_runs_failed_on_enqueue_error(self):
        from prices.services.email_import_runs import run_bulk_email_backfill_action

        supplier = SimpleNamespace(id=1)
        calls = []

        def fail_enqueue(run_ids, *, description, start_date, end_date):
            calls.append(("enqueue", run_ids, description, start_date, end_date))
            raise RuntimeError("queue down")

        def fake_mark_failed(run_ids, *, last_message):
            calls.append(("mark_failed", run_ids, last_message))

        result = run_bulk_email_backfill_action(
            ["1"],
            start_raw="2026-04-01",
            has_running_func=lambda: False,
            active_suppliers_func=lambda supplier_ids: [supplier],
            create_runs_func=lambda suppliers, **kwargs: [101],
            enqueue_func=fail_enqueue,
            mark_failed_func=fake_mark_failed,
        )

        self.assertEqual(
            calls,
            [
                (
                    "enqueue",
                    [101],
                    "Bulk supplier email backfill",
                    date(2026, 4, 1),
                    None,
                ),
                ("mark_failed", [101], "Failed to start bulk backfill: queue down"),
            ],
        )
        self.assertEqual(result.message_level, "error")
        self.assertEqual(
            result.message,
            "Failed to start bulk backfill: queue down",
        )

    def test_create_email_backfill_runs_for_suppliers_skips_missing_sender(self):
        from prices.services.email_import_runs import (
            create_email_backfill_runs_for_suppliers,
        )

        created = []
        suppliers = [
            SimpleNamespace(id=1, from_address_pattern="supplier@example.com"),
            SimpleNamespace(id=2, from_address_pattern=""),
            SimpleNamespace(id=3, from_address_pattern="other@example.com"),
        ]

        def fake_create_run(*, supplier, last_message):
            created.append((supplier.id, last_message))
            return SimpleNamespace(id=supplier.id + 100)

        run_ids = create_email_backfill_runs_for_suppliers(
            suppliers,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 30),
            create_run_func=fake_create_run,
        )

        self.assertEqual(run_ids, [101, 103])
        self.assertEqual(
            created,
            [
                (1, "Bulk backfill 2026-04-01 to 2026-04-30"),
                (3, "Bulk backfill 2026-04-01 to 2026-04-30"),
            ],
        )

    def test_create_email_backfill_runs_for_suppliers_supports_custom_label(self):
        from prices.services.email_import_runs import (
            create_email_backfill_runs_for_suppliers,
        )

        created = []
        suppliers = [SimpleNamespace(id=7, from_address_pattern="a@example.com")]

        def fake_create_run(*, supplier, last_message):
            created.append(last_message)
            return SimpleNamespace(id=supplier.id)

        run_ids = create_email_backfill_runs_for_suppliers(
            suppliers,
            label="Backfill",
            start_date=date(2026, 4, 1),
            create_run_func=fake_create_run,
        )

        self.assertEqual(run_ids, [7])
        self.assertEqual(created, ["Backfill 2026-04-01 to today"])

    @patch("prices.services.email_import_runs.timezone.now")
    @patch("prices.services.email_import_runs.models.EmailImportRun.objects")
    def test_mark_email_import_runs_failed_updates_run_ids(
        self, mock_manager, mock_now
    ):
        from prices.services.email_import_runs import mark_email_import_runs_failed

        finished_at = object()
        mock_now.return_value = finished_at
        mock_manager.filter.return_value.update.return_value = 2

        result = mark_email_import_runs_failed(
            [12, 34],
            last_message="Failed to start bulk backfill: boom",
        )

        self.assertEqual(result, 2)
        mock_manager.filter.assert_called_once_with(id__in=[12, 34])
        mock_manager.filter.return_value.update.assert_called_once_with(
            status=models.EmailImportStatus.FAILED,
            finished_at=finished_at,
            errors=1,
            last_message="Failed to start bulk backfill: boom",
        )

    @patch("prices.services.email_import_runs.models.EmailImportRun.objects")
    def test_mark_email_import_runs_failed_ignores_empty_run_list(self, mock_manager):
        from prices.services.email_import_runs import mark_email_import_runs_failed

        result = mark_email_import_runs_failed([], last_message="No runs")

        self.assertEqual(result, 0)
        mock_manager.filter.assert_not_called()

    @patch("prices.services.email_import_runs.timezone.now")
    @patch("prices.services.email_import_runs.models.EmailImportRun.objects")
    def test_mark_email_import_run_failed_from_recovery_updates_running_run(
        self, mock_manager, mock_now
    ):
        from prices.services.email_import_runs import (
            mark_email_import_run_failed_from_recovery,
        )

        finished_at = object()
        mock_now.return_value = finished_at
        mock_manager.filter.return_value.update.return_value = 1

        result = mark_email_import_run_failed_from_recovery(12)

        self.assertEqual(result, 1)
        mock_manager.filter.assert_called_once_with(
            id=12,
            status=models.EmailImportStatus.RUNNING,
        )
        mock_manager.filter.return_value.update.assert_called_once_with(
            status=models.EmailImportStatus.FAILED,
            finished_at=finished_at,
            errors=ANY,
            last_message="Marked failed from stuck-run recovery.",
        )

    def test_recover_stuck_email_import_run_rejects_invalid_id(self):
        from prices.services.email_import_runs import recover_stuck_email_import_run

        calls = []

        result = recover_stuck_email_import_run(
            "not-a-run",
            mark_func=lambda run_id: calls.append(run_id),
        )

        self.assertEqual(result.message_level, "error")
        self.assertEqual(result.message, "Select a valid import run.")
        self.assertEqual(result.updated, 0)
        self.assertEqual(calls, [])

    def test_recover_stuck_email_import_run_reports_marked_failed(self):
        from prices.services.email_import_runs import recover_stuck_email_import_run

        calls = []

        def fake_mark(run_id):
            calls.append(run_id)
            return 1

        result = recover_stuck_email_import_run(" 12 ", mark_func=fake_mark)

        self.assertEqual(calls, [12])
        self.assertEqual(result.message_level, "success")
        self.assertEqual(result.message, "Import run marked as failed.")
        self.assertEqual(result.updated, 1)

    def test_recover_stuck_email_import_run_reports_already_finished(self):
        from prices.services.email_import_runs import recover_stuck_email_import_run

        result = recover_stuck_email_import_run("12", mark_func=lambda run_id: 0)

        self.assertEqual(result.message_level, "info")
        self.assertEqual(result.message, "Import run is no longer running.")
        self.assertEqual(result.updated, 0)

    @patch("prices.services.email_import_runs.timezone.now")
    @patch("prices.services.email_import_runs.models.EmailImportRun.objects")
    def test_cancel_running_email_imports_for_supplier_updates_running_runs(
        self, mock_manager, mock_now
    ):
        from prices.services.email_import_runs import (
            cancel_running_email_imports_for_supplier,
        )

        supplier = object()
        finished_at = object()
        mock_now.return_value = finished_at
        mock_manager.filter.return_value.update.return_value = 1

        result = cancel_running_email_imports_for_supplier(supplier)

        self.assertEqual(result, 1)
        mock_manager.filter.assert_called_once_with(
            supplier=supplier,
            status=models.EmailImportStatus.RUNNING,
        )
        mock_manager.filter.return_value.update.assert_called_once_with(
            status=models.EmailImportStatus.CANCELED,
            finished_at=finished_at,
            last_message="Canceled by user.",
        )

    def test_cancel_supplier_email_import_expires_before_cancel(self):
        from prices.services.email_import_runs import cancel_supplier_email_import

        supplier = object()
        calls = []

        def fake_expire():
            calls.append(("expire", None))

        def fake_cancel(cancel_supplier):
            calls.append(("cancel", cancel_supplier))
            return 1

        result = cancel_supplier_email_import(
            supplier,
            expire_func=fake_expire,
            cancel_func=fake_cancel,
        )

        self.assertEqual(calls, [("expire", None), ("cancel", supplier)])
        self.assertEqual(result.message_level, "info")
        self.assertEqual(result.message, "Email import marked as canceled.")
        self.assertEqual(result.updated, 1)

    def test_cancel_supplier_email_import_reports_no_running_import(self):
        from prices.services.email_import_runs import cancel_supplier_email_import

        result = cancel_supplier_email_import(
            object(),
            expire_func=lambda: None,
            cancel_func=lambda supplier: 0,
        )

        self.assertEqual(result.message_level, "info")
        self.assertEqual(result.message, "No running import to cancel.")
        self.assertEqual(result.updated, 0)

    @patch("prices.services.email_import_runs.models.ImportSettings.get_solo")
    def test_email_import_timeout_seconds_converts_minutes(self, mock_get_solo):
        from prices.services.email_import_runs import email_import_timeout_seconds

        mock_get_solo.return_value = SimpleNamespace(supplier_timeout_minutes=15)

        self.assertEqual(email_import_timeout_seconds(), 900)

        mock_get_solo.return_value = SimpleNamespace(supplier_timeout_minutes=0)
        self.assertIsNone(email_import_timeout_seconds())

    @patch(
        "prices.services.email_import_runs.email_import_timeout_seconds",
        return_value=None,
    )
    def test_expire_stale_email_import_runs_noops_without_timeout(self, _mock_timeout):
        from prices.services.email_import_runs import expire_stale_email_import_runs

        self.assertEqual(expire_stale_email_import_runs(), 0)

    @patch("prices.services.email_import_runs.expire_stale_email_import_runs")
    def test_has_running_email_imports_short_circuits_on_worker_lock(self, mock_expire):
        from prices.services.email_import_runs import has_running_email_imports

        self.assertTrue(has_running_email_imports(worker_busy_func=lambda: True))
        mock_expire.assert_called_once()


class ImportHistoryServiceTests(SimpleTestCase):
    class FakeImportFiles:
        def __init__(self, updated_at):
            self.updated_at = updated_at
            self.calls = []

        def all(self):
            self.calls.append(("all", None))
            return self

        def order_by(self, *fields):
            self.calls.append(("order_by", fields))
            return self

        def aggregate(self, **kwargs):
            self.calls.append(("aggregate", tuple(kwargs)))
            return {"updated_at": self.updated_at}

    class FakeBatchQuerySet:
        def __init__(self, calls):
            self.calls = calls

        def distinct(self):
            self.calls.append(("distinct", None))
            return self

        def filter(self, **kwargs):
            self.calls.append(("queryset_filter", kwargs))
            return self

    class FakeBatchManager:
        def __init__(self):
            self.calls = []

        def filter(self, **kwargs):
            self.calls.append(("manager_filter", kwargs))
            return ImportHistoryServiceTests.FakeBatchQuerySet(self.calls)

    def test_latest_timestamp_from_rows_uses_first_available_value_per_row(self):
        from prices.services.import_history import latest_timestamp_from_rows

        older = timezone.make_aware(datetime(2026, 4, 29, 8, 0))
        newer = timezone.make_aware(datetime(2026, 4, 30, 8, 0))
        ignored_fallback = timezone.make_aware(datetime(2026, 5, 1, 8, 0))

        self.assertEqual(
            latest_timestamp_from_rows(
                [
                    (None, older),
                    (newer, ignored_fallback),
                ]
            ),
            newer,
        )

    def test_latest_timestamp_from_rows_ignores_empty_rows(self):
        from prices.services.import_history import latest_timestamp_from_rows

        self.assertIsNone(latest_timestamp_from_rows([(None, None), ()]))

    def test_collect_import_dates_from_batches_filters_local_dates(self):
        from prices.services.import_history import collect_import_dates_from_batches

        before = timezone.make_aware(datetime(2026, 4, 29, 21, 0))
        in_range = timezone.make_aware(datetime(2026, 4, 30, 8, 0))
        fallback_created = datetime(2026, 5, 1, 9, 0)
        batches = [
            SimpleNamespace(received_at=before, created_at=None),
            SimpleNamespace(received_at=in_range, created_at=None),
            SimpleNamespace(received_at=None, created_at=fallback_created),
            SimpleNamespace(received_at=None, created_at=None),
        ]

        import_dates = collect_import_dates_from_batches(
            batches,
            start_date=date(2026, 4, 30),
            end_date=date(2026, 5, 1),
        )

        self.assertEqual(import_dates, {date(2026, 4, 30), date(2026, 5, 1)})

    def test_processed_price_import_batches_filters_processed_price_files(self):
        from prices.services.import_history import processed_price_import_batches

        manager = self.FakeBatchManager()

        processed_price_import_batches(batch_manager=manager)

        self.assertEqual(
            manager.calls,
            [
                (
                    "manager_filter",
                    {
                        "importfile__file_kind": models.FileKind.PRICE,
                        "importfile__status": models.ImportStatus.PROCESSED,
                    },
                ),
                ("distinct", None),
            ],
        )

    def test_processed_price_import_batches_optionally_filters_suppliers(self):
        from prices.services.import_history import processed_price_import_batches

        manager = self.FakeBatchManager()

        processed_price_import_batches(
            supplier_ids=["12", "34"],
            batch_manager=manager,
        )

        self.assertEqual(
            manager.calls,
            [
                (
                    "manager_filter",
                    {
                        "importfile__file_kind": models.FileKind.PRICE,
                        "importfile__status": models.ImportStatus.PROCESSED,
                    },
                ),
                ("distinct", None),
                ("queryset_filter", {"supplier_id__in": ["12", "34"]}),
            ],
        )

    def test_build_import_detail_context_uses_safe_back_url_and_file_timestamps(self):
        from prices.services.import_history import build_import_detail_context

        created_at = timezone.make_aware(datetime(2026, 4, 29, 8, 0))
        received_at = timezone.make_aware(datetime(2026, 4, 30, 8, 0))
        updated_at = timezone.make_aware(datetime(2026, 5, 1, 8, 0))
        import_files = self.FakeImportFiles(updated_at)
        import_batch = SimpleNamespace(
            importfile_set=import_files,
            received_at=received_at,
            created_at=created_at,
        )
        request = RequestFactory().get(
            "/admin/imports/7/",
            {"next": "/admin/suppliers/overview/?supplier=2"},
        )

        context = build_import_detail_context(import_batch, request)

        self.assertIs(context["import_files"], import_files)
        self.assertEqual(context["received_at_display"], received_at)
        self.assertEqual(context["updated_at_display"], updated_at)
        self.assertEqual(
            context["back_url"],
            "/admin/suppliers/overview/?supplier=2",
        )
        self.assertEqual(
            import_files.calls,
            [
                ("all", None),
                ("order_by", ("id",)),
                ("aggregate", ("updated_at",)),
            ],
        )

    def test_build_import_detail_context_falls_back_to_created_and_overview_url(self):
        from prices.services.import_history import build_import_detail_context

        created_at = timezone.make_aware(datetime(2026, 4, 29, 8, 0))
        import_files = self.FakeImportFiles(None)
        import_batch = SimpleNamespace(
            importfile_set=import_files,
            received_at=None,
            created_at=created_at,
        )
        request = RequestFactory().get(
            "/admin/imports/7/",
            {"next": "https://evil.example/phish"},
        )

        context = build_import_detail_context(import_batch, request)

        self.assertEqual(context["received_at_display"], created_at)
        self.assertEqual(context["updated_at_display"], created_at)
        self.assertEqual(str(context["back_url"]), "/admin/suppliers/overview/")


class ImportLogServiceTests(SimpleTestCase):
    class FakeDiagnosticQuerySet:
        def __init__(self):
            self.calls = []

        def filter(self, **kwargs):
            self.calls.append(kwargs)
            return self

    class FakeLogQuerySet:
        def __init__(self):
            self.calls = []

        def select_related(self, *fields):
            self.calls.append(("select_related", fields))
            return self

        def prefetch_related(self, *fields):
            self.calls.append(("prefetch_related", fields))
            return self

        def order_by(self, *fields):
            self.calls.append(("order_by", fields))
            return self

        def first(self):
            self.calls.append(("first", None))
            return self.first_result

    def test_detailed_log_runs_queryset_selects_supplier_and_orders(self):
        from prices.services.import_logs import detailed_log_runs_queryset

        manager = self.FakeLogQuerySet()

        result = detailed_log_runs_queryset(run_manager=manager)

        self.assertIs(result, manager)
        self.assertEqual(
            manager.calls,
            [
                ("select_related", ("supplier",)),
                ("order_by", ("-started_at",)),
            ],
        )

    def test_detailed_log_batches_queryset_selects_prefetches_and_orders(self):
        from prices.services.import_logs import detailed_log_batches_queryset

        manager = self.FakeLogQuerySet()

        result = detailed_log_batches_queryset(batch_manager=manager)

        self.assertIs(result, manager)
        self.assertEqual(
            manager.calls,
            [
                ("select_related", ("supplier", "mailbox")),
                ("prefetch_related", ("importfile_set", "importfile_set__mapping")),
                ("order_by", ("-created_at",)),
            ],
        )

    def test_detailed_log_diagnostics_queryset_selects_and_orders(self):
        from prices.services.import_logs import detailed_log_diagnostics_queryset

        manager = self.FakeLogQuerySet()

        result = detailed_log_diagnostics_queryset(diagnostic_manager=manager)

        self.assertIs(result, manager)
        self.assertEqual(
            manager.calls,
            [
                (
                    "select_related",
                    ("supplier", "mailbox", "import_batch", "import_file"),
                ),
                ("order_by", ("-created_at", "-id")),
            ],
        )

    def test_apply_run_filters_applies_supplier_and_status(self):
        from prices.services.import_logs import apply_run_filters

        runs = self.FakeDiagnosticQuerySet()

        result = apply_run_filters(
            runs,
            supplier_filter_ids=[2, 9],
            status_filter=models.EmailImportStatus.FINISHED,
        )

        self.assertIs(result, runs)
        self.assertEqual(
            runs.calls,
            [
                {"supplier_id__in": [2, 9]},
                {"status": models.EmailImportStatus.FINISHED},
            ],
        )

    def test_apply_batch_filters_maps_known_statuses(self):
        from prices.services.import_logs import apply_batch_filters

        batches = self.FakeDiagnosticQuerySet()

        result = apply_batch_filters(
            batches,
            supplier_filter_ids=[7],
            batch_status_filter="failed",
        )

        self.assertIs(result, batches)
        self.assertEqual(
            batches.calls,
            [
                {"supplier_id__in": [7]},
                {"status": models.ImportStatus.FAILED},
            ],
        )

    def test_apply_batch_filters_ignores_unknown_status(self):
        from prices.services.import_logs import apply_batch_filters

        batches = self.FakeDiagnosticQuerySet()

        apply_batch_filters(batches, batch_status_filter="unknown")

        self.assertEqual(batches.calls, [])

    def test_parse_diagnostic_date_bounds_returns_aware_bounds(self):
        from prices.services.import_logs import parse_diagnostic_date_bounds

        date_from, date_to = parse_diagnostic_date_bounds(
            date_from_raw="2026-04-30",
            date_to_raw="2026-05-01",
        )

        self.assertEqual(date_from.date(), date(2026, 4, 30))
        self.assertEqual(date_to.date(), date(2026, 5, 2))
        self.assertTrue(timezone.is_aware(date_from))
        self.assertTrue(timezone.is_aware(date_to))

    def test_parse_diagnostic_date_bounds_ignores_invalid_input(self):
        from prices.services.import_logs import parse_diagnostic_date_bounds

        self.assertEqual(
            parse_diagnostic_date_bounds(
                date_from_raw="not-a-date",
                date_to_raw="2026-05-01",
            ),
            (None, None),
        )

    def test_apply_diagnostic_filters_applies_all_supplied_filters(self):
        from prices.services.import_logs import apply_diagnostic_filters

        diagnostics = self.FakeDiagnosticQuerySet()

        result = apply_diagnostic_filters(
            diagnostics,
            supplier_filter_ids=[3, 5],
            decision_filter=models.AttachmentDecision.IMPORTED,
            reason_filter=models.AttachmentReason.DUPLICATE_HASH,
            mailbox_filter="7",
            filename_filter="price",
            sender_filter="supplier@example.com",
            date_from_raw="2026-04-30",
            date_to_raw="2026-05-01",
        )

        self.assertIs(result, diagnostics)
        self.assertEqual(diagnostics.calls[0], {"supplier_id__in": [3, 5]})
        self.assertEqual(
            diagnostics.calls[1],
            {"decision": models.AttachmentDecision.IMPORTED},
        )
        self.assertEqual(
            diagnostics.calls[2],
            {"reason_code": models.AttachmentReason.DUPLICATE_HASH},
        )
        self.assertEqual(diagnostics.calls[3], {"mailbox_id": "7"})
        self.assertEqual(diagnostics.calls[4], {"filename__icontains": "price"})
        self.assertEqual(
            diagnostics.calls[5],
            {"sender__icontains": "supplier@example.com"},
        )
        self.assertEqual(
            diagnostics.calls[6]["created_at__gte"].date(), date(2026, 4, 30)
        )
        self.assertEqual(
            diagnostics.calls[7]["created_at__lt"].date(), date(2026, 5, 2)
        )

    def test_apply_diagnostic_filters_ignores_invalid_dates(self):
        from prices.services.import_logs import apply_diagnostic_filters

        diagnostics = self.FakeDiagnosticQuerySet()

        apply_diagnostic_filters(
            diagnostics,
            date_from_raw="not-a-date",
            date_to_raw="2026-05-01",
        )

        self.assertEqual(diagnostics.calls, [])

    def test_render_batch_console_log_includes_files_and_batch_error(self):
        from prices.services.import_logs import render_batch_console_log

        now = timezone.make_aware(datetime(2026, 4, 30, 12, 0, 0))
        file_obj = SimpleNamespace(
            processed_at=now,
            status=models.ImportStatus.FAILED,
            file_kind=models.FileKind.PRICE,
            mapping=None,
            filename="bad.xlsx",
            error_message="Broken file",
        )
        batch = SimpleNamespace(
            received_at=now,
            created_at=now,
            mailbox=None,
            supplier=SimpleNamespace(name="Supplier A"),
            status=models.ImportStatus.FAILED,
            message_id="msg-1",
            error_message="Batch failed",
            importfile_set=SimpleNamespace(all=lambda: [file_obj]),
        )

        log = render_batch_console_log(batch)

        self.assertIn("BATCH supplier=Supplier A", log)
        self.assertIn("FILE status=failed kind=price", log)
        self.assertIn("ERROR Broken file", log)
        self.assertIn("BATCH_ERROR Batch failed", log)

    def test_render_run_console_log_prefers_existing_detailed_log(self):
        from prices.services.import_logs import render_run_console_log

        run = SimpleNamespace(detailed_log="already captured")

        self.assertEqual(render_run_console_log(run, []), "already captured")

    def test_render_run_console_log_filters_batches_by_supplier_and_time(self):
        from prices.services.import_logs import render_run_console_log

        start = timezone.make_aware(datetime(2026, 4, 30, 12, 0, 0))
        inside = start + timedelta(minutes=2)
        outside = start + timedelta(minutes=10)
        file_obj = SimpleNamespace(
            processed_at=inside,
            status=models.ImportStatus.PROCESSED,
            file_kind=models.FileKind.PRICE,
            mapping=None,
            filename="ok.xlsx",
            error_message="",
        )
        matching_batch = SimpleNamespace(
            supplier_id=7,
            received_at=inside,
            created_at=inside,
            mailbox=SimpleNamespace(name="Inbox"),
            supplier=SimpleNamespace(name="Supplier A"),
            status=models.ImportStatus.PROCESSED,
            importfile_set=SimpleNamespace(all=lambda: [file_obj]),
        )
        late_batch = SimpleNamespace(
            supplier_id=7,
            created_at=outside,
            received_at=outside,
            mailbox=None,
            supplier=SimpleNamespace(name="Supplier A"),
            status=models.ImportStatus.PROCESSED,
            importfile_set=SimpleNamespace(all=lambda: []),
        )
        other_supplier_batch = SimpleNamespace(
            supplier_id=8,
            created_at=inside,
            received_at=inside,
            mailbox=None,
            supplier=SimpleNamespace(name="Supplier B"),
            status=models.ImportStatus.PROCESSED,
            importfile_set=SimpleNamespace(all=lambda: []),
        )
        run = SimpleNamespace(
            detailed_log="",
            supplier_id=7,
            started_at=start,
            finished_at=start + timedelta(minutes=5),
        )

        log = render_run_console_log(
            run, [matching_batch, late_batch, other_supplier_batch]
        )

        self.assertIn("Supplier A", log)
        self.assertIn("ok.xlsx", log)
        self.assertNotIn("Supplier B", log)

    def test_build_import_detailed_logs_context_filters_pages_and_decorates(self):
        from prices.services.import_logs import build_import_detailed_logs_context

        request = RequestFactory().get(
            "/admin/import/logs/",
            {
                "supplier": ["2", "9"],
                "run_status": models.EmailImportStatus.FINISHED,
                "batch_status": "failed",
                "decision": models.AttachmentDecision.IMPORTED,
                "reason": models.AttachmentReason.DUPLICATE_HASH,
                "mailbox": "5",
                "filename": "prices",
                "sender": "supplier@example.com",
                "date_from": "2026-04-30",
                "date_to": "2026-05-01",
                "page": "1",
                "bpage": "1",
                "dpage": "1",
            },
        )
        run = SimpleNamespace()
        batch = SimpleNamespace()
        diagnostic = SimpleNamespace()
        calls = []

        def run_filter(rows, **kwargs):
            calls.append(("run", rows, kwargs))
            return rows

        def batch_filter(rows, **kwargs):
            calls.append(("batch", rows, kwargs))
            return rows

        def diagnostic_filter(rows, **kwargs):
            calls.append(("diagnostic", rows, kwargs))
            return rows

        context = build_import_detailed_logs_context(
            request,
            runs_queryset_func=lambda: [run],
            batches_queryset_func=lambda: [batch],
            diagnostics_queryset_func=lambda: [diagnostic],
            run_filter_func=run_filter,
            batch_filter_func=batch_filter,
            diagnostic_filter_func=diagnostic_filter,
            render_run_func=lambda item, batches: f"run log with {len(batches)} batch",
            render_batch_func=lambda item: "batch log",
            supplier_options_func=lambda: ["suppliers"],
            mailbox_options_func=lambda: ["mailboxes"],
        )

        self.assertEqual(context["supplier_filter"], "2,9")
        self.assertEqual(context["status_filter"], models.EmailImportStatus.FINISHED)
        self.assertEqual(context["batch_status_filter"], "failed")
        self.assertEqual(context["decision_filter"], models.AttachmentDecision.IMPORTED)
        self.assertEqual(
            context["reason_filter"], models.AttachmentReason.DUPLICATE_HASH
        )
        self.assertEqual(context["mailbox_filter"], "5")
        self.assertEqual(context["filename_filter"], "prices")
        self.assertEqual(context["sender_filter"], "supplier@example.com")
        self.assertEqual(context["date_from"], "2026-04-30")
        self.assertEqual(context["date_to"], "2026-05-01")
        self.assertEqual(context["supplier_options"], ["suppliers"])
        self.assertEqual(context["mailbox_options"], ["mailboxes"])
        self.assertEqual(context["runs_page"].object_list, [run])
        self.assertEqual(context["batches_page"].object_list, [batch])
        self.assertEqual(context["diagnostics_page"].object_list, [diagnostic])
        self.assertEqual(run.console_log, "run log with 1 batch")
        self.assertEqual(batch.console_log, "batch log")
        self.assertEqual(
            calls,
            [
                (
                    "run",
                    [run],
                    {
                        "supplier_filter_ids": [2, 9],
                        "status_filter": models.EmailImportStatus.FINISHED,
                    },
                ),
                (
                    "batch",
                    [batch],
                    {
                        "supplier_filter_ids": [2, 9],
                        "batch_status_filter": "failed",
                    },
                ),
                (
                    "diagnostic",
                    [diagnostic],
                    {
                        "supplier_filter_ids": [2, 9],
                        "decision_filter": models.AttachmentDecision.IMPORTED,
                        "reason_filter": models.AttachmentReason.DUPLICATE_HASH,
                        "mailbox_filter": "5",
                        "filename_filter": "prices",
                        "sender_filter": "supplier@example.com",
                        "date_from_raw": "2026-04-30",
                        "date_to_raw": "2026-05-01",
                    },
                ),
            ],
        )


class CurrencyServiceTests(SimpleTestCase):
    def test_convert_price_uses_direct_and_inverse_rates_without_database(self):
        from prices.services.currency import convert_price

        rates = {
            (models.Currency.USD, models.Currency.RUB): Decimal("100.00"),
        }

        self.assertEqual(
            convert_price(
                Decimal("2.50"),
                models.Currency.USD,
                models.Currency.RUB,
                rates,
            ),
            Decimal("250.0000"),
        )
        self.assertEqual(
            convert_price(
                Decimal("250.00"),
                models.Currency.RUB,
                models.Currency.USD,
                rates,
            ),
            Decimal("2.50"),
        )

    def test_convert_price_returns_original_when_rate_missing_or_same_currency(self):
        from prices.services.currency import convert_price

        price = Decimal("12.34")

        self.assertEqual(
            convert_price(price, models.Currency.USD, models.Currency.USD, {}), price
        )
        self.assertEqual(
            convert_price(price, models.Currency.USD, models.Currency.RUB, {}), price
        )
        self.assertIsNone(
            convert_price(None, models.Currency.USD, models.Currency.RUB, {})
        )

    def test_format_price_uses_known_currency_symbols(self):
        from prices.services.currency import format_price

        self.assertEqual(format_price(Decimal("12.3"), models.Currency.USD), "12.30 $")
        self.assertEqual(format_price(Decimal("99"), models.Currency.RUB), "99.00 ₽")
        self.assertEqual(format_price(None, models.Currency.RUB), "-")

    def test_sync_cbr_markup_rates_for_dates_counts_successes_and_failures(self):
        from prices.services.currency import sync_cbr_markup_rates_for_dates

        calls = []

        def sync_func(rate_date, markup_percent):
            calls.append((rate_date, markup_percent))
            if rate_date == date(2026, 4, 30):
                raise RuntimeError("CBR unavailable")

        summary = sync_cbr_markup_rates_for_dates(
            {date(2026, 5, 1), date(2026, 4, 30), date(2026, 4, 29)},
            Decimal("7.50"),
            sync_func=sync_func,
        )

        self.assertEqual(summary.synced, 2)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(
            calls,
            [
                (date(2026, 4, 29), Decimal("7.50")),
                (date(2026, 4, 30), Decimal("7.50")),
                (date(2026, 5, 1), Decimal("7.50")),
            ],
        )

    def test_recalculate_cbr_rates_for_processed_price_imports_filters_and_syncs_dates(
        self,
    ):
        from prices.services.currency import (
            recalculate_cbr_rates_for_processed_price_imports,
        )

        calls = []
        batches = [
            SimpleNamespace(
                received_at=timezone.make_aware(datetime(2026, 4, 29, 9, 0)),
                created_at=None,
            ),
            SimpleNamespace(
                received_at=timezone.make_aware(datetime(2026, 4, 30, 9, 0)),
                created_at=None,
            ),
            SimpleNamespace(
                received_at=timezone.make_aware(datetime(2026, 5, 1, 9, 0)),
                created_at=None,
            ),
        ]

        class FakeBatchQuerySet:
            def __init__(self, rows):
                self.rows = rows

            def distinct(self):
                calls.append(("distinct", None))
                return self

            def filter(self, **kwargs):
                calls.append(("queryset_filter", kwargs))
                return self

            def only(self, *fields):
                calls.append(("only", fields))
                return self

            def __iter__(self):
                return iter(self.rows)

        class FakeBatchManager:
            def filter(self, **kwargs):
                calls.append(("manager_filter", kwargs))
                return FakeBatchQuerySet(batches)

        sync_calls = []

        def sync_func(rate_date, markup_percent):
            sync_calls.append((rate_date, markup_percent))

        result = recalculate_cbr_rates_for_processed_price_imports(
            supplier_ids=["7"],
            start_date=date(2026, 4, 30),
            end_date=date(2026, 5, 1),
            markup_percent=Decimal("3.5"),
            batch_manager=FakeBatchManager(),
            sync_func=sync_func,
        )

        self.assertEqual(result.import_dates, {date(2026, 4, 30), date(2026, 5, 1)})
        self.assertEqual(result.summary.synced, 2)
        self.assertEqual(result.summary.failed, 0)
        self.assertEqual(
            calls,
            [
                (
                    "manager_filter",
                    {
                        "importfile__file_kind": models.FileKind.PRICE,
                        "importfile__status": models.ImportStatus.PROCESSED,
                    },
                ),
                ("distinct", None),
                ("queryset_filter", {"supplier_id__in": ["7"]}),
                ("only", ("received_at", "created_at")),
            ],
        )
        self.assertEqual(
            sync_calls,
            [
                (date(2026, 4, 30), Decimal("3.5")),
                (date(2026, 5, 1), Decimal("3.5")),
            ],
        )

    def test_run_supplier_rates_recalculation_action_validates_date_order(self):
        from prices.services.currency import run_supplier_rates_recalculation_action

        result = run_supplier_rates_recalculation_action(
            ["7"],
            start_raw="2026-05-01",
            end_raw="2026-04-30",
        )

        self.assertEqual(result.message_level, "info")
        self.assertEqual(result.message, "End date must be on or after start date.")

    def test_run_supplier_rates_recalculation_action_reports_no_import_dates(self):
        from prices.services.currency import run_supplier_rates_recalculation_action

        calls = []

        def fake_recalculate(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(import_dates=set(), summary=None)

        result = run_supplier_rates_recalculation_action(
            ["7"],
            start_raw="2026-04-01",
            end_raw="2026-04-30",
            settings_func=lambda: SimpleNamespace(cbr_markup_percent=Decimal("3.5")),
            recalculate_func=fake_recalculate,
        )

        self.assertEqual(
            calls,
            [
                {
                    "supplier_ids": ["7"],
                    "start_date": date(2026, 4, 1),
                    "end_date": date(2026, 4, 30),
                    "markup_percent": Decimal("3.5"),
                }
            ],
        )
        self.assertEqual(result.message_level, "info")
        self.assertEqual(result.message, "No import dates found for selected filters.")

    def test_run_supplier_rates_recalculation_action_reports_success(self):
        from prices.services.currency import (
            RateSyncSummary,
            run_supplier_rates_recalculation_action,
        )

        result = run_supplier_rates_recalculation_action(
            ["7"],
            settings_func=lambda: SimpleNamespace(cbr_markup_percent=Decimal("3.5")),
            recalculate_func=lambda **kwargs: SimpleNamespace(
                import_dates={date(2026, 4, 30), date(2026, 5, 1)},
                summary=RateSyncSummary(synced=2, failed=0),
            ),
        )

        self.assertEqual(result.message_level, "success")
        self.assertEqual(
            result.message,
            "Rate recalculation finished: 2 day(s) synced.",
        )

    def test_run_supplier_rates_recalculation_action_reports_partial_failure(self):
        from prices.services.currency import (
            RateSyncSummary,
            run_supplier_rates_recalculation_action,
        )

        result = run_supplier_rates_recalculation_action(
            ["7"],
            settings_func=lambda: SimpleNamespace(cbr_markup_percent=Decimal("3.5")),
            recalculate_func=lambda **kwargs: SimpleNamespace(
                import_dates={date(2026, 4, 30), date(2026, 5, 1)},
                summary=RateSyncSummary(synced=1, failed=1),
            ),
        )

        self.assertEqual(result.message_level, "warning")
        self.assertEqual(
            result.message,
            "Rate recalculation finished: 1 day(s) synced, 1 failed.",
        )

    def test_attach_display_prices_sets_display_fields_without_database(self):
        from prices.services.currency import attach_display_prices

        products = [
            SimpleNamespace(
                current_price=Decimal("2.50"),
                currency=models.Currency.USD,
            ),
            SimpleNamespace(
                current_price=Decimal("120.00"),
                currency=models.Currency.RUB,
            ),
        ]
        rates = {
            (models.Currency.USD, models.Currency.RUB): Decimal("100.00"),
        }

        attach_display_prices(products, models.Currency.RUB, rates)

        self.assertEqual(products[0].display_currency, models.Currency.RUB)
        self.assertEqual(products[0].display_price, Decimal("250.0000"))
        self.assertEqual(products[1].display_currency, models.Currency.RUB)
        self.assertEqual(products[1].display_price, Decimal("120.00"))


class ProductDisplayServiceTests(SimpleTestCase):
    def test_supplier_product_detail_back_url_uses_safe_next_url(self):
        from prices.services.product_display import supplier_product_detail_back_url

        back_url = supplier_product_detail_back_url(
            next_url_raw="/admin/products/?q=oud",
            host="testserver",
            fallback_url_name="prices:product_list",
        )

        self.assertEqual(back_url, "/admin/products/?q=oud")

    def test_supplier_product_detail_back_url_rejects_external_next_url(self):
        from prices.services.product_display import supplier_product_detail_back_url

        back_url = supplier_product_detail_back_url(
            next_url_raw="https://evil.example/products/",
            host="testserver",
            fallback_url_name="prices:product_list",
        )

        self.assertEqual(back_url, "/admin/products/")

    def test_supplier_product_detail_back_url_supports_viewer_fallback(self):
        from prices.services.product_display import supplier_product_detail_back_url

        back_url = supplier_product_detail_back_url(
            next_url_raw="",
            host="testserver",
            fallback_url_name="viewer_home",
        )

        self.assertEqual(back_url, "/")

    def test_supplier_product_detail_context_builds_display_context(self):
        from prices.services.product_display import (
            build_supplier_product_detail_context,
        )

        product = SimpleNamespace(id=7, our_product=SimpleNamespace(id=3))
        request = RequestFactory().get(
            "/admin/products/7/",
            {"next": "/admin/products/?q=oud", "history_page": "2"},
        )
        calls = []

        class FakeForm:
            def __init__(self, *, instance):
                self.instance = instance
                calls.append(("form", instance))

        def history_builder(product_arg, query_params):
            calls.append(("history", product_arg, query_params.get("history_page")))
            return {"snapshots": ["snapshot"]}

        context = build_supplier_product_detail_context(
            product,
            request.GET,
            next_url_raw=request.GET.get("next", ""),
            host="testserver",
            fallback_url_name="prices:product_list",
            link_form_class=FakeForm,
            history_context_builder=history_builder,
        )

        self.assertEqual(context["back_url"], "/admin/products/?q=oud")
        self.assertIs(context["link_form"].instance, product)
        self.assertIs(context["our_product"], product.our_product)
        self.assertEqual(context["snapshots"], ["snapshot"])
        self.assertEqual(
            calls,
            [
                ("form", product),
                ("history", product, "2"),
            ],
        )

    def test_supplier_product_list_context_builds_template_contract(self):
        from prices.services.product_display import build_supplier_product_list_context
        from prices.services.product_filters import SupplierProductFilterState

        products = [SimpleNamespace(id=1)]
        filter_state = SupplierProductFilterState(
            query="oud",
            include_tokens=["oud"],
            inline_exclude_tokens=[],
            exclude_raw="tester",
            exclude_terms=["tester"],
            currency=models.Currency.RUB,
            supplier_filter_ids=[2],
            include_inactive_suppliers=False,
            status_filter="active",
            smart_search_enabled=True,
        )
        calls = []

        def filter_builder(state, *, price_min_raw, price_max_raw):
            calls.append(("filter", state, price_min_raw, price_max_raw))
            return {
                "currency_filter": state.currency,
                "price_min": price_min_raw,
                "price_max": price_max_raw,
            }

        def attach_display(object_list, currency):
            calls.append(("display", object_list, currency))

        context = build_supplier_product_list_context(
            {"object_list": products},
            filter_state,
            price_min_raw="10",
            price_max_raw="100",
            show_currency_filter=True,
            show_cleanup=False,
            show_search=True,
            link_detail=True,
            show_status=True,
            show_actions=False,
            show_bulk_delete=True,
            search_url="/admin/products/search/",
            detail_base_url="/admin/products/",
            filter_context_builder=filter_builder,
            attach_display_func=attach_display,
        )

        self.assertEqual(context["currency_filter"], models.Currency.RUB)
        self.assertEqual(context["price_min"], "10")
        self.assertEqual(context["price_max"], "100")
        self.assertTrue(context["show_currency_filter"])
        self.assertFalse(context["show_cleanup"])
        self.assertTrue(context["show_search"])
        self.assertTrue(context["link_detail"])
        self.assertTrue(context["show_status"])
        self.assertFalse(context["show_actions"])
        self.assertTrue(context["show_bulk_delete"])
        self.assertEqual(context["search_url"], "/admin/products/search/")
        self.assertEqual(context["detail_base_url"], "/admin/products/")
        self.assertEqual(
            calls,
            [
                ("filter", filter_state, "10", "100"),
                ("display", products, models.Currency.RUB),
            ],
        )

    def test_sparkline_renderer_outputs_flat_line_for_missing_history(self):
        from prices.services.product_display import render_product_sparkline_svg

        svg = str(render_product_sparkline_svg([], "up"))

        self.assertIn('class="product-sparkline"', svg)
        self.assertIn("<line", svg)
        self.assertIn('stroke="#e2e2e2"', svg)

    def test_sparkline_renderer_uses_delta_color_for_history(self):
        from prices.services.product_display import render_product_sparkline_svg

        svg = str(render_product_sparkline_svg([10, 12, 9], "down"))

        self.assertIn("<polyline", svg)
        self.assertIn('stroke="#22c55e"', svg)
        self.assertIn("197.0,29.0", svg)

    def test_build_supplier_product_sparklines_returns_empty_without_products(self):
        from prices.services.product_display import build_supplier_product_sparklines

        self.assertEqual(build_supplier_product_sparklines([]), {})

    def test_attach_supplier_product_list_display_adds_prices_and_sparklines(self):
        from prices.services.product_display import attach_supplier_product_list_display

        product = SimpleNamespace(
            id=7,
            current_price=Decimal("10.00"),
            currency=models.Currency.USD,
            price_delta_direction="down",
        )
        rates = {(models.Currency.USD, models.Currency.RUB): Decimal("100.00")}

        def attach_prices(products, display_currency, rates_arg):
            self.assertEqual(display_currency, models.Currency.RUB)
            self.assertEqual(rates_arg, rates)
            products[0].display_price = Decimal("1000.00")
            products[0].display_currency = display_currency

        with (
            patch(
                "prices.services.product_display.get_latest_rates", return_value=rates
            ),
            patch(
                "prices.services.product_display.attach_display_prices",
                side_effect=attach_prices,
            ) as display_prices,
            patch(
                "prices.services.product_display.attach_previous_price_deltas"
            ) as price_deltas,
            patch(
                "prices.services.product_display.build_supplier_product_sparklines",
                return_value={7: [1.0, 2.0]},
            ),
        ):
            attach_supplier_product_list_display([product], models.Currency.RUB)

        display_prices.assert_called_once()
        price_deltas.assert_called_once()
        self.assertEqual(product.original_price_display, "10.00 $")
        self.assertEqual(product.sparkline_values, [1.0, 2.0])
        self.assertIn("product-sparkline", str(product.sparkline_svg))
        self.assertIn('stroke="#22c55e"', str(product.sparkline_svg))

    def test_attach_supplier_product_search_display_keeps_sparklines_without_deltas(
        self,
    ):
        from prices.services.product_display import (
            attach_supplier_product_search_display,
        )

        product = SimpleNamespace(
            id=7,
            current_price=Decimal("10.00"),
            currency=models.Currency.USD,
        )
        rates = {(models.Currency.USD, models.Currency.RUB): Decimal("100.00")}

        def attach_prices(products, display_currency, rates_arg):
            self.assertEqual(display_currency, models.Currency.RUB)
            self.assertEqual(rates_arg, rates)
            products[0].display_price = Decimal("1000.00")
            products[0].display_currency = display_currency

        with (
            patch(
                "prices.services.product_display.get_latest_rates", return_value=rates
            ),
            patch(
                "prices.services.product_display.attach_display_prices",
                side_effect=attach_prices,
            ) as display_prices,
            patch(
                "prices.services.product_display.attach_previous_price_deltas"
            ) as price_deltas,
            patch(
                "prices.services.product_display.build_supplier_product_sparklines",
                return_value={7: [10.0, 11.0, 9.5]},
            ) as sparklines,
        ):
            attach_supplier_product_search_display([product], models.Currency.RUB)

        display_prices.assert_called_once()
        price_deltas.assert_not_called()
        sparklines.assert_called_once_with([product])
        self.assertEqual(product.display_price, Decimal("1000.00"))
        self.assertEqual(product.sparkline_values, [10.0, 11.0, 9.5])
        self.assertEqual(product.price_delta_value, None)

    def test_supplier_product_search_row_serializer_formats_ajax_payload(self):
        from prices.services.currency import format_price
        from prices.services.product_display import (
            serialize_supplier_product_search_row,
        )

        imported_at = timezone.make_aware(datetime(2026, 4, 30, 10, 0, 0))
        now = timezone.make_aware(datetime(2026, 4, 30, 12, 0, 0))
        product = SimpleNamespace(
            id=7,
            supplier=SimpleNamespace(name="Supplier A"),
            supplier_id=3,
            supplier_sku="SKU-1",
            name="Amber Oud",
            current_price=Decimal("10.00"),
            currency=models.Currency.USD,
            display_price=Decimal("1000.00"),
            display_currency=models.Currency.RUB,
            last_imported_at=imported_at,
            is_active=True,
            price_delta_direction="up",
            price_delta_value=Decimal("50.00"),
            price_delta_percent=Decimal("5.5"),
        )

        with patch("prices.services.supplier_board.timezone.now", return_value=now):
            row = serialize_supplier_product_search_row(product, [1.0, 2.0])

        self.assertEqual(row["id"], 7)
        self.assertEqual(row["supplier"], "Supplier A")
        self.assertEqual(
            row["current_price"], format_price(Decimal("1000.00"), models.Currency.RUB)
        )
        self.assertEqual(row["original_price"], "10.00 $")
        self.assertEqual(row["last_imported_at"], "2h ago")
        self.assertEqual(row["last_imported_at_full"], "30.04.2026 10:00")
        self.assertEqual(
            row["price_delta_value"],
            format_price(Decimal("50.00"), models.Currency.RUB),
        )
        self.assertEqual(row["price_delta_percent"], "5.50%")
        self.assertEqual(row["sparkline"], [1.0, 2.0])

    def test_supplier_product_search_response_serializes_page_metadata(self):
        from prices.services.product_display import (
            build_supplier_product_search_response,
        )

        products = [
            SimpleNamespace(id=1, sparkline_values=[1.0]),
            SimpleNamespace(id=2, sparkline_values=[2.0]),
            SimpleNamespace(id=3, sparkline_values=[3.0]),
        ]

        with (
            patch(
                "prices.services.product_display.attach_supplier_product_search_display"
            ) as attach_display,
            patch(
                "prices.services.product_display.serialize_supplier_product_search_row",
                side_effect=lambda product, sparkline: {
                    "id": product.id,
                    "sparkline": sparkline,
                },
            ),
        ):
            response = build_supplier_product_search_response(
                products,
                page_raw="bad",
                currency=models.Currency.USD,
                page_size=2,
            )

        attach_display.assert_called_once_with(products[:2], models.Currency.USD)
        self.assertEqual(response["count"], None)
        self.assertEqual(response["count_display"], "2+")
        self.assertEqual(response["shown"], 2)
        self.assertEqual(response["page"], 1)
        self.assertEqual(response["num_pages"], None)
        self.assertTrue(response["has_next"])
        self.assertFalse(response["has_previous"])
        self.assertEqual(response["next_page"], 2)
        self.assertEqual(response["previous_page"], None)
        self.assertEqual(
            response["items"],
            [{"id": 1, "sparkline": [1.0]}, {"id": 2, "sparkline": [2.0]}],
        )

    def test_supplier_product_search_payload_builds_ajax_payload(self):
        from prices.services.product_display import (
            build_supplier_product_search_payload,
        )
        from prices.services.product_filters import SupplierProductFilterState

        request = RequestFactory().get("/admin/products/search/", {"page": "3"})
        filter_state = SupplierProductFilterState(
            query="",
            include_tokens=[],
            inline_exclude_tokens=[],
            exclude_raw="",
            exclude_terms=[],
            currency=models.Currency.USD,
            supplier_filter_ids=[],
            include_inactive_suppliers=False,
            status_filter="all",
            smart_search_enabled=False,
        )
        query_result = SimpleNamespace(
            queryset=["product"],
            filter_state=filter_state,
        )
        rates = {(models.Currency.USD, models.Currency.RUB): Decimal("100.00")}
        calls = []

        def rates_getter():
            calls.append(("rates",))
            return rates

        def queryset_builder(request_arg, *, rates, fast_search_default_order=False):
            calls.append(("queryset", request_arg, rates, fast_search_default_order))
            return query_result

        def response_builder(queryset, *, page_raw, currency):
            calls.append(("response", queryset, page_raw, currency))
            return {"items": [{"id": 7}], "page": page_raw, "currency": currency}

        payload = build_supplier_product_search_payload(
            request,
            rates_getter=rates_getter,
            queryset_builder=queryset_builder,
            response_builder=response_builder,
        )

        self.assertEqual(
            payload, {"items": [{"id": 7}], "page": "3", "currency": "USD"}
        )
        self.assertEqual(
            calls,
            [
                ("rates",),
                ("queryset", request, rates, True),
                ("response", ["product"], "3", models.Currency.USD),
            ],
        )

    def test_price_chart_currency_helpers_normalize_and_pick_symbol(self):
        from prices.services.product_display import (
            normalize_price_chart_currency,
            price_chart_currency_symbol,
        )

        self.assertEqual(normalize_price_chart_currency(" USD "), "usd")
        self.assertEqual(normalize_price_chart_currency("eur"), "original")
        self.assertEqual(price_chart_currency_symbol("rub"), "\u20bd")
        self.assertEqual(price_chart_currency_symbol("original"), "")

    def test_product_history_datetime_parser_accepts_date_and_datetime_values(self):
        from prices.services.product_display import (
            expand_product_history_end_datetime,
            parse_product_history_datetime,
        )

        start_dt = parse_product_history_datetime("2026-04-30")
        end_dt = parse_product_history_datetime("2026-04-30T14:30:00")

        self.assertIsNotNone(start_dt)
        self.assertEqual(start_dt.hour, 0)
        self.assertIsNotNone(end_dt)
        self.assertEqual(end_dt.hour, 14)
        expanded_end = expand_product_history_end_datetime("2026-04-30", start_dt)
        self.assertEqual(expanded_end.hour, 23)
        self.assertEqual(expanded_end.minute, 59)
        self.assertIsNone(parse_product_history_datetime("not-a-date"))

    def test_supplier_product_detail_history_context_applies_filters(self):
        from prices.services.product_display import (
            build_supplier_product_detail_history_context,
        )

        class FakeQuerySet:
            def __init__(self):
                self.calls = []

            def only(self, *fields):
                self.calls.append(("only", fields))
                return self

            def order_by(self, *fields):
                self.calls.append(("order_by", fields))
                return self

            def filter(self, **kwargs):
                self.calls.append(("filter", kwargs))
                return self

        class FakeManager:
            def __init__(self):
                self.queryset = FakeQuerySet()
                self.calls = []

            def filter(self, **kwargs):
                self.calls.append(("filter", kwargs))
                return self.queryset

        product = SimpleNamespace(id=7)
        manager = FakeManager()
        request = RequestFactory().get(
            "/products/7/",
            {
                "start": "2026-04-01",
                "end": "2026-04-03",
                "history_page": "2",
                "chart_currency": "rub",
            },
        )
        latest_by_day = [SimpleNamespace(id=1)]
        calls = []

        def latest_per_day(queryset):
            calls.append(("latest", queryset))
            return latest_by_day

        def history_builder(latest, *, history_page_raw, query_params, chart_currency):
            calls.append(
                ("history", latest, history_page_raw, query_params, chart_currency)
            )
            return {"snapshots": latest}

        context = build_supplier_product_detail_history_context(
            product,
            request.GET,
            snapshot_manager=manager,
            latest_per_day_func=latest_per_day,
            history_context_builder=history_builder,
        )

        self.assertEqual(manager.calls, [("filter", {"supplier_product": product})])
        self.assertEqual(manager.queryset.calls[1], ("order_by", ("-recorded_at",)))
        self.assertEqual(
            [call[0] for call in manager.queryset.calls if call[0] == "filter"],
            ["filter", "filter"],
        )
        self.assertEqual(calls[0], ("latest", manager.queryset))
        self.assertEqual(calls[1][1], latest_by_day)
        self.assertEqual(calls[1][2], "2")
        self.assertEqual(calls[1][4], "rub")
        self.assertEqual(context["snapshots"], latest_by_day)
        self.assertEqual(context["start_value"], "2026-04-01")
        self.assertEqual(context["end_value"], "2026-04-03")

    def test_price_history_chart_uses_latest_order_and_converted_currency(self):
        from prices.services.product_display import build_price_history_chart

        older_at = timezone.make_aware(datetime(2026, 4, 29, 9, 0, 0))
        newer_at = timezone.make_aware(datetime(2026, 4, 30, 9, 0, 0))
        snapshots = [
            SimpleNamespace(
                recorded_at=newer_at,
                price=Decimal("12.00"),
                currency=models.Currency.USD,
                price_rub=None,
                price_usd=Decimal("12.00"),
            ),
            SimpleNamespace(
                recorded_at=older_at,
                price=Decimal("10.00"),
                currency=models.Currency.USD,
                price_rub=None,
                price_usd=Decimal("10.00"),
            ),
        ]
        rates = {
            older_at.date(): {
                (models.Currency.USD, models.Currency.RUB): Decimal("90.00")
            },
            newer_at.date(): {
                (models.Currency.USD, models.Currency.RUB): Decimal("100.00")
            },
        }

        labels, values = build_price_history_chart(snapshots, "rub", rates)

        self.assertEqual(labels, ["29/04/2026", "30/04/2026"])
        self.assertEqual(values, [900.0, 1200.0])

    def test_price_history_chart_falls_back_to_original_price(self):
        from prices.services.product_display import build_price_history_chart

        recorded_at = timezone.make_aware(datetime(2026, 4, 30, 9, 0, 0))
        snapshot = SimpleNamespace(
            recorded_at=recorded_at,
            price=Decimal("42.00"),
            currency="EUR",
            price_rub=None,
            price_usd=None,
        )

        labels, values = build_price_history_chart(
            [snapshot], "usd", {recorded_at.date(): {}}
        )

        self.assertEqual(labels, ["30/04/2026"])
        self.assertEqual(values, [42.0])

    def test_price_history_context_builds_pagination_chart_and_querystring(self):
        from prices.services.product_display import build_price_history_context

        recorded_at = timezone.make_aware(datetime(2026, 4, 30, 9, 0, 0))
        snapshots = [
            SimpleNamespace(id=1, recorded_at=recorded_at),
            SimpleNamespace(id=2, recorded_at=recorded_at),
            SimpleNamespace(id=3, recorded_at=recorded_at),
        ]
        query_params = (
            RequestFactory()
            .get(
                "/products/1/",
                {
                    "history_page": "2",
                    "start": "2026-04-01",
                    "chart_currency": "rub",
                },
            )
            .GET
        )

        with (
            patch(
                "prices.services.product_display.prime_rates_cache_for_dates"
            ) as prime_rates,
            patch(
                "prices.services.product_display.build_price_history_chart",
                return_value=(["30/04/2026"], [100.0]),
            ) as build_chart,
            patch(
                "prices.services.product_display.attach_snapshot_display_prices"
            ) as attach_display,
        ):
            context = build_price_history_context(
                snapshots,
                history_page_raw="2",
                query_params=query_params,
                chart_currency="rub",
                page_size=2,
            )

        self.assertEqual(context["snapshots"], snapshots[2:])
        self.assertTrue(context["history_is_paginated"])
        self.assertEqual(context["history_page_obj"].number, 2)
        self.assertEqual(
            context["history_querystring"], "start=2026-04-01&chart_currency=rub"
        )
        self.assertEqual(context["chart_labels"], ["30/04/2026"])
        self.assertEqual(context["chart_values"], [100.0])
        self.assertEqual(context["chart_currency"], "rub")
        self.assertEqual(context["chart_currency_symbol"], "\u20bd")
        prime_rates.assert_called_once()
        build_chart.assert_called_once()
        attach_display.assert_called_once()
        self.assertEqual(attach_display.call_args.args[0], snapshots[2:])

    def test_attach_snapshot_display_prices_adds_converted_prices_and_rate(self):
        from prices.services.product_display import attach_snapshot_display_prices

        recorded_at = timezone.make_aware(datetime(2026, 4, 30, 9, 0, 0))
        snapshot = SimpleNamespace(
            recorded_at=recorded_at,
            price=Decimal("10.00"),
            currency=models.Currency.USD,
            price_rub=None,
            price_usd=Decimal("10.00"),
        )
        rates = {
            recorded_at.date(): {
                (models.Currency.USD, models.Currency.RUB): Decimal("100.00"),
            }
        }

        attach_snapshot_display_prices([snapshot], rates)

        self.assertEqual(snapshot.display_price_rub, Decimal("1000.0000"))
        self.assertEqual(snapshot.display_price_usd, Decimal("10.00"))
        self.assertEqual(snapshot.display_exchange_rate, Decimal("100.00"))


class ImportOperationServiceTests(SimpleTestCase):
    def test_normalize_supplier_import_source_accepts_known_sources(self):
        from prices.services.import_operations import (
            normalize_supplier_import_source,
            supplier_import_tab_url,
        )

        self.assertEqual(normalize_supplier_import_source("link"), "link")
        self.assertEqual(normalize_supplier_import_source("bad"), "email")
        self.assertEqual(
            normalize_supplier_import_source("bad", default="file"),
            "file",
        )
        self.assertEqual(
            supplier_import_tab_url(7, "bad", default="file"),
            "/admin/suppliers/7/import/?source=file",
        )

    def test_import_board_redirect_url_uses_safe_next_url(self):
        from prices.services.import_operations import import_board_redirect_url

        self.assertEqual(
            import_board_redirect_url(
                next_url_raw="/import-prices/?supplier=7",
                host="testserver",
                is_staff=True,
            ),
            "/import-prices/?supplier=7",
        )

    def test_import_board_redirect_url_rejects_external_next_url(self):
        from prices.services.import_operations import import_board_redirect_url

        self.assertEqual(
            import_board_redirect_url(
                next_url_raw="https://evil.example/import-prices/",
                host="testserver",
                is_staff=True,
            ),
            "/admin/suppliers/overview/",
        )

    def test_import_board_redirect_url_falls_back_by_user_role(self):
        from prices.services.import_operations import import_board_redirect_url

        self.assertEqual(
            import_board_redirect_url(is_staff=True),
            "/admin/suppliers/overview/",
        )
        self.assertEqual(
            import_board_redirect_url(is_staff=False),
            "/import-prices/",
        )

    def test_import_settings_or_overview_redirect_name(self):
        from prices.services.import_operations import (
            import_settings_or_overview_redirect_name,
        )

        self.assertEqual(
            import_settings_or_overview_redirect_name("import_settings"),
            "prices:import_settings",
        )
        self.assertEqual(
            import_settings_or_overview_redirect_name("bad"),
            "prices:supplier_overview",
        )
        self.assertEqual(
            import_settings_or_overview_redirect_name(None),
            "prices:supplier_overview",
        )

    def test_build_import_wizard_initial_adds_query_values(self):
        from prices.services.import_operations import build_import_wizard_initial

        initial = build_import_wizard_initial(
            initial={"existing": "value"},
            supplier_raw="7",
            file_kind_raw=models.FileKind.STOCK,
        )

        self.assertEqual(
            initial,
            {
                "existing": "value",
                "supplier": "7",
                "file_kind": models.FileKind.STOCK,
            },
        )

    def test_build_import_wizard_initial_ignores_empty_query_values(self):
        from prices.services.import_operations import build_import_wizard_initial

        initial = build_import_wizard_initial(
            initial={"supplier": "existing"},
            supplier_raw="",
            file_kind_raw=None,
        )

        self.assertEqual(initial, {"supplier": "existing"})

    def test_build_supplier_mapping_defaults_splits_sheet_selector_and_columns(self):
        from prices.services.import_operations import build_supplier_mapping_defaults

        defaults = build_supplier_mapping_defaults(
            {
                "sheet_selector": "Main, 2, Backup, 5",
                "name_columns": "1, 3, bad",
                "sku_column": 4,
                "price_column": 6,
                "currency_column": "",
                "header_row": 2,
            }
        )

        self.assertEqual(defaults["mapping_mode"], models.MappingMode.INDEX)
        self.assertEqual(defaults["sheet_names"], "Main, Backup")
        self.assertEqual(defaults["sheet_indexes"], "2, 5")
        self.assertEqual(defaults["header_row"], 2)
        self.assertEqual(
            defaults["column_map"],
            {"sku": 4, "name": [1, 3], "price": 6, "currency": 0},
        )

    def test_build_supplier_mapping_preview_result_requires_file(self):
        from prices.services.import_operations import (
            build_supplier_mapping_preview_result,
        )

        result = build_supplier_mapping_preview_result({}, {})

        self.assertEqual(result.status, 400)
        self.assertEqual(result.payload, {"error": "No file uploaded."})

    def test_build_supplier_mapping_preview_result_passes_numeric_sheet_index(self):
        from prices.services.import_operations import (
            build_supplier_mapping_preview_result,
        )

        calls = []
        upload = SimpleNamespace(name="prices.xlsx")

        def fake_preview(file_obj, sheet_index):
            calls.append((file_obj, sheet_index))
            return {"headers": ["name", "price"]}

        result = build_supplier_mapping_preview_result(
            {"file": upload},
            {"sheet_index": "2"},
            preview_func=fake_preview,
        )

        self.assertEqual(result.status, 200)
        self.assertEqual(result.payload, {"headers": ["name", "price"]})
        self.assertEqual(calls, [(upload, 2)])

    def test_build_supplier_mapping_preview_result_ignores_invalid_sheet_index(self):
        from prices.services.import_operations import (
            build_supplier_mapping_preview_result,
        )

        calls = []
        upload = SimpleNamespace(name="prices.xlsx")

        def fake_preview(file_obj, sheet_index):
            calls.append((file_obj, sheet_index))
            return {"rows": []}

        result = build_supplier_mapping_preview_result(
            {"file": upload},
            {"sheet_index": "bad"},
            preview_func=fake_preview,
        )

        self.assertEqual(result.payload, {"rows": []})
        self.assertEqual(calls, [(upload, None)])

    def test_build_supplier_mapping_defaults_requires_name_and_price_columns(self):
        from prices.services.import_operations import build_supplier_mapping_defaults

        with self.assertRaisesMessage(
            RuntimeError, "Mapping must include name and price columns."
        ):
            build_supplier_mapping_defaults(
                {
                    "sheet_selector": "",
                    "name_columns": "",
                    "price_column": "",
                }
            )

    def test_latest_active_supplier_mapping_uses_expected_filter_and_order(self):
        from prices.services.import_operations import latest_active_supplier_mapping

        calls = []
        expected_mapping = object()

        class FakeMappingQuerySet:
            def order_by(self, *fields):
                calls.append(("order_by", fields))
                return self

            def first(self):
                calls.append(("first", None))
                return expected_mapping

        class FakeMappingManager:
            def filter(self, **kwargs):
                calls.append(("filter", kwargs))
                return FakeMappingQuerySet()

        supplier = object()

        result = latest_active_supplier_mapping(
            supplier,
            file_kind=models.FileKind.STOCK,
            mapping_manager=FakeMappingManager(),
        )

        self.assertIs(result, expected_mapping)
        self.assertEqual(
            calls,
            [
                (
                    "filter",
                    {
                        "supplier": supplier,
                        "file_kind": models.FileKind.STOCK,
                        "is_active": True,
                    },
                ),
                ("order_by", ("-id",)),
                ("first", None),
            ],
        )

    def test_build_supplier_import_mapping_initial_serializes_saved_mapping(self):
        from prices.services.import_operations import (
            build_supplier_import_mapping_initial,
        )

        mapping = SimpleNamespace(
            sheet_names="Main, Backup",
            sheet_indexes="2, 5",
            header_row=3,
            column_map={
                "sku": 1,
                "name": [2, 4],
                "price": 6,
                "currency": 7,
            },
        )

        self.assertEqual(
            build_supplier_import_mapping_initial(mapping),
            {
                "sheet_selector": "Main, Backup, 2, 5",
                "header_row": 3,
                "sku_column": 1,
                "name_columns": "2,4",
                "price_column": 6,
                "currency_column": 7,
            },
        )

    def test_build_supplier_import_mapping_initial_handles_single_name_column(self):
        from prices.services.import_operations import (
            build_supplier_import_mapping_initial,
        )

        mapping = SimpleNamespace(
            sheet_names="",
            sheet_indexes="",
            header_row=1,
            column_map={"name": 2, "price": 5},
        )

        self.assertEqual(
            build_supplier_import_mapping_initial(mapping),
            {
                "sheet_selector": "",
                "header_row": 1,
                "sku_column": None,
                "name_columns": "2",
                "price_column": 5,
                "currency_column": None,
            },
        )

    def test_build_supplier_import_initial_merges_saved_mapping_with_base_initial(self):
        from prices.services.import_operations import build_supplier_import_initial

        supplier = SimpleNamespace(id=7)
        mapping = SimpleNamespace(
            sheet_names="Main",
            sheet_indexes="2",
            header_row=3,
            column_map={"sku": 1, "name": [2, 4], "price": 6},
        )

        initial = build_supplier_import_initial(
            supplier,
            initial={"supplier": "kept"},
            mapping_func=lambda supplier_obj: mapping,
        )

        self.assertEqual(initial["supplier"], "kept")
        self.assertEqual(initial["sheet_selector"], "Main, 2")
        self.assertEqual(initial["header_row"], 3)
        self.assertEqual(initial["sku_column"], 1)
        self.assertEqual(initial["name_columns"], "2,4")
        self.assertEqual(initial["price_column"], 6)

    def test_build_supplier_import_initial_keeps_base_initial_without_mapping(self):
        from prices.services.import_operations import build_supplier_import_initial

        initial = build_supplier_import_initial(
            SimpleNamespace(id=7),
            initial={"supplier": "kept"},
            mapping_func=lambda supplier_obj: None,
        )

        self.assertEqual(initial, {"supplier": "kept"})

    def test_build_supplier_import_context_assembles_tab_form_and_sources(self):
        from prices.services.import_operations import build_supplier_import_context

        calls = []

        class FakePriceSources:
            def order_by(self, *fields):
                calls.append(("order_by", fields))
                return "ordered-sources"

        class FakeSourceForm:
            def __init__(self, *, initial):
                calls.append(("form", initial))

        supplier = SimpleNamespace(id=7, price_sources=FakePriceSources())

        context = build_supplier_import_context(
            supplier,
            source_raw="bad",
            source_form_class=FakeSourceForm,
        )

        self.assertIs(context["supplier"], supplier)
        self.assertEqual(context["active_import_source"], "email")
        self.assertIsInstance(context["source_form"], FakeSourceForm)
        self.assertEqual(context["price_sources"], "ordered-sources")
        self.assertEqual(
            calls,
            [
                ("form", {"source_type": models.PriceSourceType.FIXED_LINK}),
                ("order_by", ("-is_active", "source_type", "id")),
            ],
        )

    def test_run_supplier_import_form_action_save_mapping_returns_tab_redirect(self):
        from prices.services.import_operations import (
            ImportOperationActionResult,
            SupplierImportMappingSaveResult,
            run_supplier_import_form_action,
        )

        supplier = SimpleNamespace(pk=7, name="Supplier A")
        form = SimpleNamespace(cleaned_data={})
        mapping = SimpleNamespace(id=2)
        action = ImportOperationActionResult(
            "success",
            "Supplier A: mapping saved.",
            redirect_source="file",
        )

        result = run_supplier_import_form_action(
            supplier,
            form,
            action_raw="save_mapping",
            source_raw="bad",
            save_mapping_func=lambda supplier_obj, form_obj: (
                SupplierImportMappingSaveResult(mapping=mapping, action=action)
            ),
        )

        self.assertIs(result.action, action)
        self.assertEqual(
            result.redirect_url,
            "/admin/suppliers/7/import/?source=file#mapping-preview",
        )
        self.assertEqual(result.form_error_field, "")

    def test_run_supplier_import_form_action_reports_missing_upload(self):
        from prices.services.import_operations import (
            ImportOperationActionResult,
            SupplierImportMappingSaveResult,
            run_supplier_import_form_action,
        )

        result = run_supplier_import_form_action(
            SimpleNamespace(pk=7, name="Supplier A"),
            SimpleNamespace(cleaned_data={}),
            action_raw="upload_import",
            source_raw="file",
            save_mapping_func=lambda supplier, form: SupplierImportMappingSaveResult(
                mapping=SimpleNamespace(id=2),
                action=ImportOperationActionResult("success", "saved"),
            ),
            upload_action_func=lambda *args: self.fail("upload should not run"),
        )

        self.assertIsNone(result.action)
        self.assertEqual(result.form_error_field, "file")
        self.assertEqual(
            result.form_error_message,
            "Choose a spreadsheet to upload and import, or use Save mapping.",
        )

    def test_run_supplier_import_form_action_uploads_with_saved_mapping(self):
        from prices.services.import_operations import (
            ImportOperationActionResult,
            SupplierImportMappingSaveResult,
            run_supplier_import_form_action,
        )

        supplier = SimpleNamespace(pk=7, name="Supplier A")
        mapping = SimpleNamespace(id=2)
        upload = SimpleNamespace(name="prices.xlsx")
        calls = []
        upload_result = ImportOperationActionResult(
            "success",
            "Supplier A: prices.xlsx imported.",
        )

        result = run_supplier_import_form_action(
            supplier,
            SimpleNamespace(cleaned_data={"file": upload}),
            action_raw="upload_import",
            source_raw="file",
            save_mapping_func=lambda supplier_obj, form_obj: (
                SupplierImportMappingSaveResult(
                    mapping=mapping,
                    action=ImportOperationActionResult("success", "saved"),
                )
            ),
            upload_action_func=lambda *args: calls.append(args) or upload_result,
        )

        self.assertEqual(calls, [(supplier, mapping, upload)])
        self.assertIs(result.action, upload_result)
        self.assertEqual(result.redirect_url, "")

    def test_save_supplier_import_mapping_action_saves_mapping_and_message(self):
        from prices.services.import_operations import (
            save_supplier_import_mapping_action,
        )

        supplier = SimpleNamespace(name="Supplier A")
        form = SimpleNamespace(id=1)
        mapping = SimpleNamespace(id=2)
        calls = []

        result = save_supplier_import_mapping_action(
            supplier,
            form,
            save_func=lambda form_obj, supplier_obj: calls.append(
                (form_obj, supplier_obj)
            )
            or mapping,
        )

        self.assertEqual(calls, [(form, supplier)])
        self.assertIs(result.mapping, mapping)
        self.assertEqual(result.action.message_level, "success")
        self.assertEqual(result.action.message, "Supplier A: mapping saved.")
        self.assertEqual(result.action.redirect_source, "file")

    def test_upload_content_hash_hashes_upload_chunks_without_database(self):
        from prices.services.import_operations import upload_content_hash

        upload = SimpleNamespace(chunks=lambda: [b"abc", b"123"])

        self.assertEqual(
            upload_content_hash(upload),
            hashlib.sha256(b"abc123").hexdigest(),
        )
        self.assertEqual(upload_content_hash(None), "")

    @patch("prices.services.import_operations.process_supplier_upload")
    def test_price_upload_uses_price_file_kind(self, mock_process_upload):
        from prices.services.import_operations import process_supplier_price_upload

        supplier = SimpleNamespace(id=1)
        mapping = SimpleNamespace(id=2)
        upload = SimpleNamespace(name="prices.xlsx")

        process_supplier_price_upload(supplier, mapping, upload)

        mock_process_upload.assert_called_once_with(
            supplier, mapping, upload, models.FileKind.PRICE
        )

    def test_process_import_wizard_upload_processes_with_active_mapping(self):
        from prices.services.import_operations import process_import_wizard_upload

        supplier = SimpleNamespace(id=1)
        mapping = SimpleNamespace(id=2)
        upload = SimpleNamespace(name="prices.xlsx")
        calls = []

        result = process_import_wizard_upload(
            supplier,
            models.FileKind.PRICE,
            upload,
            mapping_func=lambda supplier_obj, *, file_kind: calls.append(
                ("mapping", supplier_obj, file_kind)
            )
            or mapping,
            process_upload_func=lambda *args: calls.append(("process", args)),
        )

        self.assertIs(result, True)
        self.assertEqual(
            calls,
            [
                ("mapping", supplier, models.FileKind.PRICE),
                ("process", (supplier, mapping, upload, models.FileKind.PRICE)),
            ],
        )

    def test_process_import_wizard_upload_swallows_processing_errors(self):
        from prices.services.import_operations import process_import_wizard_upload

        supplier = SimpleNamespace(id=1)
        mapping = SimpleNamespace(id=2)
        upload = SimpleNamespace(name="prices.xlsx")

        def fail_upload(*args):
            raise RuntimeError("bad file")

        result = process_import_wizard_upload(
            supplier,
            models.FileKind.PRICE,
            upload,
            mapping_func=lambda supplier_obj, *, file_kind: mapping,
            process_upload_func=fail_upload,
        )

        self.assertIs(result, False)

    def test_delete_single_import_batch_delegates_to_delete_func(self):
        from prices.services.import_operations import delete_single_import_batch

        batch = SimpleNamespace(id=7)
        calls = []

        delete_single_import_batch(batch, delete_func=lambda item: calls.append(item))

        self.assertEqual(calls, [batch])

    def test_run_import_delete_action_deletes_and_uses_safe_next_url(self):
        from prices.services.import_operations import run_import_delete_action

        batch = SimpleNamespace(id=7)
        calls = []

        redirect_url = run_import_delete_action(
            batch,
            next_url_raw="/admin/suppliers/overview/?supplier=2",
            host="testserver",
            delete_func=lambda item: calls.append(item),
        )

        self.assertEqual(calls, [batch])
        self.assertEqual(redirect_url, "/admin/suppliers/overview/?supplier=2")

    def test_run_import_delete_action_ignores_unsafe_next_url(self):
        from prices.services.import_operations import run_import_delete_action

        redirect_url = run_import_delete_action(
            SimpleNamespace(id=7),
            next_url_raw="https://evil.example/phish",
            host="testserver",
            delete_func=lambda item: None,
        )

        self.assertEqual(redirect_url, "/admin/suppliers/overview/")

    def test_delete_import_batches_by_ids_skips_empty_selection(self):
        from prices.services.import_operations import delete_import_batches_by_ids

        calls = []
        manager = SimpleNamespace(filter=lambda **kwargs: calls.append(kwargs))

        result = delete_import_batches_by_ids(
            [],
            batch_manager=manager,
            delete_func=lambda item: calls.append(item),
        )

        self.assertEqual(result, 0)
        self.assertEqual(calls, [])

    def test_delete_import_batches_by_ids_deletes_matching_batches(self):
        from prices.services.import_operations import delete_import_batches_by_ids

        batch_one = SimpleNamespace(id=1)
        batch_two = SimpleNamespace(id=2)
        calls = []

        class BatchManager:
            def filter(self, **kwargs):
                calls.append(("filter", kwargs))
                return [batch_one, batch_two]

        result = delete_import_batches_by_ids(
            ["1", "2"],
            batch_manager=BatchManager(),
            delete_func=lambda batch: calls.append(("delete", batch.id)),
        )

        self.assertEqual(result, 2)
        self.assertEqual(
            calls,
            [
                ("filter", {"id__in": ["1", "2"]}),
                ("delete", 1),
                ("delete", 2),
            ],
        )

    def test_run_import_delete_bulk_action_deletes_and_redirects_to_overview(self):
        from prices.services.import_operations import run_import_delete_bulk_action

        calls = []

        redirect_url = run_import_delete_bulk_action(
            ["1", "2"],
            delete_func=lambda import_ids: calls.append(list(import_ids)),
        )

        self.assertEqual(calls, [["1", "2"]])
        self.assertEqual(redirect_url, "/admin/suppliers/overview/")

    def test_run_supplier_price_upload_action_uploads_file(self):
        from prices.services.import_operations import run_supplier_price_upload_action

        supplier = SimpleNamespace(name="Supplier A")
        mapping = SimpleNamespace(id=2)
        upload = SimpleNamespace(name="prices.xlsx")
        calls = []

        result = run_supplier_price_upload_action(
            supplier,
            mapping,
            upload,
            process_upload_func=lambda *args: calls.append(args),
        )

        self.assertEqual(calls, [(supplier, mapping, upload)])
        self.assertEqual(result.message_level, "success")
        self.assertEqual(result.message, "Supplier A: prices.xlsx imported.")
        self.assertEqual(result.redirect_source, "")

    def test_run_supplier_price_upload_action_reports_upload_failure(self):
        from prices.services.import_operations import run_supplier_price_upload_action

        supplier = SimpleNamespace(name="Supplier A")
        mapping = SimpleNamespace(id=2)
        upload = SimpleNamespace(name="prices.xlsx")

        def fail_upload(supplier_obj, mapping_obj, upload_obj):
            raise RuntimeError("bad file")

        result = run_supplier_price_upload_action(
            supplier,
            mapping,
            upload,
            process_upload_func=fail_upload,
        )

        self.assertEqual(result.message_level, "error")
        self.assertEqual(result.message, "Supplier A: upload failed. bad file")
        self.assertEqual(result.redirect_source, "")

    def test_run_supplier_quick_upload_action_requires_mapping(self):
        from prices.services.import_operations import run_supplier_quick_upload_action

        supplier = SimpleNamespace(name="Supplier A")
        upload = SimpleNamespace(name="prices.xlsx")

        result = run_supplier_quick_upload_action(
            supplier,
            upload,
            mapping_func=lambda supplier_obj: None,
        )

        self.assertEqual(result.message_level, "info")
        self.assertEqual(
            result.message,
            "Create or confirm the supplier price mapping first.",
        )
        self.assertEqual(result.redirect_source, "file")

    def test_run_supplier_quick_upload_action_requires_file(self):
        from prices.services.import_operations import run_supplier_quick_upload_action

        supplier = SimpleNamespace(name="Supplier A")
        mapping = SimpleNamespace(id=2)

        result = run_supplier_quick_upload_action(
            supplier,
            None,
            mapping_func=lambda supplier_obj: mapping,
        )

        self.assertEqual(result.message_level, "info")
        self.assertEqual(result.message, "Select a file to upload.")
        self.assertEqual(result.redirect_source, "")

    def test_run_supplier_quick_upload_action_reports_upload_failure(self):
        from prices.services.import_operations import run_supplier_quick_upload_action

        supplier = SimpleNamespace(name="Supplier A")
        mapping = SimpleNamespace(id=2)
        upload = SimpleNamespace(name="prices.xlsx")

        def fail_upload(supplier_obj, mapping_obj, upload_obj):
            raise RuntimeError("bad file")

        result = run_supplier_quick_upload_action(
            supplier,
            upload,
            mapping_func=lambda supplier_obj: mapping,
            process_upload_func=fail_upload,
        )

        self.assertEqual(result.message_level, "error")
        self.assertEqual(result.message, "Supplier A: upload failed. bad file")
        self.assertEqual(result.redirect_source, "")

    def test_run_supplier_quick_upload_action_uploads_file(self):
        from prices.services.import_operations import run_supplier_quick_upload_action

        supplier = SimpleNamespace(name="Supplier A")
        mapping = SimpleNamespace(id=2)
        upload = SimpleNamespace(name="prices.xlsx")
        calls = []

        result = run_supplier_quick_upload_action(
            supplier,
            upload,
            mapping_func=lambda supplier_obj: mapping,
            process_upload_func=lambda *args: calls.append(args),
        )

        self.assertEqual(calls, [(supplier, mapping, upload)])
        self.assertEqual(result.message_level, "success")
        self.assertEqual(result.message, "Supplier A: prices.xlsx imported.")
        self.assertEqual(result.redirect_source, "")

    def test_enqueue_bulk_price_reimport_dispatches_repair_command(self):
        from prices.services.import_operations import enqueue_bulk_price_reimport

        calls = []

        def fake_enqueue(command_name, *args, **options):
            calls.append((command_name, args, options))
            return "queued"

        result = enqueue_bulk_price_reimport(enqueue_func=fake_enqueue)

        self.assertEqual(result, "queued")
        self.assertEqual(
            calls,
            [
                (
                    "repair_supplier_price_imports",
                    (),
                    {
                        "all_suppliers": True,
                        "description": "Bulk price reimport",
                    },
                )
            ],
        )

    def test_run_bulk_price_reimport_action_enqueues_reimport(self):
        from prices.services.import_operations import run_bulk_price_reimport_action

        calls = []

        result = run_bulk_price_reimport_action(
            enqueue_func=lambda **kwargs: calls.append(kwargs),
        )

        self.assertEqual(calls, [{"description": "Bulk price reimport"}])
        self.assertEqual(result.message_level, "success")
        self.assertEqual(
            result.message,
            "Reimport of all processed price files started in background.",
        )

    def test_run_bulk_price_reimport_action_reports_enqueue_failure(self):
        from prices.services.import_operations import run_bulk_price_reimport_action

        def fail_enqueue(**kwargs):
            raise RuntimeError("queue down")

        result = run_bulk_price_reimport_action(enqueue_func=fail_enqueue)

        self.assertEqual(result.message_level, "error")
        self.assertEqual(result.message, "Failed to start price reimport: queue down")

    def test_run_supplier_price_source_import_action_requires_mapping(self):
        from prices.services.import_operations import (
            run_supplier_price_source_import_action,
        )

        supplier = SimpleNamespace(name="Supplier A")
        source = SimpleNamespace(id=1)

        result = run_supplier_price_source_import_action(
            supplier,
            source,
            mapping_func=lambda supplier_obj: None,
        )

        self.assertEqual(result.message_level, "info")
        self.assertEqual(
            result.message,
            "Create or confirm the supplier price mapping first.",
        )
        self.assertEqual(result.redirect_source, "file")

    def test_run_supplier_price_source_import_action_reports_import_failure(self):
        from prices.services.import_operations import (
            run_supplier_price_source_import_action,
        )

        supplier = SimpleNamespace(name="Supplier A")
        source = SimpleNamespace(id=1)
        mapping = SimpleNamespace(id=2)

        def fail_import(**kwargs):
            raise RuntimeError("download failed")

        result = run_supplier_price_source_import_action(
            supplier,
            source,
            mapping_func=lambda supplier_obj: mapping,
            import_func=fail_import,
        )

        self.assertEqual(result.message_level, "error")
        self.assertEqual(
            result.message,
            "Supplier A: link import failed. download failed",
        )
        self.assertEqual(result.redirect_source, "link")

    def test_run_supplier_price_source_import_action_reports_duplicate(self):
        from prices.services.import_operations import (
            run_supplier_price_source_import_action,
        )

        supplier = SimpleNamespace(name="Supplier A")
        source = SimpleNamespace(id=1)
        mapping = SimpleNamespace(id=2)

        result = run_supplier_price_source_import_action(
            supplier,
            source,
            mapping_func=lambda supplier_obj: mapping,
            import_func=lambda **kwargs: {
                "status": "duplicate",
                "filename": "prices.xlsx",
            },
        )

        self.assertEqual(result.message_level, "info")
        self.assertEqual(
            result.message,
            "Supplier A: no change, duplicate file prices.xlsx.",
        )
        self.assertEqual(result.redirect_source, "link")

    def test_run_supplier_price_source_import_action_reports_success(self):
        from prices.services.import_operations import (
            run_supplier_price_source_import_action,
        )

        supplier = SimpleNamespace(name="Supplier A")
        source = SimpleNamespace(id=1)
        mapping = SimpleNamespace(id=2)
        calls = []

        def fake_import(**kwargs):
            calls.append(kwargs)
            return {"status": "imported", "filename": "prices.xlsx"}

        result = run_supplier_price_source_import_action(
            supplier,
            source,
            mapping_func=lambda supplier_obj: mapping,
            import_func=fake_import,
        )

        self.assertEqual(
            calls,
            [{"supplier": supplier, "source": source, "mapping": mapping}],
        )
        self.assertEqual(result.message_level, "success")
        self.assertEqual(
            result.message,
            "Supplier A: imported prices.xlsx from link.",
        )
        self.assertEqual(result.redirect_source, "link")

    def test_run_supplier_price_source_create_action_reports_invalid_form(self):
        from prices.services.import_operations import (
            run_supplier_price_source_create_action,
        )

        supplier = SimpleNamespace(id=1)
        form = SimpleNamespace(is_valid=lambda: False)

        result = run_supplier_price_source_create_action(supplier, form)

        self.assertEqual(result.message_level, "error")
        self.assertEqual(
            result.message,
            "Link source was not saved. Check the highlighted fields.",
        )
        self.assertEqual(result.redirect_source, "link")

    def test_run_supplier_price_source_create_action_saves_source(self):
        from prices.services.import_operations import (
            run_supplier_price_source_create_action,
        )

        supplier = SimpleNamespace(id=1)

        class Source(SimpleNamespace):
            def save(self):
                self.saved = True

        source = Source(saved=False, supplier=None)
        calls = []
        form = SimpleNamespace(
            is_valid=lambda: True,
            save=lambda **kwargs: calls.append(kwargs) or source,
        )

        result = run_supplier_price_source_create_action(supplier, form)

        self.assertEqual(calls, [{"commit": False}])
        self.assertIs(source.supplier, supplier)
        self.assertIs(source.saved, True)
        self.assertEqual(result.message_level, "success")
        self.assertEqual(result.message, "Price link source saved.")
        self.assertEqual(result.redirect_source, "link")

    def test_run_supplier_price_source_delete_action_deletes_source(self):
        from prices.services.import_operations import (
            run_supplier_price_source_delete_action,
        )

        calls = []
        source = SimpleNamespace(delete=lambda: calls.append("delete"))

        result = run_supplier_price_source_delete_action(source)

        self.assertEqual(calls, ["delete"])
        self.assertEqual(result.message_level, "success")
        self.assertEqual(result.message, "Price link source deleted.")
        self.assertEqual(result.redirect_source, "link")

    def test_import_supplier_price_source_updates_source_after_success(self):
        from prices.services.import_operations import import_supplier_price_source

        checked_at = object()
        process_calls = []

        class Source(SimpleNamespace):
            def __init__(self):
                super().__init__(save_calls=[])

            def get_source_type_display(self):
                return "Fixed link"

            def save(self, **kwargs):
                self.save_calls.append(kwargs)

        source = Source()
        downloaded = SimpleNamespace(
            filename="prices.xlsx",
            payload=b"payload",
            content_type="application/vnd.ms-excel",
            provider="Yandex Disk",
            source_url="https://disk.yandex.ru/d/example",
        )

        def process_payload_func(**kwargs):
            process_calls.append(kwargs)
            return {
                "status": "imported",
                "message": "Imported successfully.",
                "filename": "prices.xlsx",
            }

        result = import_supplier_price_source(
            supplier=SimpleNamespace(name="Supplier"),
            source=source,
            mapping=SimpleNamespace(id=1),
            download_func=lambda source_obj: downloaded,
            process_payload_func=process_payload_func,
            now_func=lambda: checked_at,
        )

        self.assertEqual(result["status"], "imported")
        self.assertEqual(source.last_checked_at, checked_at)
        self.assertEqual(source.last_status, "imported")
        self.assertEqual(source.last_message, "Imported successfully.")
        self.assertEqual(source.last_filename, "prices.xlsx")
        self.assertEqual(
            source.save_calls,
            [
                {
                    "update_fields": [
                        "last_checked_at",
                        "last_status",
                        "last_message",
                        "last_filename",
                    ]
                }
            ],
        )
        self.assertEqual(process_calls[0]["filename"], "prices.xlsx")
        self.assertEqual(process_calls[0]["payload"], b"payload")
        self.assertEqual(process_calls[0]["content_type"], "application/vnd.ms-excel")
        self.assertEqual(process_calls[0]["source_label"], "Fixed link / Yandex Disk")
        self.assertEqual(
            process_calls[0]["source_url"],
            "https://disk.yandex.ru/d/example",
        )

    def test_import_supplier_price_source_marks_source_failed_after_error(self):
        from prices.services.import_operations import import_supplier_price_source

        checked_at = object()

        class Source(SimpleNamespace):
            def __init__(self):
                super().__init__(save_calls=[])

            def get_source_type_display(self):
                return "Fixed link"

            def save(self, **kwargs):
                self.save_calls.append(kwargs)

        source = Source()

        def failing_download(_source):
            raise RuntimeError("download failed")

        with self.assertRaisesMessage(RuntimeError, "download failed"):
            import_supplier_price_source(
                supplier=SimpleNamespace(name="Supplier"),
                source=source,
                mapping=SimpleNamespace(id=1),
                download_func=failing_download,
                now_func=lambda: checked_at,
            )

        self.assertEqual(source.last_checked_at, checked_at)
        self.assertEqual(source.last_status, "failed")
        self.assertEqual(source.last_message, "download failed")
        self.assertEqual(
            source.save_calls,
            [
                {
                    "update_fields": [
                        "last_checked_at",
                        "last_status",
                        "last_message",
                    ]
                }
            ],
        )

    @patch(
        "prices.services.import_operations.models.SupplierFileMapping.objects.update_or_create"
    )
    def test_save_supplier_mapping_from_import_form_persists_active_price_mapping(
        self,
        mock_update_or_create,
    ):
        from prices.services.import_operations import (
            save_supplier_mapping_from_import_form,
        )

        supplier = SimpleNamespace(id=1)
        form = SimpleNamespace(
            cleaned_data={
                "sheet_selector": "Main, 2",
                "name_columns": "3,4",
                "sku_column": 1,
                "price_column": 5,
                "currency_column": "",
                "header_row": None,
            }
        )
        mapping = SimpleNamespace(id=9)
        mock_update_or_create.return_value = (mapping, True)

        result = save_supplier_mapping_from_import_form(form, supplier)

        self.assertIs(result, mapping)
        mock_update_or_create.assert_called_once_with(
            supplier=supplier,
            file_kind=models.FileKind.PRICE,
            is_active=True,
            defaults={
                "mapping_mode": models.MappingMode.INDEX,
                "sheet_names": "Main",
                "sheet_indexes": "2",
                "header_row": 1,
                "column_map": {
                    "sku": 1,
                    "name": [3, 4],
                    "price": 5,
                    "currency": 0,
                },
            },
        )

    @patch("prices.services.import_operations.process_import_file")
    @patch("prices.services.import_operations.models.ImportFile.objects.create")
    @patch("prices.services.import_operations.models.ImportBatch.objects.create")
    def test_process_supplier_upload_marks_batch_processed_after_success(
        self,
        mock_batch_create,
        mock_file_create,
        mock_process_import_file,
    ):
        from prices.services.import_operations import process_supplier_upload

        class SavedObject(SimpleNamespace):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.save_calls = []

            def save(self, **kwargs):
                self.save_calls.append(kwargs)

        supplier = SimpleNamespace(id=1)
        mapping = SimpleNamespace(id=2)
        upload = SimpleNamespace(name="price.xlsx", chunks=lambda: [b"abc"])
        batch = SavedObject()
        import_file = SavedObject()
        mock_batch_create.return_value = batch
        mock_file_create.return_value = import_file

        result = process_supplier_upload(
            supplier,
            mapping,
            upload,
            models.FileKind.PRICE,
        )

        self.assertIs(result, batch)
        mock_process_import_file.assert_called_once_with(import_file)
        self.assertEqual(batch.status, models.ImportStatus.PROCESSED)
        self.assertEqual(batch.save_calls, [{"update_fields": ["status"]}])
        mock_file_create.assert_called_once()
        self.assertEqual(
            mock_file_create.call_args.kwargs["content_hash"],
            hashlib.sha256(b"abc").hexdigest(),
        )

    @patch(
        "prices.services.import_operations.process_import_file",
        side_effect=RuntimeError("bad file"),
    )
    @patch("prices.services.import_operations.models.ImportFile.objects.create")
    @patch("prices.services.import_operations.models.ImportBatch.objects.create")
    def test_process_supplier_upload_marks_file_and_batch_failed_after_error(
        self,
        mock_batch_create,
        mock_file_create,
        _mock_process_import_file,
    ):
        from prices.services.import_operations import process_supplier_upload

        class SavedObject(SimpleNamespace):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.save_calls = []

            def save(self, **kwargs):
                self.save_calls.append(kwargs)

        supplier = SimpleNamespace(id=1)
        mapping = SimpleNamespace(id=2)
        upload = SimpleNamespace(name="price.xlsx", chunks=lambda: [b"abc"])
        batch = SavedObject()
        import_file = SavedObject()
        mock_batch_create.return_value = batch
        mock_file_create.return_value = import_file

        with self.assertRaisesMessage(RuntimeError, "bad file"):
            process_supplier_upload(
                supplier,
                mapping,
                upload,
                models.FileKind.PRICE,
            )

        self.assertEqual(import_file.status, models.ImportStatus.FAILED)
        self.assertEqual(import_file.error_message, "bad file")
        self.assertEqual(
            import_file.save_calls,
            [{"update_fields": ["status", "error_message"]}],
        )
        self.assertEqual(batch.status, models.ImportStatus.FAILED)
        self.assertEqual(batch.error_message, "bad file")
        self.assertEqual(
            batch.save_calls,
            [{"update_fields": ["status", "error_message"]}],
        )


class ProductLinkingServiceTests(SimpleTestCase):
    def test_split_link_search_terms_splits_spaces_and_commas(self):
        from prices.services.product_linking import split_link_search_terms

        self.assertEqual(
            split_link_search_terms("dior, sauvage 100ml"), ["dior", "sauvage", "100ml"]
        )

    def test_product_linking_list_context_applies_filters_and_pagination(self):
        from prices.services.product_linking import build_product_linking_list_context

        class FakeQuerySet:
            def __init__(self):
                self.calls = []

            def select_related(self, *fields):
                self.calls.append(("select_related", fields, {}))
                return self

            def filter(self, *args, **kwargs):
                self.calls.append(("filter", args, kwargs))
                return self

            def exclude(self, *args, **kwargs):
                self.calls.append(("exclude", args, kwargs))
                return self

            def order_by(self, *fields):
                self.calls.append(("order_by", fields, {}))
                return self

        paginator_calls = []

        class FakePaginator:
            def __init__(self, queryset, page_size):
                paginator_calls.append(("init", queryset, page_size))

            def get_page(self, page_number):
                paginator_calls.append(("page", page_number))
                return f"page-{page_number}"

        supplier_products = FakeQuerySet()
        supplier_options = object()
        supplier_order_calls = []
        supplier_manager = SimpleNamespace(
            order_by=lambda *fields: supplier_order_calls.append(fields)
            or supplier_options
        )
        request = RequestFactory().get(
            "/admin/product-linking/",
            {
                "supplier": "3,4",
                "q": "oud",
                "exclude": "sample",
                "sp_page": "2",
            },
        )
        request.user = SimpleNamespace(is_authenticated=False)

        result = build_product_linking_list_context(
            request,
            supplier_product_manager=supplier_products,
            supplier_manager=supplier_manager,
            paginator_class=FakePaginator,
        )

        self.assertEqual(result["supplier_products"], "page-2")
        self.assertEqual(result["supplier_filter"], "3,4")
        self.assertEqual(result["search_query"], "oud")
        self.assertIs(result["supplier_options"], supplier_options)
        self.assertIn(("select_related", ("supplier",), {}), supplier_products.calls)
        self.assertIn(
            ("filter", (), {"supplier_id__in": [3, 4]}), supplier_products.calls
        )
        self.assertEqual(supplier_products.calls[-1], ("order_by", ("name",), {}))
        self.assertEqual(
            [call[0] for call in supplier_products.calls],
            ["select_related", "exclude", "filter", "filter", "order_by"],
        )
        self.assertEqual(
            paginator_calls,
            [("init", supplier_products, 50), ("page", "2")],
        )
        self.assertEqual(supplier_order_calls, [("name",)])

    def test_product_linking_candidate_payload_builds_filtered_candidate_lists(self):
        from prices.services.product_linking import (
            build_product_linking_candidate_payload,
        )

        class FakeQuerySet:
            def __init__(self, label):
                self.label = label
                self.calls = []

            def select_related(self, *fields):
                self.calls.append(("select_related", fields, {}))
                return self

            def exclude(self, *args, **kwargs):
                self.calls.append(("exclude", args, kwargs))
                return self

            def filter(self, *args, **kwargs):
                self.calls.append(("filter", args, kwargs))
                return self

            def order_by(self, *fields):
                self.calls.append(("order_by", fields, {}))
                return self

            def __getitem__(self, key):
                self.calls.append(("slice", key.start, key.stop, key.step))
                return f"{self.label}-slice-{key.stop}"

        our_queryset = FakeQuerySet("our")
        supplier_queryset = FakeQuerySet("supplier")
        our_manager_calls = []
        our_manager = SimpleNamespace(
            all=lambda: our_manager_calls.append("all") or our_queryset
        )
        request = RequestFactory().get(
            "/admin/product-linking/search/",
            {"exclude": "sample"},
        )
        request.user = SimpleNamespace(is_authenticated=False)
        source = SimpleNamespace(
            id=11,
            name="Dior Sauvage 100 ml",
            brand="Dior",
            size="100 ml",
            supplier_id=7,
        )

        result = build_product_linking_candidate_payload(
            request,
            source,
            "dior sauvage",
            our_product_manager=our_manager,
            supplier_product_manager=supplier_queryset,
            our_serializer=lambda _source, candidates: {"candidates": candidates},
            supplier_serializer=lambda _source, candidates: {"candidates": candidates},
            candidate_query_limit=25,
        )

        self.assertEqual(result["source"]["id"], 11)
        self.assertEqual(result["our_products"]["candidates"], "our-slice-25")
        self.assertEqual(result["supplier_products"]["candidates"], "supplier-slice-25")
        self.assertEqual(our_manager_calls, ["all"])
        self.assertEqual(
            [call[0] for call in our_queryset.calls],
            ["filter", "filter", "order_by", "slice"],
        )
        self.assertEqual(
            [call[0] for call in supplier_queryset.calls],
            [
                "select_related",
                "exclude",
                "exclude",
                "filter",
                "filter",
                "order_by",
                "slice",
            ],
        )
        self.assertEqual(
            supplier_queryset.calls[1],
            ("exclude", (), {"supplier_id": 7}),
        )

    def test_resolve_product_linking_source_validates_and_finds_source(self):
        from prices.services.product_linking import resolve_product_linking_source

        class FakeManager:
            def __init__(self, supplier_product):
                self.supplier_product = supplier_product
                self.calls = []

            def select_related(self, *fields):
                self.calls.append(("select_related", fields))
                return self

            def filter(self, **kwargs):
                self.calls.append(("filter", kwargs))
                return self

            def first(self):
                self.calls.append(("first",))
                return self.supplier_product

        invalid_manager = FakeManager(SimpleNamespace(id=99))

        invalid = resolve_product_linking_source(
            "abc",
            supplier_product_manager=invalid_manager,
        )

        self.assertIsNone(invalid.supplier_product)
        self.assertEqual(invalid.error, "Invalid supplier product.")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid_manager.calls, [])

        not_found_manager = FakeManager(None)
        not_found = resolve_product_linking_source(
            "12",
            supplier_product_manager=not_found_manager,
        )

        self.assertIsNone(not_found.supplier_product)
        self.assertEqual(not_found.error, "Supplier product not found.")
        self.assertEqual(not_found.status_code, 404)
        self.assertEqual(
            not_found_manager.calls,
            [("select_related", ("supplier",)), ("filter", {"id": 12}), ("first",)],
        )

        source = SimpleNamespace(id=15)
        found = resolve_product_linking_source(
            "15",
            supplier_product_manager=FakeManager(source),
        )

        self.assertIs(found.supplier_product, source)
        self.assertEqual(found.error, "")
        self.assertEqual(found.status_code, 200)

    def test_product_linking_search_payload_handles_errors_and_auto_terms(self):
        from prices.services.product_linking import (
            ProductLinkingSourceResolution,
            build_product_linking_search_payload,
        )

        request = RequestFactory().get(
            "/admin/linking/search/",
            {"supplier_product": "7", "auto": "1"},
        )
        source = SimpleNamespace(id=7, name="Dior Sauvage")
        calls = []

        def source_resolver(raw_supplier_product_id):
            calls.append(("resolve", raw_supplier_product_id))
            return ProductLinkingSourceResolution(source, "", 200)

        def candidate_builder(request_arg, supplier_product, terms):
            calls.append(("candidates", request_arg, supplier_product, terms))
            return {"items": [{"id": 1}]}

        result = build_product_linking_search_payload(
            request,
            source_resolver=source_resolver,
            candidate_payload_builder=candidate_builder,
        )

        self.assertEqual(result.payload, {"items": [{"id": 1}]})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(
            calls,
            [
                ("resolve", "7"),
                ("candidates", request, source, "Dior Sauvage"),
            ],
        )

        error_result = build_product_linking_search_payload(
            RequestFactory().get(
                "/admin/linking/search/",
                {"supplier_product": "bad", "terms": "oud"},
            ),
            source_resolver=lambda _raw_id: ProductLinkingSourceResolution(
                None,
                "Invalid supplier product.",
                400,
            ),
            candidate_payload_builder=candidate_builder,
        )

        self.assertEqual(error_result.payload, {"error": "Invalid supplier product."})
        self.assertEqual(error_result.status_code, 400)

    def test_extract_link_size_normalizes_latin_and_cyrillic_ml(self):
        from prices.services.product_linking import extract_link_size

        self.assertEqual(extract_link_size("100 ml"), "100ml")
        self.assertEqual(extract_link_size("100 \u043c\u043b"), "100ml")
        self.assertEqual(extract_link_size("7,5ml"), "7.5ml")

    def test_score_link_candidate_rewards_brand_and_size_matches(self):
        from prices.services.product_linking import score_link_candidate

        score, reason = score_link_candidate(
            "Dior Sauvage 100 \u043c\u043b",
            "Dior",
            "",
            "Sauvage",
            "Dior",
            "100 ml",
        )

        self.assertGreater(score, 0.4)
        self.assertIn("brand exact", reason)
        self.assertIn("size exact", reason)

    def test_score_link_candidate_handles_empty_tokens(self):
        from prices.services.product_linking import score_link_candidate

        self.assertEqual(
            score_link_candidate("", "", "", "Sauvage", "Dior", "100 ml"),
            (0.0, "no tokens"),
        )

    def test_link_candidate_serializers_sort_and_limit_results(self):
        from prices.services.product_linking import (
            serialize_our_product_link_candidates,
            serialize_supplier_product_link_candidates,
        )

        source = SimpleNamespace(
            name="Dior Sauvage 100 ml",
            brand="Dior",
            size="100 ml",
        )
        strong = SimpleNamespace(
            id=1,
            name="Dior Sauvage 100 ml",
            brand="Dior",
            size="100 ml",
        )
        weak = SimpleNamespace(
            id=2,
            name="Sauvage sample",
            brand="",
            size="",
        )
        no_match = SimpleNamespace(
            id=3,
            name="Bleu de Chanel",
            brand="Chanel",
            size="50 ml",
        )

        our_items = serialize_our_product_link_candidates(
            source,
            [weak, no_match, strong],
            limit=2,
        )

        self.assertEqual([item["id"] for item in our_items], [1, 2])
        self.assertGreater(our_items[0]["score"], our_items[1]["score"])
        self.assertIn("brand exact", our_items[0]["reason"])

        supplier_items = serialize_supplier_product_link_candidates(
            source,
            [
                SimpleNamespace(
                    id=4,
                    name=strong.name,
                    brand=strong.brand,
                    size=strong.size,
                    supplier=SimpleNamespace(name="Supplier A"),
                    supplier_sku="SKU-4",
                    our_product_id=9,
                )
            ],
        )

        self.assertEqual(supplier_items[0]["supplier"], "Supplier A")
        self.assertEqual(supplier_items[0]["sku"], "SKU-4")
        self.assertEqual(supplier_items[0]["our_product_id"], 9)

    def test_link_supplier_product_to_our_product_saves_source_link(self):
        from prices.services.product_linking import link_supplier_product_to_our_product

        source = SimpleNamespace(our_product=None, save_calls=[])
        source.save = lambda **kwargs: source.save_calls.append(kwargs)
        our_product = SimpleNamespace(id=7)

        link_supplier_product_to_our_product(source, our_product)

        self.assertEqual(source.our_product, our_product)
        self.assertEqual(source.save_calls, [{"update_fields": ["our_product"]}])

    def test_link_supplier_product_to_supplier_product_reuses_existing_our_product(
        self,
    ):
        from prices.services.product_linking import (
            link_supplier_product_to_supplier_product,
        )

        existing_our = SimpleNamespace(id=7)
        source = SimpleNamespace(our_product=None, save_calls=[])
        target = SimpleNamespace(our_product=existing_our, save_calls=[])
        source.save = lambda **kwargs: source.save_calls.append(kwargs)
        target.save = lambda **kwargs: target.save_calls.append(kwargs)

        link_supplier_product_to_supplier_product(source, target)

        self.assertEqual(source.our_product, existing_our)
        self.assertEqual(source.save_calls, [{"update_fields": ["our_product"]}])
        self.assertEqual(target.save_calls, [])

    def test_link_supplier_product_to_supplier_product_creates_legacy_our_product(self):
        from prices.services.product_linking import (
            link_supplier_product_to_supplier_product,
        )

        created = SimpleNamespace(id=9)
        create_calls = []
        source = SimpleNamespace(our_product=None, save_calls=[])
        target = SimpleNamespace(
            name="Dior Sauvage",
            brand="Dior",
            size="100 ml",
            our_product=None,
            save_calls=[],
        )
        source.save = lambda **kwargs: source.save_calls.append(kwargs)
        target.save = lambda **kwargs: target.save_calls.append(kwargs)
        manager = SimpleNamespace(
            create=lambda **kwargs: create_calls.append(kwargs) or created
        )

        link_supplier_product_to_supplier_product(
            source,
            target,
            our_product_manager=manager,
        )

        self.assertEqual(
            create_calls,
            [{"name": "Dior Sauvage", "brand": "Dior", "size": "100 ml"}],
        )
        self.assertEqual(source.our_product, created)
        self.assertEqual(target.our_product, created)
        self.assertEqual(target.save_calls, [{"update_fields": ["our_product"]}])
        self.assertEqual(source.save_calls, [{"update_fields": ["our_product"]}])

    def test_run_product_linking_apply_action_links_to_our_product_first(self):
        from prices.services.product_linking import run_product_linking_apply_action

        source = SimpleNamespace(id=1)
        our_product = SimpleNamespace(id=2)
        supplier_target = SimpleNamespace(id=3)
        calls = []

        def supplier_getter(product_id):
            calls.append(("supplier_get", product_id))
            return {1: source, 3: supplier_target}[product_id]

        def our_getter(product_id):
            calls.append(("our_get", product_id))
            return {2: our_product}[product_id]

        def link_our(source_arg, our_product_arg):
            calls.append(("link_our", source_arg, our_product_arg))

        def link_supplier(source_arg, supplier_arg):
            calls.append(("link_supplier", source_arg, supplier_arg))

        redirect_url = run_product_linking_apply_action(
            {
                "source_id": "1",
                "target_our": "2",
                "target_supplier": "3",
            },
            supplier_product_getter=supplier_getter,
            our_product_getter=our_getter,
            link_our_func=link_our,
            link_supplier_func=link_supplier,
        )

        self.assertEqual(redirect_url, reverse("prices:product_linking"))
        self.assertEqual(
            calls,
            [
                ("supplier_get", 1),
                ("our_get", 2),
                ("link_our", source, our_product),
            ],
        )

    def test_run_product_linking_apply_action_links_to_supplier_product(self):
        from prices.services.product_linking import run_product_linking_apply_action

        source = SimpleNamespace(id=1)
        target = SimpleNamespace(id=3)
        calls = []

        def supplier_getter(product_id):
            calls.append(("supplier_get", product_id))
            return {1: source, 3: target}[product_id]

        def link_supplier(source_arg, target_arg):
            calls.append(("link_supplier", source_arg, target_arg))

        redirect_url = run_product_linking_apply_action(
            {
                "source_id": "1",
                "target_our": "",
                "target_supplier": "3",
            },
            supplier_product_getter=supplier_getter,
            our_product_getter=lambda product_id: None,
            link_supplier_func=link_supplier,
        )

        self.assertEqual(redirect_url, reverse("prices:product_linking"))
        self.assertEqual(
            calls,
            [
                ("supplier_get", 1),
                ("supplier_get", 3),
                ("link_supplier", source, target),
            ],
        )

    def test_run_product_linking_apply_action_redirects_on_invalid_ids(self):
        from prices.services.product_linking import run_product_linking_apply_action

        calls = []
        redirect_url = run_product_linking_apply_action(
            {"source_id": "bad", "target_our": "2"},
            supplier_product_getter=lambda product_id: calls.append(product_id),
        )

        self.assertEqual(redirect_url, reverse("prices:product_linking"))
        self.assertEqual(calls, [])

        source = SimpleNamespace(id=1)
        redirect_url = run_product_linking_apply_action(
            {"source_id": "1", "target_our": "bad", "target_supplier": "3"},
            supplier_product_getter=lambda product_id: source,
            our_product_getter=lambda product_id: calls.append(("our", product_id)),
        )

        self.assertEqual(redirect_url, reverse("prices:product_linking"))
        self.assertEqual(calls, [])


class CatalogReviewServiceTests(SimpleTestCase):
    def test_catalog_search_tokens_only_returns_multi_word_queries(self):
        from prices.services.catalog_review import catalog_search_tokens

        self.assertEqual(catalog_search_tokens("Vanilla Extasy"), ["Vanilla", "Extasy"])
        self.assertEqual(catalog_search_tokens("Vanilla"), [])

    def test_fragrantica_identity_matching_folds_latin_diacritics(self):
        from prices.services.catalog_review import (
            fragrantica_identity_key,
            normalize_catalogue_perfume_name,
            normalized_fragrance_key,
        )

        self.assertEqual(
            normalized_fragrance_key("Donna Nòbile Ìle Été"),
            normalized_fragrance_key("Donna Nobile Ile Ete"),
        )
        self.assertEqual(
            normalize_catalogue_perfume_name("Donna Nòbile Ìle Été"),
            "Donna Nobile Ile Ete",
        )
        self.assertEqual(
            normalized_fragrance_key("L’air Barbès"),
            normalized_fragrance_key("L air Barbes"),
        )
        self.assertEqual(
            normalize_catalogue_perfume_name("L’air Barbès"),
            "L'air Barbes",
        )
        self.assertEqual(
            normalize_catalogue_perfume_name("L´air Barbès"),
            "L'air Barbes",
        )
        self.assertEqual(
            fragrantica_identity_key("19-69", "L´air Barbès"),
            fragrantica_identity_key("19-69", "L'air Barbes"),
        )

    def test_build_our_product_catalog_variant_queryset_applies_search_policy(self):
        from prices.services.catalog_review import (
            build_our_product_catalog_variant_queryset,
        )

        class FakeVariantQuerySet:
            def __init__(self):
                self.calls = []

            def select_related(self, *fields):
                self.calls.append(("select_related", fields, {}))
                return self

            def filter(self, *args, **kwargs):
                self.calls.append(("filter", args, kwargs))
                return self

            def order_by(self, *fields):
                self.calls.append(("order_by", fields, {}))
                return self

        queryset = FakeVariantQuerySet()

        result = build_our_product_catalog_variant_queryset(
            "blond amber",
            variant_manager=queryset,
        )

        self.assertIs(result, queryset)
        self.assertEqual(
            [call[0] for call in queryset.calls],
            ["select_related", "filter", "order_by"],
        )
        self.assertEqual(
            queryset.calls[0],
            ("select_related", ("perfume", "perfume__brand"), {}),
        )
        self.assertEqual(
            queryset.calls[-1],
            (
                "order_by",
                (
                    "perfume__brand__name",
                    "perfume__name",
                    "perfume__concentration",
                    "size_ml",
                    "packaging",
                ),
                {},
            ),
        )

    def test_build_our_product_catalog_list_context_normalizes_tabs_and_rows(self):
        from prices.services.catalog_review import (
            build_our_product_catalog_list_context,
        )

        class FakeQuerySet:
            def __init__(self, label):
                self.label = label
                self.calls = []

            def annotate(self, **kwargs):
                self.calls.append(("annotate", sorted(kwargs)))
                return self

            def order_by(self, *fields):
                self.calls.append(("order_by", fields))
                return self

            def exclude(self, **kwargs):
                self.calls.append(("exclude", kwargs))
                return self

            def values(self, *fields):
                self.calls.append(("values", fields))
                return self

            def values_list(self, *fields, **kwargs):
                self.calls.append(("values_list", fields, kwargs))
                return self

            def distinct(self):
                self.calls.append(("distinct",))
                return self

        brand_rows = FakeQuerySet("brands")
        perfume_rows = FakeQuerySet("perfumes")
        variant_rows = FakeQuerySet("variants")
        request = RequestFactory().get(
            "/admin/our-products/",
            {"q": "vanilla", "tab": "unexpected"},
        )

        result = build_our_product_catalog_list_context(
            request,
            {"variants": [object(), object()]},
            brand_manager=brand_rows,
            perfume_manager=perfume_rows,
            variant_manager=variant_rows,
        )

        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["search_query"], "vanilla")
        self.assertEqual(result["active_tab"], "products")
        self.assertIs(result["brand_rows"], brand_rows)
        self.assertIs(result["collection_rows"], perfume_rows)
        self.assertIs(result["concentration_rows"], perfume_rows)
        self.assertIs(result["variant_type_rows"], variant_rows)
        self.assertIn(("order_by", ("name",)), brand_rows.calls)
        self.assertIn(("exclude", {"collection_name": ""}), perfume_rows.calls)
        self.assertIn(
            ("values", ("brand_id", "brand__name", "collection_name")),
            perfume_rows.calls,
        )
        self.assertIn(
            ("order_by", ("brand__name", "collection_name")),
            perfume_rows.calls,
        )
        self.assertIn(("exclude", {"concentration": ""}), perfume_rows.calls)
        self.assertIn(
            ("values_list", ("variant_type",), {"flat": True}),
            variant_rows.calls,
        )

    def test_build_fragrantica_product_review_context_filters_fragrantica_rows(self):
        from prices.services.catalog_review import (
            build_fragrantica_product_review_context,
        )

        class FakeQuerySet:
            def __init__(self, rows):
                self.rows = rows
                self.calls = []

            def select_related(self, *fields):
                self.calls.append(("select_related", fields))
                return self

            def filter(self, *args, **kwargs):
                self.calls.append(("filter", args, kwargs))
                rows = self.rows
                if "brand_name__iexact" in kwargs:
                    rows = [
                        row
                        for row in rows
                        if row.brand_name.lower()
                        == kwargs["brand_name__iexact"].lower()
                    ]
                if "match_status" in kwargs:
                    rows = [
                        row
                        for row in rows
                        if row.match_status == kwargs["match_status"]
                    ]
                filtered = FakeQuerySet(rows)
                filtered.calls = self.calls
                return filtered

            def order_by(self, *fields):
                self.calls.append(("order_by", fields))
                return self

            def values(self, *fields):
                self.calls.append(("values", fields))
                return FakeValuesQuerySet(self.rows, fields)

            def __iter__(self):
                return iter(self.rows)

            def __getitem__(self, item):
                return self.rows[item]

            def count(self):
                return len(self.rows)

        class FakeValuesQuerySet:
            def __init__(self, rows, fields):
                self.rows = rows
                self.fields = fields
                self.count_name = None

            def annotate(self, **kwargs):
                self.count_name = next(iter(kwargs))
                return self

            def order_by(self, *fields):
                return sorted(
                    self._dict_rows(),
                    key=lambda row: tuple(row[field] for field in fields),
                )

            def values_list(self, *fields):
                return [
                    tuple(row[field] for field in fields) for row in self._dict_rows()
                ]

            def _dict_rows(self):
                grouped = defaultdict(int)
                for row in self.rows:
                    key = tuple(getattr(row, field) for field in self.fields)
                    grouped[key] += 1
                return [
                    {
                        **dict(zip(self.fields, key, strict=False)),
                        self.count_name or "count": count,
                    }
                    for key, count in grouped.items()
                ]

        class FakePerfumeQuerySet:
            def select_related(self, *fields):
                return self

            def filter(self, *args, **kwargs):
                return self

            def __iter__(self):
                return iter(())

        class FakePaginator:
            def __init__(self, rows, page_size):
                self.rows = list(rows)
                self.page_size = page_size
                self.num_pages = 2

            def get_page(self, page_number):
                return SimpleNamespace(object_list=self.rows, number=page_number)

        unlinked = SimpleNamespace(
            brand_name="Montale",
            name="Evidence Scent",
            collection_name="Classic",
            audience="Women",
            release_year=2008,
            match_status="unlinked",
            matched_perfume=None,
        )
        linked = SimpleNamespace(
            brand_name="Montale",
            name="Linked Scent",
            collection_name="Classic",
            audience="Men",
            release_year=2010,
            match_status="linked",
            matched_perfume=None,
        )
        other_brand = SimpleNamespace(
            brand_name="Amouage",
            name="Other Scent",
            collection_name="",
            audience="Unisex",
            release_year=2020,
            match_status="unlinked",
            matched_perfume=None,
        )
        fragrantica_rows = FakeQuerySet([unlinked, linked, other_brand])
        request = RequestFactory().get(
            "/admin/fragrantica-products/",
            {
                "brand": "Montale",
                "status": "unlinked",
                "page": "3",
            },
        )

        result = build_fragrantica_product_review_context(
            request,
            fragrantica_manager=fragrantica_rows,
            perfume_manager=FakePerfumeQuerySet(),
            paginator_class=FakePaginator,
            page_size=25,
        )

        self.assertEqual(result["selected_brand"], "Montale")
        self.assertEqual(result["search_query"], "")
        self.assertEqual(result["status_filter"], "unlinked")
        self.assertEqual(result["status_counts"], {"unlinked": 1, "linked": 1})
        self.assertEqual(result["total_count"], 3)
        self.assertEqual(result["filtered_count"], 1)
        self.assertEqual(result["rows"][0]["source"], unlinked)
        self.assertNotIn("page=", result["query_string"])
        self.assertEqual(result["paginator"].page_size, 25)
        self.assertIn(
            ("filter", (), {"brand_name__iexact": "Montale"}), fragrantica_rows.calls
        )

    def test_build_catalog_tab_action_result_handles_catalogue_tab_mutations(self):
        from prices.services.catalog_review import build_catalog_tab_action_result

        class FakeBrand:
            def __init__(self, name, pk=7):
                self.pk = pk
                self.name = name
                self.deleted = False
                self.save_calls = []

            def delete(self):
                self.deleted = True

            def save(self, **kwargs):
                self.save_calls.append(kwargs)

        class FakeBrandQuerySet:
            def __init__(self, brand=None):
                self.brand = brand
                self.exclude_calls = []

            def exclude(self, **kwargs):
                self.exclude_calls.append(kwargs)
                return self

            def first(self):
                return self.brand

        class FakeBrandManager:
            def __init__(self, brand=None, created=True, duplicate_brand=None):
                self.brand = brand
                self.created = created
                self.duplicate_brand = duplicate_brand
                self.calls = []

            def get_or_create(self, **kwargs):
                self.calls.append(("get_or_create", kwargs))
                return self.brand, self.created

            def filter(self, **kwargs):
                self.calls.append(("filter", kwargs))
                if "name__iexact" in kwargs:
                    return FakeBrandQuerySet(self.duplicate_brand)
                return FakeBrandQuerySet(self.brand)

        class FakePerfumeQuerySet:
            def __init__(self, exists=False, updated=3):
                self.exists_value = exists
                self.updated = updated
                self.update_calls = []

            def exists(self):
                return self.exists_value

            def update(self, **kwargs):
                self.update_calls.append(kwargs)
                return self.updated

        class FakePerfumeManager:
            def __init__(self, exists=False, updated=3):
                self.calls = []
                self.queryset = FakePerfumeQuerySet(exists=exists, updated=updated)

            def filter(self, **kwargs):
                self.calls.append(("filter", kwargs))
                return self.queryset

        missing_name = build_catalog_tab_action_result(
            {"action": "add_brand", "tab": "brands", "name": " "}
        )

        self.assertEqual(missing_name.level, "error")
        self.assertEqual(missing_name.message, "Brand name is required.")
        self.assertEqual(missing_name.tab, "brands")

        brand = FakeBrand("Montale")
        brand_manager = FakeBrandManager(brand=brand, created=False)
        existing_brand = build_catalog_tab_action_result(
            {"action": "add_brand", "name": "Montale"},
            brand_manager=brand_manager,
        )

        self.assertEqual(existing_brand.level, "success")
        self.assertEqual(existing_brand.message, "Brand already exists: Montale.")
        self.assertEqual(brand_manager.calls, [("get_or_create", {"name": "Montale"})])

        renamed_brand = build_catalog_tab_action_result(
            {
                "action": "rename_brand",
                "tab": "brands",
                "brand_id": "7",
                "new_value": "Montale Paris",
            },
            brand_manager=brand_manager,
        )

        self.assertEqual(renamed_brand.level, "success")
        self.assertEqual(brand.name, "Montale Paris")
        self.assertEqual(
            brand.save_calls,
            [{"update_fields": ["name", "updated_at"]}],
        )

        perfume_manager = FakePerfumeManager(updated=4)
        renamed = build_catalog_tab_action_result(
            {
                "action": "rename_collection",
                "tab": "collections",
                "brand_id": "7",
                "old_value": "Classic",
                "new_value": "Archive",
            },
            brand_manager=brand_manager,
            perfume_manager=perfume_manager,
            collection_getter=lambda brand, name: SimpleNamespace(
                id=99,
                brand=brand,
                name=name,
            ),
        )

        self.assertEqual(renamed.level, "success")
        self.assertEqual(
            renamed.message,
            "Collection renamed on 4 Montale Paris products.",
        )
        self.assertEqual(
            perfume_manager.calls,
            [("filter", {"brand": brand, "collection_name": "Classic"})],
        )
        self.assertEqual(
            perfume_manager.queryset.update_calls,
            [
                {
                    "collection_name": "Archive",
                    "collection": SimpleNamespace(
                        id=99,
                        brand=brand,
                        name="Archive",
                    ),
                }
            ],
        )

        used_brand = FakeBrand("Used House")
        used_brand_result = build_catalog_tab_action_result(
            {"action": "delete_brand", "brand_id": "7"},
            brand_manager=FakeBrandManager(brand=used_brand),
            perfume_manager=FakePerfumeManager(exists=True),
        )

        self.assertEqual(used_brand_result.level, "error")
        self.assertFalse(used_brand.deleted)
        self.assertIn("Used House has products", used_brand_result.message)

        unused_brand = FakeBrand("Unused House")
        deleted_brand_result = build_catalog_tab_action_result(
            {"action": "delete_brand", "brand_id": "8"},
            brand_manager=FakeBrandManager(brand=unused_brand),
            perfume_manager=FakePerfumeManager(exists=False),
        )

        self.assertEqual(deleted_brand_result.level, "success")
        self.assertEqual(deleted_brand_result.message, "Brand deleted: Unused House.")
        self.assertTrue(unused_brand.deleted)

    def test_normalize_fragrantica_review_status_defaults_unknown_values(self):
        from prices.services.catalog_review import normalize_fragrantica_review_status

        self.assertEqual(normalize_fragrantica_review_status("linked"), "linked")
        self.assertEqual(normalize_fragrantica_review_status("unexpected"), "all")
        self.assertEqual(normalize_fragrantica_review_status(""), "all")

    def test_fragrance_name_without_audience_removes_gender_words(self):
        from prices.services.catalog_review import (
            audience_group_from_text,
            fragrance_name_without_audience,
            fragrance_name_without_audience_or_concentration,
        )

        self.assertEqual(
            fragrance_name_without_audience("Light Blue Woman"), "light blue"
        )
        self.assertEqual(
            fragrance_name_without_audience("Light Blue pour Femme"), "light blue"
        )
        self.assertEqual(
            fragrance_name_without_audience("Gucci Guilty wom"), "gucci guilty"
        )
        self.assertEqual(audience_group_from_text("Light Blue Femme"), "women")
        self.assertEqual(audience_group_from_text("Gucci Guilty wom"), "women")
        self.assertEqual(audience_group_from_text("Light Blue Homme"), "men")
        self.assertEqual(
            fragrance_name_without_audience_or_concentration(
                "Light Blue Eau de Toilette"
            ),
            "light blue",
        )

    def test_fragrantica_review_row_status_detects_collection_conflicts_first(self):
        from prices.services.catalog_review import fragrantica_review_row_status

        perfume = SimpleNamespace(collection_name="Classic", linked_supplier_count=3)
        evidence = [SimpleNamespace(collection_name="Limited")]

        self.assertEqual(
            fragrantica_review_row_status(perfume, evidence),
            ("collection_review", "Collection review"),
        )

    def test_fragrantica_review_row_status_handles_linked_evidence_and_catalogue_only(
        self,
    ):
        from prices.services.catalog_review import fragrantica_review_row_status

        linked = SimpleNamespace(collection_name="Classic", linked_supplier_count=1)
        with_evidence = SimpleNamespace(
            collection_name="Classic", linked_supplier_count=0
        )
        catalogue_only = SimpleNamespace(
            collection_name="Classic", linked_supplier_count=0
        )
        evidence = [SimpleNamespace(collection_name="Classic")]

        self.assertEqual(
            fragrantica_review_row_status(linked, evidence), ("linked", "Linked")
        )
        self.assertEqual(
            fragrantica_review_row_status(with_evidence, evidence),
            ("supplier_evidence", "Supplier evidence"),
        )
        self.assertEqual(
            fragrantica_review_row_status(catalogue_only, []),
            ("catalog_only", "Catalogue only"),
        )

    def test_build_missing_supplier_rows_skips_catalogue_keys_and_sorts(self):
        from prices.services.catalog_review import build_missing_supplier_rows

        known = [
            SimpleNamespace(
                normalized_brand="Brand B",
                display_product_name="",
                product_name_text="Known",
                collection_name="",
            )
        ]
        missing = [
            SimpleNamespace(
                normalized_brand="Brand A",
                display_product_name="Missing",
                product_name_text="Missing raw",
                collection_name="Collection",
            )
        ]
        rows = build_missing_supplier_rows(
            {
                (1, "known"): known,
                (2, "missing"): missing,
            },
            {(1, "known")},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["brand"], "Brand A")
        self.assertEqual(rows[0]["name"], "Missing")
        self.assertEqual(rows[0]["collections"], ["Collection"])
        self.assertEqual(rows[0]["count"], 1)

    def test_parse_catalog_variant_size_handles_decimal_ml_and_invalid_labels(self):
        from prices.services.catalog_review import parse_catalog_variant_size

        self.assertEqual(parse_catalog_variant_size("100 ml"), (Decimal("100"), ""))
        self.assertEqual(parse_catalog_variant_size("7,5ml"), (Decimal("7.5"), ""))
        self.assertEqual(
            parse_catalog_variant_size("travel spray"), (None, "travel spray")
        )
        self.assertEqual(parse_catalog_variant_size(""), (None, ""))

    def test_build_catalog_variant_inline_update_normalizes_post_data(self):
        from prices.services.catalog_review import build_catalog_variant_inline_update

        update_data = build_catalog_variant_inline_update(
            {
                "brand_name": " Montale ",
                "perfume_name": " Vanilla Extasy ",
                "collection_name": " Classic ",
                "concentration": " EDP ",
                "size_ml": "100 ml",
                "is_tester": "1",
                "packaging": " box ",
                "variant_type": "",
            }
        )

        self.assertEqual(update_data.brand_name, "Montale")
        self.assertEqual(update_data.perfume_name, "Vanilla Extasy")
        self.assertEqual(update_data.collection_name, "Classic")
        self.assertEqual(update_data.concentration, "EDP")
        self.assertEqual(update_data.size_ml, Decimal("100"))
        self.assertEqual(update_data.size_label, "")
        self.assertTrue(update_data.is_tester)
        self.assertEqual(update_data.packaging, "box")
        self.assertEqual(update_data.variant_type, "standard")

    def test_apply_catalog_variant_inline_update_validates_and_updates_variant(self):
        from prices.services.catalog_review import apply_catalog_variant_inline_update

        class FakeManager:
            def __init__(self, brand):
                self.brand = brand
                self.calls = []

            def filter(self, **kwargs):
                self.calls.append(("filter", kwargs))
                return SimpleNamespace(first=lambda: self.brand)

        missing_brand = SimpleNamespace()
        invalid_variant = SimpleNamespace(
            perfume=SimpleNamespace(save_calls=[]),
            save_calls=[],
        )

        invalid = apply_catalog_variant_inline_update(
            invalid_variant,
            {"brand_name": "", "perfume_name": "Scent"},
            brand_manager=FakeManager(missing_brand),
        )

        self.assertEqual(invalid.level, "error")
        self.assertEqual(invalid.message, "Brand and scent are required.")
        self.assertEqual(invalid_variant.perfume.save_calls, [])
        self.assertEqual(invalid_variant.save_calls, [])

        not_found_manager = FakeManager(None)
        not_found = apply_catalog_variant_inline_update(
            invalid_variant,
            {"brand_name": "Unknown", "perfume_name": "Scent"},
            brand_manager=not_found_manager,
        )

        self.assertEqual(not_found.level, "error")
        self.assertEqual(
            not_found.message, "Choose an existing brand from the catalogue."
        )
        self.assertEqual(
            not_found_manager.calls,
            [("filter", {"name__iexact": "Unknown"})],
        )

        brand = SimpleNamespace(name="Montale")
        perfume = SimpleNamespace(save_calls=[])
        perfume.save = lambda **kwargs: perfume.save_calls.append(kwargs)
        variant = SimpleNamespace(perfume=perfume, save_calls=[])
        variant.save = lambda **kwargs: variant.save_calls.append(kwargs)

        result = apply_catalog_variant_inline_update(
            variant,
            {
                "brand_name": "Montale",
                "perfume_name": "Vanilla Extasy",
                "collection_name": "Classic",
                "concentration": "EDP",
                "size_ml": "7,5ml",
                "is_tester": "1",
                "packaging": "box",
                "variant_type": "mini",
            },
            brand_manager=FakeManager(brand),
        )

        self.assertEqual(result.level, "success")
        self.assertEqual(result.message, "Product row updated.")
        self.assertIs(perfume.brand, brand)
        self.assertEqual(perfume.name, "Vanilla Extasy")
        self.assertEqual(perfume.collection_name, "Classic")
        self.assertEqual(perfume.concentration, "EDP")
        self.assertEqual(variant.size_ml, Decimal("7.5"))
        self.assertEqual(variant.size_label, "")
        self.assertTrue(variant.is_tester)
        self.assertEqual(variant.packaging, "box")
        self.assertEqual(variant.variant_type, "mini")
        self.assertEqual(
            perfume.save_calls,
            [
                {
                    "update_fields": [
                        "brand",
                        "name",
                        "collection",
                        "collection_name",
                        "concentration",
                        "updated_at",
                    ]
                }
            ],
        )
        self.assertEqual(
            variant.save_calls,
            [
                {
                    "update_fields": [
                        "size_ml",
                        "size_label",
                        "is_tester",
                        "packaging",
                        "variant_type",
                        "updated_at",
                    ]
                }
            ],
        )

    def test_run_catalog_tab_post_action_builds_message_and_redirect(self):
        from prices.services.catalog_review import (
            CatalogTabActionResult,
            run_catalog_tab_post_action,
        )

        post_data = {"action": "add_brand", "tab": "brands"}
        calls = []

        def action_builder(post_data_arg):
            calls.append(post_data_arg)
            return CatalogTabActionResult("success", "Brand created.", "brands")

        result = run_catalog_tab_post_action(
            post_data,
            action_builder=action_builder,
        )

        self.assertEqual(result.level, "success")
        self.assertEqual(result.message, "Brand created.")
        self.assertEqual(result.redirect_url, "/admin/our-products/?tab=brands")
        self.assertEqual(calls, [post_data])

    def test_catalog_variant_inline_update_redirect_url_uses_safe_next(self):
        from prices.services.catalog_review import (
            catalog_variant_inline_update_redirect_url,
        )

        self.assertEqual(
            catalog_variant_inline_update_redirect_url(
                "/admin/our-products/?tab=products",
                host="testserver",
            ),
            "/admin/our-products/?tab=products",
        )
        self.assertEqual(
            catalog_variant_inline_update_redirect_url(
                "https://evil.example/steal",
                host="testserver",
                fallback_url="/admin/our-products/",
            ),
            "/admin/our-products/",
        )

    def test_run_catalog_variant_inline_update_action_updates_and_redirects(self):
        from prices.services.catalog_review import (
            CatalogVariantInlineUpdateResult,
            run_catalog_variant_inline_update_action,
        )

        variant = SimpleNamespace(id=7)
        calls = []

        def variant_getter(pk):
            calls.append(("variant", pk))
            return variant

        def update_func(variant_arg, post_data):
            calls.append(("update", variant_arg, post_data.get("brand_name")))
            return CatalogVariantInlineUpdateResult("success", "Updated.")

        result = run_catalog_variant_inline_update_action(
            7,
            {
                "next": "/admin/our-products/?tab=products",
                "brand_name": "Montale",
            },
            host="testserver",
            variant_getter=variant_getter,
            update_func=update_func,
        )

        self.assertEqual(result.level, "success")
        self.assertEqual(result.message, "Updated.")
        self.assertEqual(result.redirect_url, "/admin/our-products/?tab=products")
        self.assertEqual(
            calls,
            [
                ("variant", 7),
                ("update", variant, "Montale"),
            ],
        )

    def test_build_our_product_detail_offers_queryset_filters_hidden_terms(self):
        from prices.services.catalog_review import (
            build_our_product_detail_offers_queryset,
        )

        class FakeQuerySet:
            def __init__(self):
                self.calls = []

            def select_related(self, *fields):
                self.calls.append(("select_related", fields, {}))
                return self

            def filter(self, *args, **kwargs):
                self.calls.append(("filter", args, kwargs))
                return self

            def exclude(self, *args, **kwargs):
                self.calls.append(("exclude", args, kwargs))
                return self

        request = RequestFactory().get(
            "/admin/our-products/1/",
            {"exclude": "sample"},
        )
        request.user = SimpleNamespace(is_authenticated=False)
        our_product = SimpleNamespace(id=17)
        queryset = FakeQuerySet()

        result = build_our_product_detail_offers_queryset(
            request,
            our_product,
            supplier_product_manager=queryset,
        )

        self.assertIs(result, queryset)
        self.assertEqual(
            [call[0] for call in queryset.calls],
            ["select_related", "filter", "exclude"],
        )
        self.assertEqual(queryset.calls[0], ("select_related", ("supplier",), {}))
        self.assertEqual(
            queryset.calls[1], ("filter", (), {"our_product": our_product})
        )

    def test_build_our_product_detail_context_adds_filtered_offers(self):
        from prices.services.catalog_review import build_our_product_detail_context

        request = RequestFactory().get("/admin/our-products/17/")
        our_product = SimpleNamespace(id=17)
        offers = [SimpleNamespace(id=1)]
        calls = []

        def offers_builder(request_arg, our_product_arg):
            calls.append((request_arg, our_product_arg))
            return offers

        context = build_our_product_detail_context(
            request,
            our_product,
            offers_builder=offers_builder,
        )

        self.assertEqual(context, {"offers": offers})
        self.assertEqual(calls, [(request, our_product)])


class ProductOperationServiceTests(SimpleTestCase):
    class FakeQuerySet:
        def __init__(self):
            self.filter_calls = []
            self.deleted = False

        def filter(self, *args, **kwargs):
            self.filter_calls.append((args, kwargs))
            return self

        def delete(self):
            self.deleted = True
            return (3, {"prices.SupplierProduct": 3})

    class FakeManager:
        def __init__(self):
            self.filter_calls = []
            self.queryset = ProductOperationServiceTests.FakeQuerySet()

        def filter(self, *args, **kwargs):
            self.filter_calls.append((args, kwargs))
            return self.queryset

    def test_delete_orphan_supplier_products_filters_unimported_rows(self):
        from prices.services.product_operations import delete_orphan_supplier_products

        manager = self.FakeManager()

        result = delete_orphan_supplier_products(product_manager=manager)

        self.assertEqual(result, (3, {"prices.SupplierProduct": 3}))
        self.assertEqual(
            manager.filter_calls,
            [
                (
                    (),
                    {
                        "created_import_batch__isnull": True,
                        "last_import_batch__isnull": True,
                    },
                )
            ],
        )
        self.assertTrue(manager.queryset.deleted)

    def test_delete_inactive_supplier_products_applies_optional_supplier_filter(self):
        from prices.services.product_operations import delete_inactive_supplier_products

        manager = self.FakeManager()

        delete_inactive_supplier_products("5, bad 7 5", product_manager=manager)

        self.assertEqual(manager.filter_calls, [((), {"is_active": False})])
        self.assertEqual(
            manager.queryset.filter_calls,
            [((), {"supplier_id__in": [5, 7]})],
        )
        self.assertTrue(manager.queryset.deleted)

    def test_delete_supplier_products_by_ids_skips_empty_selection(self):
        from prices.services.product_operations import delete_supplier_products_by_ids

        manager = self.FakeManager()

        result = delete_supplier_products_by_ids([], product_manager=manager)

        self.assertIsNone(result)
        self.assertEqual(manager.filter_calls, [])

    def test_delete_supplier_products_by_ids_deletes_selected_rows(self):
        from prices.services.product_operations import delete_supplier_products_by_ids

        manager = self.FakeManager()

        delete_supplier_products_by_ids(["3", "4"], product_manager=manager)

        self.assertEqual(manager.filter_calls, [((), {"id__in": ["3", "4"]})])
        self.assertTrue(manager.queryset.deleted)

    def test_run_supplier_product_cleanup_action_deletes_and_redirects(self):
        from prices.services.product_operations import (
            run_supplier_product_cleanup_action,
        )

        calls = []

        redirect_url = run_supplier_product_cleanup_action(
            delete_func=lambda: calls.append("delete")
        )

        self.assertEqual(calls, ["delete"])
        self.assertEqual(redirect_url, "/admin/products/")

    def test_run_supplier_product_inactive_cleanup_action_deletes_and_redirects(self):
        from prices.services.product_operations import (
            run_supplier_product_inactive_cleanup_action,
        )

        calls = []

        redirect_url = run_supplier_product_inactive_cleanup_action(
            "5,7",
            delete_func=lambda raw_supplier_filter: calls.append(raw_supplier_filter),
        )

        self.assertEqual(calls, ["5,7"])
        self.assertEqual(redirect_url, "/admin/products/")

    def test_run_supplier_product_bulk_delete_action_uses_safe_next_url(self):
        from prices.services.product_operations import (
            run_supplier_product_bulk_delete_action,
        )

        calls = []

        redirect_url = run_supplier_product_bulk_delete_action(
            ["3", "4"],
            next_url_raw="/admin/products/?q=oud",
            host="testserver",
            delete_func=lambda product_ids: calls.append(list(product_ids)),
        )

        self.assertEqual(calls, [["3", "4"]])
        self.assertEqual(redirect_url, "/admin/products/?q=oud")

    def test_run_supplier_product_bulk_delete_action_rejects_unsafe_next_url(self):
        from prices.services.product_operations import (
            run_supplier_product_bulk_delete_action,
        )

        calls = []

        redirect_url = run_supplier_product_bulk_delete_action(
            ["3"],
            next_url_raw="https://evil.example/products/",
            host="testserver",
            delete_func=lambda product_ids: calls.append(list(product_ids)),
        )

        self.assertEqual(calls, [["3"]])
        self.assertEqual(redirect_url, "/admin/products/")

    def test_save_supplier_product_link_form_saves_only_valid_form(self):
        from prices.services.product_operations import save_supplier_product_link_form

        valid_form = SimpleNamespace(
            is_valid=lambda: True,
            save=lambda: "saved-product",
        )
        invalid_form = SimpleNamespace(
            is_valid=lambda: False,
            save=lambda: self.fail("invalid form should not save"),
        )

        self.assertEqual(save_supplier_product_link_form(valid_form), "saved-product")
        self.assertIsNone(save_supplier_product_link_form(invalid_form))

    def test_run_supplier_product_link_action_builds_and_saves_form(self):
        from prices.services.product_operations import run_supplier_product_link_action

        calls = []
        product = SimpleNamespace(id=7)
        post_data = {"our_product": "3"}

        class FakeForm:
            def __init__(self, data, *, instance):
                calls.append(("form", data, instance))

        def fake_save(form):
            calls.append(("save", isinstance(form, FakeForm)))
            return "saved-product"

        result = run_supplier_product_link_action(
            product,
            post_data,
            form_class=FakeForm,
            save_func=fake_save,
        )

        self.assertEqual(result, "saved-product")
        self.assertEqual(
            calls,
            [
                ("form", post_data, product),
                ("save", True),
            ],
        )


class ProductFilterServiceTests(SimpleTestCase):
    def test_supplier_product_base_queryset_selects_list_fields(self):
        from prices.services.product_filters import (
            SUPPLIER_PRODUCT_LIST_FIELDS,
            supplier_product_base_queryset,
        )

        class FakeQuerySet:
            def __init__(self):
                self.calls = []

            def select_related(self, *fields):
                self.calls.append(("select_related", fields))
                return self

            def only(self, *fields):
                self.calls.append(("only", fields))
                return self

        class FakeManager:
            def __init__(self):
                self.queryset = FakeQuerySet()
                self.calls = []

            def all(self):
                self.calls.append(("all",))
                return self.queryset

        manager = FakeManager()

        queryset = supplier_product_base_queryset(product_manager=manager)

        self.assertIs(queryset, manager.queryset)
        self.assertEqual(manager.calls, [("all",)])
        self.assertEqual(
            manager.queryset.calls,
            [
                ("select_related", ("supplier",)),
                ("only", SUPPLIER_PRODUCT_LIST_FIELDS),
            ],
        )

    def test_parse_search_query_splits_inline_excludes(self):
        from prices.services.product_filters import parse_search_query

        include, exclude = parse_search_query("montale intense -tester -mini")

        self.assertEqual(include, ["montale", "intense"])
        self.assertEqual(exclude, ["tester", "mini"])

    def test_parse_supplier_filter_ids_deduplicates_and_ignores_invalid_values(self):
        from prices.services.product_filters import parse_supplier_filter_ids

        ids = parse_supplier_filter_ids("3, nope 2 3 -1 0 4")

        self.assertEqual(ids, [3, 2, 4])

    def test_parse_decimal_query_param_accepts_spaces_and_commas(self):
        from prices.services.product_filters import parse_decimal_query_param

        self.assertEqual(parse_decimal_query_param(" 1 234,50 "), Decimal("1234.50"))
        self.assertIsNone(parse_decimal_query_param("not a price"))

    def test_collect_front_filter_values_merges_repeated_suppliers(self):
        from prices.services.product_filters import (
            collect_front_filter_values,
            has_front_filter_params,
        )

        request = RequestFactory().get(
            "/products/",
            {
                "q": "oud",
                "supplier": ["3", "4"],
                "include_inactive_suppliers": "1",
                "exclude": "tester",
            },
        )

        self.assertTrue(has_front_filter_params(request))
        values = collect_front_filter_values(request)
        self.assertEqual(values["q"], "oud")
        self.assertEqual(values["supplier"], "3,4")
        self.assertEqual(values["include_inactive_suppliers"], "1")
        self.assertEqual(values["exclude"], "tester")

    def test_viewer_front_filter_redirect_saves_explicit_filters(self):
        from prices.services.product_filters import (
            resolve_viewer_front_filter_redirect_url,
        )

        request = RequestFactory().get("/products/", {"q": "oud"})
        request.user = SimpleNamespace(is_authenticated=True)
        saved = []

        redirect_url = resolve_viewer_front_filter_redirect_url(
            request,
            save_func=lambda saved_request: saved.append(saved_request),
        )

        self.assertEqual(redirect_url, "")
        self.assertEqual(saved, [request])

    def test_viewer_front_filter_redirect_uses_saved_filters_when_query_is_empty(self):
        from prices.services.product_filters import (
            resolve_viewer_front_filter_redirect_url,
        )

        request = RequestFactory().get("/products/")
        request.user = SimpleNamespace(is_authenticated=True)
        prefs = SimpleNamespace(
            supplier_front_filters={
                "q": "oud",
                "supplier": "3,4",
                "include_inactive_suppliers": "1",
                "status": " ",
                "unexpected": "ignored",
            }
        )

        redirect_url = resolve_viewer_front_filter_redirect_url(
            request,
            preferences_getter=lambda user: prefs,
            save_func=lambda request: self.fail("empty query should not save filters"),
        )

        self.assertEqual(
            redirect_url,
            "/products/?q=oud&supplier=3%2C4&include_inactive_suppliers=1",
        )

    def test_supplier_filter_ids_from_request_merges_repeated_values(self):
        from prices.services.product_filters import (
            serialize_supplier_filter_ids,
            supplier_filter_ids_from_request,
        )

        request = RequestFactory().get(
            "/products/",
            {"supplier": ["3,2", "bad", "2", "5"]},
        )

        ids = supplier_filter_ids_from_request(request)

        self.assertEqual(ids, [3, 2, 5])
        self.assertEqual(serialize_supplier_filter_ids(ids), "3,2,5")

    def test_token_filter_applies_at_most_six_include_terms(self):
        from prices.services.product_filters import apply_supplier_product_token_filter

        class FakeQuerySet:
            def __init__(self):
                self.filter_calls = []

            def filter(self, *args, **kwargs):
                self.filter_calls.append((args, kwargs))
                return self

        queryset = FakeQuerySet()

        result = apply_supplier_product_token_filter(
            queryset,
            ["one", "two", "three", "four", "five", "six", "seven"],
        )

        self.assertIs(result, queryset)
        self.assertEqual(len(queryset.filter_calls), 6)
        self.assertIn("supplier_sku__icontains", repr(queryset.filter_calls[0]))

    @patch("assistant_linking.services.smart_search.apply_smart_supplier_search")
    def test_search_filter_uses_smart_search_only_when_requested(
        self, mock_smart_search
    ):
        from prices.services.product_filters import apply_supplier_product_search_filter

        queryset = SimpleNamespace(name="queryset")
        request = RequestFactory().get("/products/", {"smart": "yes"})
        mock_smart_search.return_value = "smart-result"

        result = apply_supplier_product_search_filter(
            queryset,
            "oud amber",
            ["oud", "amber"],
            request,
        )

        self.assertEqual(result, "smart-result")
        mock_smart_search.assert_called_once_with(queryset, "oud amber")

    def test_status_normalization_defaults_unknown_values_to_all(self):
        from prices.services.product_filters import normalize_supplier_product_status

        self.assertEqual(normalize_supplier_product_status(" active "), "active")
        self.assertEqual(normalize_supplier_product_status("inactive"), "inactive")
        self.assertEqual(normalize_supplier_product_status("unexpected"), "all")
        self.assertEqual(normalize_supplier_product_status(None), "all")

    def test_filter_state_from_request_collects_product_list_filters(self):
        from django.contrib.auth.models import AnonymousUser

        from prices.services.product_filters import (
            supplier_product_filter_state_from_request,
        )

        request = RequestFactory().get(
            "/products/",
            {
                "q": "oud -tester",
                "supplier": ["4", "bad", "7"],
                "status": "active",
                "currency": models.Currency.RUB,
                "exclude": "sample, mini",
                "smart": "on",
                "include_inactive_suppliers": "1",
            },
        )
        request.user = AnonymousUser()

        state = supplier_product_filter_state_from_request(request)

        self.assertEqual(state.query, "oud -tester")
        self.assertEqual(state.include_tokens, ["oud"])
        self.assertEqual(state.inline_exclude_tokens, ["tester"])
        self.assertEqual(state.supplier_filter_ids, [4, 7])
        self.assertEqual(state.status_filter, "active")
        self.assertEqual(state.currency, models.Currency.RUB)
        self.assertEqual(state.exclude_terms, ["sample", "mini"])
        self.assertTrue(state.smart_search_enabled)
        self.assertTrue(state.include_inactive_suppliers)

    @patch("prices.services.product_filters.models.Supplier.objects")
    def test_filter_context_serializes_template_filter_state(
        self, mock_supplier_manager
    ):
        from prices.services.product_filters import (
            SupplierProductFilterState,
            build_supplier_product_filter_context,
        )

        supplier_options = [SimpleNamespace(id=1, name="Any Supplier")]
        active_supplier_queryset = Mock()
        active_supplier_queryset.order_by.return_value = supplier_options
        mock_supplier_manager.filter.side_effect = [
            active_supplier_queryset,
            [SimpleNamespace(id=4, name="Supplier Four")],
        ]
        state = SupplierProductFilterState(
            query="oud",
            include_tokens=["oud"],
            inline_exclude_tokens=[],
            exclude_raw="tester",
            exclude_terms=["tester"],
            currency=models.Currency.RUB,
            supplier_filter_ids=[4, 9],
            include_inactive_suppliers=False,
            status_filter="inactive",
            smart_search_enabled=True,
        )

        context = build_supplier_product_filter_context(
            state,
            price_min_raw="10",
            price_max_raw="90",
        )

        self.assertEqual(context["currency_filter"], models.Currency.RUB)
        self.assertIn(models.Currency.USD, context["currency_options"])
        self.assertEqual(context["supplier_filter"], "4,9")
        self.assertEqual(context["supplier_options"], supplier_options)
        self.assertFalse(context["include_inactive_suppliers"])
        self.assertEqual(
            context["supplier_filter_names"],
            [
                {"id": 4, "name": "Supplier Four"},
                {"id": 9, "name": "Supplier #9"},
            ],
        )
        self.assertEqual(context["status_filter"], "inactive")
        self.assertTrue(context["smart_search_enabled"])
        self.assertEqual(context["exclude_terms"], "tester")
        self.assertEqual(context["price_min"], "10")
        self.assertEqual(context["price_max"], "90")
        active_supplier_queryset.order_by.assert_called_once_with("name")
        self.assertEqual(
            mock_supplier_manager.filter.call_args_list,
            [
                call(is_active=True),
                call(id__in=[4, 9]),
            ],
        )

    def test_apply_filter_state_keeps_list_and_ajax_filter_policy_together(self):
        from prices.services.product_filters import (
            SupplierProductFilterState,
            apply_supplier_product_filter_state,
        )

        class FakeQuerySet:
            def __init__(self):
                self.filter_calls = []
                self.exclude_calls = []

            def filter(self, *args, **kwargs):
                self.filter_calls.append((args, kwargs))
                return self

            def exclude(self, *args, **kwargs):
                self.exclude_calls.append((args, kwargs))
                return self

        request = RequestFactory().get("/products/")
        queryset = FakeQuerySet()
        state = SupplierProductFilterState(
            query="",
            include_tokens=[],
            inline_exclude_tokens=["tester"],
            exclude_raw="mini",
            exclude_terms=["mini"],
            currency=models.Currency.USD,
            supplier_filter_ids=[4, 7],
            include_inactive_suppliers=False,
            status_filter="active",
            smart_search_enabled=False,
        )

        result = apply_supplier_product_filter_state(queryset, state, request)

        self.assertIs(result, queryset)
        self.assertIn(((), {"supplier_id__in": [4, 7]}), queryset.filter_calls)
        self.assertIn(((), {"supplier__is_active": True}), queryset.filter_calls)
        self.assertIn(((), {"is_active": True}), queryset.filter_calls)
        self.assertIn(((), {"name__icontains": "tester"}), queryset.exclude_calls)
        self.assertEqual(len(queryset.exclude_calls), 2)

    def test_apply_filter_state_can_include_inactive_suppliers(self):
        from prices.services.product_filters import (
            SupplierProductFilterState,
            apply_supplier_product_filter_state,
        )

        class FakeQuerySet:
            def __init__(self):
                self.filter_calls = []

            def filter(self, *args, **kwargs):
                self.filter_calls.append((args, kwargs))
                return self

            def exclude(self, *args, **kwargs):
                return self

        request = RequestFactory().get("/products/")
        queryset = FakeQuerySet()
        state = SupplierProductFilterState(
            query="",
            include_tokens=[],
            inline_exclude_tokens=[],
            exclude_raw="",
            exclude_terms=[],
            currency=models.Currency.USD,
            supplier_filter_ids=[],
            include_inactive_suppliers=True,
            status_filter="all",
            smart_search_enabled=False,
        )

        result = apply_supplier_product_filter_state(queryset, state, request)

        self.assertIs(result, queryset)
        self.assertNotIn(((), {"supplier__is_active": True}), queryset.filter_calls)

    def test_build_supplier_product_queryset_for_request_applies_shared_pipeline(self):
        from django.contrib.auth.models import AnonymousUser

        from prices.services.product_filters import (
            SupplierProductFilterState,
            build_supplier_product_queryset_for_request,
        )

        class FakeQuerySet:
            def __init__(self):
                self.calls = []

            def annotate(self, **kwargs):
                self.calls.append(("annotate", kwargs))
                return self

            def order_by(self, *fields):
                self.calls.append(("order_by", fields))
                return self

        request = RequestFactory().get(
            "/products/",
            {
                "currency": models.Currency.RUB,
                "sort": "current_price",
                "dir": "desc",
                "status": "active",
                "price_min": "10",
            },
        )
        request.user = AnonymousUser()
        queryset = FakeQuerySet()
        filter_state = SupplierProductFilterState(
            query="",
            include_tokens=[],
            inline_exclude_tokens=[],
            exclude_raw="",
            exclude_terms=[],
            currency=models.Currency.RUB,
            supplier_filter_ids=[],
            include_inactive_suppliers=False,
            status_filter="active",
            smart_search_enabled=False,
        )

        with (
            patch(
                "prices.services.product_filters.supplier_product_filter_state_from_request",
                return_value=filter_state,
            ) as mock_state,
            patch(
                "prices.services.product_filters.apply_supplier_product_filter_state",
                return_value=queryset,
            ) as mock_apply_filters,
            patch(
                "prices.services.product_filters.apply_supplier_price_filter",
                return_value=(queryset, "10", ""),
            ) as mock_price_filter,
        ):
            result = build_supplier_product_queryset_for_request(
                request,
                base_queryset=queryset,
                rates={(models.Currency.USD, models.Currency.RUB): Decimal("100")},
            )

        mock_state.assert_called_once_with(request)
        mock_apply_filters.assert_called_once_with(queryset, filter_state, request)
        mock_price_filter.assert_called_once()
        self.assertIs(result.queryset, queryset)
        self.assertEqual(result.filter_state, filter_state)
        self.assertEqual(result.price_min_raw, "10")
        self.assertEqual(result.price_max_raw, "")
        self.assertEqual(
            result.ordering_plan.ordering,
            ("-display_price_sort", "id"),
        )
        self.assertEqual(
            queryset.calls[-1], ("order_by", ("-display_price_sort", "id"))
        )

    def test_build_supplier_product_queryset_for_request_uses_fast_default_order(self):
        from django.contrib.auth.models import AnonymousUser

        from prices.services.product_filters import (
            SupplierProductFilterState,
            build_supplier_product_queryset_for_request,
        )

        class FakeQuerySet:
            def __init__(self):
                self.calls = []

            def annotate(self, **kwargs):
                self.calls.append(("annotate", kwargs))
                return self

            def order_by(self, *fields):
                self.calls.append(("order_by", fields))
                return self

        request = RequestFactory().get(
            "/products/search/",
            {"q": "amber", "currency": models.Currency.RUB},
        )
        request.user = AnonymousUser()
        queryset = FakeQuerySet()
        filter_state = SupplierProductFilterState(
            query="amber",
            include_tokens=["amber"],
            inline_exclude_tokens=[],
            exclude_raw="",
            exclude_terms=[],
            currency=models.Currency.RUB,
            supplier_filter_ids=[],
            include_inactive_suppliers=False,
            status_filter="all",
            smart_search_enabled=False,
        )

        with (
            patch(
                "prices.services.product_filters.supplier_product_filter_state_from_request",
                return_value=filter_state,
            ),
            patch(
                "prices.services.product_filters.apply_supplier_product_filter_state",
                return_value=queryset,
            ),
            patch(
                "prices.services.product_filters.apply_supplier_price_filter",
                return_value=(queryset, "", ""),
            ),
        ):
            result = build_supplier_product_queryset_for_request(
                request,
                base_queryset=queryset,
                rates={(models.Currency.USD, models.Currency.RUB): Decimal("100")},
                fast_search_default_order=True,
            )

        self.assertIs(result.queryset, queryset)
        self.assertEqual(result.ordering_plan.display_price_currency, "")
        self.assertEqual(
            result.ordering_plan.ordering,
            ("-is_active", "supplier__name", "name", "id"),
        )
        self.assertNotIn("annotate", [call[0] for call in queryset.calls])
        self.assertEqual(
            queryset.calls[-1],
            ("order_by", ("-is_active", "supplier__name", "name", "id")),
        )

    def test_supplier_product_ordering_keeps_active_products_first_for_all_statuses(
        self,
    ):
        from prices.services.product_filters import supplier_product_ordering

        ordering = supplier_product_ordering(
            sort_field="name",
            sort_dir="desc",
            currency=models.Currency.USD,
            status_filter="all",
            allowed_fields=(
                "supplier",
                "supplier_sku",
                "name",
                "current_price",
                "last_imported_at",
            ),
        )

        self.assertEqual(ordering, ("-is_active", "-name", "id"))

    def test_supplier_product_ordering_resets_unknown_sort_to_price(self):
        from prices.services.product_filters import supplier_product_ordering

        ordering = supplier_product_ordering(
            sort_field="unknown",
            sort_dir="desc",
            currency="original",
            status_filter="active",
            allowed_fields=(
                "supplier",
                "supplier_sku",
                "name",
                "current_price",
                "last_imported_at",
            ),
        )

        self.assertEqual(ordering, ("current_price", "id"))

    def test_supplier_product_ordering_plan_requests_display_price_sort_only_for_price(
        self,
    ):
        from prices.services.product_filters import supplier_product_ordering_plan

        price_plan = supplier_product_ordering_plan(
            sort_field="current_price",
            sort_dir="desc",
            currency=models.Currency.RUB,
            status_filter="all",
        )
        name_plan = supplier_product_ordering_plan(
            sort_field="name",
            sort_dir="desc",
            currency=models.Currency.RUB,
            status_filter="all",
        )

        self.assertEqual(price_plan.display_price_currency, models.Currency.RUB)
        self.assertEqual(
            price_plan.ordering, ("-is_active", "-display_price_sort", "id")
        )
        self.assertEqual(name_plan.display_price_currency, "")
        self.assertEqual(name_plan.ordering, ("-is_active", "-name", "id"))


class CBRSyncCommandTests(SimpleTestCase):
    @patch("prices.management.commands.sync_cbr_rates.upsert_cbr_markup_rates")
    def test_sync_cbr_rates_single_date_uses_markup_argument(self, mock_upsert):
        mock_upsert.return_value = Decimal("100.500000")
        output = io.StringIO()

        call_command(
            "sync_cbr_rates",
            "--date",
            "2026-04-30",
            "--markup-percent",
            "3.5",
            stdout=output,
        )

        mock_upsert.assert_called_once()
        self.assertEqual(mock_upsert.call_args.args[1], Decimal("3.5"))
        self.assertIn("2026-04-30", output.getvalue())

    @patch("prices.management.commands.sync_cbr_rates.upsert_cbr_markup_rates_range")
    def test_sync_cbr_rates_range_uses_markup_argument(self, mock_upsert_range):
        mock_upsert_range.return_value = {
            "total_days": 2,
            "synced_days": 2,
            "errors": [],
        }
        output = io.StringIO()

        call_command(
            "sync_cbr_rates",
            "--start-date",
            "2026-04-29",
            "--end-date",
            "2026-04-30",
            "--markup-percent",
            "2",
            stdout=output,
        )

        mock_upsert_range.assert_called_once()
        self.assertEqual(
            mock_upsert_range.call_args.kwargs["markup_percent"], Decimal("2")
        )


class OurProductCatalogueListTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="staff",
            password="password",
            is_staff=True,
        )
        self.client.force_login(user)
        brand = Brand.objects.create(name="Montale")
        self.perfume = Perfume.objects.create(
            brand=brand,
            name="Vanilla Extasy",
            concentration="Eau de Parfum",
            collection_name="Classic",
        )
        self.variant = PerfumeVariant.objects.create(
            perfume=self.perfume,
            size_ml="100.00",
            packaging="box",
            is_tester=True,
        )

    def test_our_products_page_lists_catalogue_variants(self):
        response = self.client.get(reverse("prices:our_product_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-catalogue-selection-root")
        self.assertContains(response, "Delete selected")
        self.assertContains(response, "data-catalogue-select-toggle")
        self.assertContains(response, "data-catalogue-select-checkbox")
        self.assertContains(response, "Montale")
        self.assertContains(response, "Vanilla Extasy")
        self.assertContains(response, "Eau de Parfum")
        self.assertContains(response, "100ml")
        self.assertContains(response, "tester")
        self.assertContains(response, "box")
        self.assertContains(response, reverse("prices:fragrantica_product_review"))

    def test_our_products_page_shows_catalogue_audience_and_year_subnames(self):
        self.perfume.audience = "Women"
        self.perfume.release_year = 2008
        self.perfume.save(update_fields=["audience", "release_year", "updated_at"])

        response = self.client.get(reverse("prices:our_product_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Collection")
        self.assertContains(response, "Classic")
        self.assertContains(response, "Audience")
        self.assertContains(response, "Women")
        self.assertContains(response, "Year")
        self.assertContains(response, "2008")

    def test_our_products_page_defers_fragrantica_suggestions(self):
        self.perfume.name = "Vanilla Extasy Women"
        self.perfume.save(update_fields=["name", "updated_at"])
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy pour Femme",
            normalized_name="vanilla extasy pour femme",
            collection_name="Fragrantica Collection",
            audience="Women",
            release_year=2008,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )

        response = self.client.get(reverse("prices:our_product_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Find match")
        self.assertContains(response, reverse("prices:fragrantica_product_review"))
        self.assertNotContains(response, "Suggested Fragrantica matches")
        self.assertNotContains(response, "Link without leaving this page")
        self.assertNotContains(response, "Exact brand and scent identity match")
        self.assertNotContains(response, "Fragrantica Collection")
        self.assertNotContains(response, "Montale / Vanilla Extasy pour Femme")
        self.assertNotContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )

    def test_our_products_page_shows_linked_fragrantica_without_more_suggestions(self):
        linked_source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            collection_name="Fragrantica Collection",
            audience="Women",
            release_year=2008,
            matched_perfume=self.perfume,
            match_status=FragranticaProduct.STATUS_LINKED,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )
        FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy Intense",
            normalized_name="vanilla extasy intense",
            collection_name="Other Collection",
            audience="Women",
            release_year=2011,
            source_path="/perfume/Montale/Vanilla-Extasy-2.html",
        )

        response = self.client.get(reverse("prices:our_product_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Linked Fragrantica:")
        self.assertContains(
            response,
            "Montale / Vanilla Extasy / Fragrantica Collection / 2008 / Women",
        )
        self.assertContains(response, "Fragrantica Collection")
        self.assertContains(response, "Linked")
        self.assertNotContains(response, ">Flags<")
        self.assertNotContains(response, "our-products-row-flags")
        self.assertNotContains(response, "Linked Fragrantica match")
        self.assertNotContains(response, "Catalogue fields applied on link")
        self.assertNotContains(
            response,
            "Linked. Fragrantica collection and year are stored",
        )
        self.assertNotContains(response, "Suggested Fragrantica matches")
        self.assertNotContains(
            response,
            reverse("prices:fragrantica_product_link", args=[linked_source.pk]),
        )
        self.assertNotContains(response, "Vanilla Extasy Intense")

    def test_our_products_linked_fragrantica_falls_back_to_catalogue_audience(self):
        self.perfume.audience = "Unisex"
        self.perfume.save(update_fields=["audience", "updated_at"])
        FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            collection_name="Fragrantica Collection",
            release_year=2008,
            matched_perfume=self.perfume,
            match_status=FragranticaProduct.STATUS_LINKED,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )

        response = self.client.get(reverse("prices:our_product_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Montale / Vanilla Extasy / Fragrantica Collection / 2008 / Unisex",
        )

    def test_concentration_audit_flags_name_concentration_conflict(self):
        self.perfume.name = "Vanilla Extasy Eau de Toilette"
        self.perfume.concentration = "Eau de Parfum"
        self.perfume.save(update_fields=["name", "concentration", "updated_at"])

        response = self.client.get(reverse("prices:our_product_concentration_audit"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Name Conflict")
        self.assertContains(response, "Name terms:")
        self.assertContains(response, "Eau de Toilette")
        self.assertContains(response, "Eau de Parfum")

    def test_concentration_audit_flags_linked_fragrantica_conflict(self):
        self.perfume.name = "The Icon"
        self.perfume.concentration = "Eau de Toilette"
        self.perfume.save(update_fields=["name", "concentration", "updated_at"])
        FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="The Icon Eau de Parfum",
            normalized_name="the icon eau de parfum",
            matched_perfume=self.perfume,
            match_status=FragranticaProduct.STATUS_LINKED,
            source_path="/perfume/Montale/The-Icon-1.html",
        )

        response = self.client.get(
            reverse("prices:our_product_concentration_audit"),
            {"issue": "source_conflict"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Source Conflict")
        self.assertContains(response, "Linked Fragrantica")
        self.assertContains(response, "The Icon Eau de Parfum")

    def test_concentration_audit_can_mark_conflict(self):
        self.perfume.name = "Vanilla Extasy Eau de Toilette"
        self.perfume.concentration = "Eau de Parfum"
        self.perfume.save(update_fields=["name", "concentration", "updated_at"])

        response = self.client.post(
            reverse("prices:our_product_concentration_audit"),
            {
                "action": "mark_conflict",
                "perfume_id": str(self.perfume.pk),
                "next": reverse("prices:our_product_concentration_audit"),
            },
        )

        self.assertRedirects(
            response, reverse("prices:our_product_concentration_audit")
        )
        self.perfume.refresh_from_db()
        self.assertEqual(
            self.perfume.verification_status,
            Perfume.VERIFICATION_CONFLICT,
        )

    def test_inline_edit_joins_existing_linked_perfume_identity(self):
        from prices.services.catalog_review import apply_catalog_variant_inline_update

        self.perfume.audience = "Men"
        self.perfume.release_year = 2000
        self.perfume.save(update_fields=["audience", "release_year", "updated_at"])
        FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy for Men",
            normalized_name="vanilla extasy for men",
            collection_name="Classic",
            audience="Men",
            release_year=2000,
            matched_perfume=self.perfume,
            match_status=FragranticaProduct.STATUS_LINKED,
            source_path="/perfume/Montale/Vanilla-Extasy-Men.html",
        )
        duplicate_perfume = Perfume.objects.create(
            brand=self.perfume.brand,
            name="Vanilla Extasy Men",
            concentration="Eau de Parfum",
        )
        duplicate_variant = PerfumeVariant.objects.create(
            perfume=duplicate_perfume,
            size_ml="150.00",
            variant_type="standard",
        )
        supplier = models.Supplier.objects.create(name="Linked Supplier")
        supplier_product = models.SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="linked-duplicate-variant",
            name="Montale Vanilla Extasy Men 150ml",
            catalog_perfume=duplicate_perfume,
            catalog_variant=duplicate_variant,
        )

        result = apply_catalog_variant_inline_update(
            duplicate_variant,
            {
                "brand_name": "Montale",
                "perfume_name": "Vanilla Extasy",
                "collection_name": "",
                "concentration": "Eau de Parfum",
                "size_ml": "150ml",
                "variant_type": "standard",
            },
        )

        self.assertEqual(result.level, "success")
        self.assertIn("joined existing catalogue identity", result.message)
        duplicate_variant.refresh_from_db()
        self.assertEqual(duplicate_variant.perfume, self.perfume)
        supplier_product.refresh_from_db()
        self.assertEqual(supplier_product.catalog_perfume, self.perfume)
        self.assertEqual(supplier_product.catalog_variant, duplicate_variant)
        self.assertFalse(Perfume.objects.filter(pk=duplicate_perfume.pk).exists())

        response = self.client.get(reverse("prices:our_product_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Linked Fragrantica:")
        self.assertNotContains(response, "Vanilla Extasy Men")

    def test_fragrantica_products_lists_fragrantica_rows_only(self):
        supplier = models.Supplier.objects.create(name="Antonina")
        supplier_product = models.SupplierProduct.objects.create(
            supplier=supplier,
            catalog_perfume=self.perfume,
            name="MONTALE Vanilla Extasy edp 100 ml",
            brand="Montale",
            size="100 ml",
        )
        ParsedSupplierProduct.objects.create(
            supplier_product=supplier_product,
            raw_name=supplier_product.name,
            normalized_text="montale vanilla extasy edp 100 ml",
            normalized_brand=self.perfume.brand,
            product_name_text="Vanilla Extasy",
            collection_name="Classic",
            concentration="Eau de Parfum",
            size_ml="100.00",
            confidence=95,
        )
        FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy Source",
            normalized_name="vanilla extasy source",
            collection_name="Fragrantica Collection",
            audience="Women",
            release_year=2008,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )

        response = self.client.get(
            reverse("prices:fragrantica_product_review"),
            {"brand": "Montale"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fragrantica Products")
        self.assertContains(response, "Vanilla Extasy Source")
        self.assertContains(response, "Fragrantica Collection")
        self.assertContains(response, "Women")
        self.assertContains(response, "2008")
        self.assertNotContains(response, "1 parsed supplier rows")
        self.assertNotContains(response, "1 linked supplier rows")

    def test_fragrantica_products_does_not_show_supplier_missing_comparison(self):
        supplier = models.Supplier.objects.create(name="Antonina")
        supplier_product = models.SupplierProduct.objects.create(
            supplier=supplier,
            name="MONTALE Missing Scent edp 100 ml",
            brand="Montale",
            size="100 ml",
        )
        ParsedSupplierProduct.objects.create(
            supplier_product=supplier_product,
            raw_name=supplier_product.name,
            normalized_text="montale missing scent edp 100 ml",
            normalized_brand=self.perfume.brand,
            product_name_text="Missing Scent",
            collection_name="Classic",
            concentration="Eau de Parfum",
            size_ml="100.00",
            confidence=95,
        )

        response = self.client.get(
            reverse("prices:fragrantica_product_review"),
            {"brand": "Montale"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response, "Supplier products not found in Fragrantica catalogue"
        )
        self.assertNotContains(response, "Missing Scent")

    def test_fragrantica_products_shows_staged_source_rows_with_match_action(self):
        FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy Source",
            normalized_name="vanilla extasy source",
            collection_name="Fragrantica Collection",
            audience="Women",
            release_year=2008,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )
        matched_source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            collection_name="Fragrantica Collection",
            audience="Women",
            release_year=2008,
            source_path="/perfume/Montale/Vanilla-Extasy-2.html",
        )

        response = self.client.get(reverse("prices:fragrantica_product_review"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fragrantica Collection")
        self.assertContains(response, "Women")
        self.assertContains(response, "2008")
        self.assertContains(response, "Open Fragrantica")
        self.assertContains(response, "Suggested local matches")
        self.assertContains(response, "Link without leaving this page")
        self.assertContains(response, "Montale / Vanilla Extasy / Eau de Parfum")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[matched_source.pk]),
        )

    def test_fragrantica_products_suggests_audience_synonym_match(self):
        amouage = Brand.objects.create(name="Amouage")
        perfume = Perfume.objects.create(
            brand=amouage,
            name="Beach Hut for Men",
            audience="Men",
            concentration="Eau de Parfum",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Amouage",
            normalized_brand_name="amouage",
            name="Beach Hut Man",
            normalized_name="beach hut man",
            audience="Men",
            release_year=2017,
            source_path="/perfume/Amouage/Beach-Hut-Man-500.html",
        )

        response = self.client.get(
            reverse("prices:fragrantica_product_review"),
            {"brand": "Amouage", "q": "beach hut"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Amouage / Beach Hut for Men / Eau de Parfum",
        )
        self.assertContains(response, "Exact brand and scent identity match")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )
        self.assertNotEqual(perfume.pk, self.perfume.pk)

    def test_fragrantica_products_uses_product_alias_knowledge_for_suggestion(self):
        alexandre = Brand.objects.create(name="Alexandre J.")
        perfume = Perfume.objects.create(
            brand=alexandre,
            name="St Honore",
            audience="Women",
            concentration="Eau de Parfum",
        )
        ProductAlias.objects.create(
            perfume=perfume,
            brand=alexandre,
            alias_text="Saint Honore",
            canonical_text="St Honore",
            active=True,
        )
        source = FragranticaProduct.objects.create(
            brand_name="Alexandre J.",
            normalized_brand_name="alexandre j.",
            name="Saint Honore",
            normalized_name="saint honore",
            audience="Women",
            source_path="/perfume/Alexandre-J/Saint-Honore-1.html",
        )

        response = self.client.get(
            reverse("prices:fragrantica_product_review"),
            {"brand": "Alexandre J.", "q": "saint honore"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alexandre J. / St Honore")
        self.assertContains(response, "Matched by product alias knowledge")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )

    def test_fragrantica_products_uses_product_alias_with_concentration_words(self):
        alexandre = Brand.objects.create(name="Alexandre J.")
        perfume = Perfume.objects.create(
            brand=alexandre,
            name="St Honore",
            audience="Women",
            concentration="Eau de Parfum",
        )
        ProductAlias.objects.create(
            perfume=perfume,
            brand=alexandre,
            alias_text="Saint Honore",
            canonical_text="St Honore",
            active=True,
        )
        source = FragranticaProduct.objects.create(
            brand_name="Alexandre J.",
            normalized_brand_name="alexandre j.",
            name="Saint Honore Eau de Parfum",
            normalized_name="saint honore eau de parfum",
            audience="Women",
            source_path="/perfume/Alexandre-J/Saint-Honore-2.html",
        )

        response = self.client.get(
            reverse("prices:fragrantica_product_review"),
            {"brand": "Alexandre J.", "q": "saint honore"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alexandre J. / St Honore")
        self.assertContains(response, "Matched by product alias knowledge")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )

    def test_fragrantica_products_uses_brand_alias_knowledge_for_suggestion(self):
        alexandre = Brand.objects.create(name="Alexandre J.")
        Perfume.objects.create(
            brand=alexandre,
            name="Legacy WB",
            audience="Women",
            concentration="Eau de Parfum",
        )
        BrandAlias.objects.create(
            brand=alexandre,
            alias_text="Alexandre.J",
            normalized_alias="alexandre.j",
            active=True,
        )
        source = FragranticaProduct.objects.create(
            brand_name="Alexandre.J",
            normalized_brand_name="alexandre.j",
            name="Legacy WB",
            normalized_name="legacy wb",
            audience="Women",
            source_path="/perfume/Alexandre-J/Legacy-WB-1.html",
        )

        response = self.client.get(
            reverse("prices:fragrantica_product_review"),
            {"brand": "Alexandre.J", "q": "legacy"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alexandre J. / Legacy WB")
        self.assertContains(response, "Exact brand and scent identity match")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )

    def test_fragrantica_products_suggests_ampersand_brand_and_concentration_title(
        self,
    ):
        dolce = Brand.objects.create(name="Dolce & Gabbana")
        perfume = Perfume.objects.create(
            brand=dolce,
            name="Light Blue",
            audience="Women",
            concentration="Eau de Toilette",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Dolce&Gabbana",
            normalized_brand_name="dolceandgabbana",
            name="Light Blue Eau de Toilette",
            normalized_name="light blue eau de toilette",
            collection_name="LIGHT BLUE BY DOLCE&GABBANA",
            audience="Women",
            release_year=2025,
            source_path="/perfume/Dolce-Gabbana/Light-Blue-Eau-de-Toilette-1.html",
        )

        response = self.client.get(
            reverse("prices:fragrantica_product_review"),
            {"brand": "Dolce&Gabbana", "q": "light blue"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dolce &amp; Gabbana / Light Blue")
        self.assertContains(response, "Exact brand, scent, and concentration match")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )
        self.assertNotEqual(perfume.pk, self.perfume.pk)

    def test_fragrantica_products_suggests_extrait_title_for_extrait_variant(self):
        brand = Brand.objects.create(name="Matiere Premiere")
        Perfume.objects.create(
            brand=brand,
            name="Crystal Saffron",
            concentration="Eau de Parfum",
        )
        extrait = Perfume.objects.create(
            brand=brand,
            name="Crystal Saffron",
            concentration="Extrait de Parfum",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Matiere Premiere",
            normalized_brand_name="matiere premiere",
            name="Crystal Saffron Extrait",
            normalized_name="crystal saffron extrait",
            collection_name="Extrait de Parfum",
            audience="Unisex",
            release_year=2024,
            source_path="/perfume/Matiere-Premiere/Crystal-Saffron-Extrait.html",
        )

        response = self.client.get(
            reverse("prices:fragrantica_product_review"),
            {"brand": "Matiere Premiere", "q": "crystal saffron extrait"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Matiere Premiere / Crystal Saffron / Extrait de Parfum",
        )
        self.assertContains(response, "Exact brand, scent, and concentration match")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )
        self.assertNotEqual(extrait.pk, self.perfume.pk)

    def test_fragrantica_products_suggests_audience_suffix_and_concentration_title(
        self,
    ):
        dolce = Brand.objects.create(name="Dolce & Gabbana")
        perfume = Perfume.objects.create(
            brand=dolce,
            name="Light Blue Capri In Love Pour Femme",
            audience="Women",
            concentration="Eau de Parfum",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Dolce&Gabbana",
            normalized_brand_name="dolceandgabbana",
            name="Light Blue Capri In Love Eau de Parfum",
            normalized_name="light blue capri in love eau de parfum",
            collection_name="LIGHT BLUE BY DOLCE&GABBANA",
            audience="Women",
            release_year=2025,
            source_path="/perfume/Dolce-Gabbana/Light-Blue-Capri-In-Love-1.html",
        )

        response = self.client.get(
            reverse("prices:fragrantica_product_review"),
            {"brand": "Dolce&Gabbana", "q": "capri in love"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "Dolce &amp; Gabbana / Light Blue Capri In Love Pour Femme"
        )
        self.assertContains(response, "Exact brand, scent, and concentration match")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )
        self.assertNotEqual(perfume.pk, self.perfume.pk)

    def test_fragrantica_products_suggests_pour_homme_with_concentration_title(
        self,
    ):
        dolce = Brand.objects.create(name="Dolce & Gabbana")
        generic_perfume = Perfume.objects.create(
            brand=dolce,
            name="Light Blue",
            audience="Women",
            concentration="Eau de Toilette",
        )
        perfume = Perfume.objects.create(
            brand=dolce,
            name="Light Blue Pour Homme",
            audience="Men",
            concentration="Eau de Toilette",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Dolce&Gabbana",
            normalized_brand_name="dolceandgabbana",
            name="Light Blue Pour Homme Eau de Toilette",
            normalized_name="light blue pour homme eau de toilette",
            collection_name="LIGHT BLUE BY DOLCE&GABBANA",
            audience="Men",
            release_year=2025,
            source_path="/perfume/Dolce-Gabbana/Light-Blue-Pour-Homme-1.html",
        )

        response = self.client.get(
            reverse("prices:fragrantica_product_review"),
            {"brand": "Dolce&Gabbana", "q": "light blue pour homme"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dolce &amp; Gabbana / Light Blue Pour Homme")
        self.assertContains(response, "Exact brand, scent, and concentration match")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )
        self.assertNotEqual(perfume.pk, self.perfume.pk)
        self.assertNotEqual(perfume.pk, generic_perfume.pk)

    def test_fragrantica_suggestions_reject_opposite_audience_identity(self):
        from prices.services.catalog_review import (
            build_fragrantica_candidate_choices,
            build_fragrantica_candidates_for_perfume,
        )

        gucci = Brand.objects.create(name="Gucci")
        femme = Perfume.objects.create(
            brand=gucci,
            name="Guilty Pour Femme",
            audience="Women",
            concentration="Eau de Toilette",
        )
        homme = Perfume.objects.create(
            brand=gucci,
            name="Guilty Pour Homme",
            audience="Men",
            concentration="Eau de Toilette",
        )
        femme_source = FragranticaProduct.objects.create(
            brand_name="Gucci",
            normalized_brand_name="gucci",
            name="Guilty Pour Femme",
            normalized_name="guilty pour femme",
            audience="Women",
            source_path="/perfume/Gucci/Guilty-Pour-Femme-1.html",
        )
        homme_source = FragranticaProduct.objects.create(
            brand_name="Gucci",
            normalized_brand_name="gucci",
            name="Guilty Pour Homme",
            normalized_name="guilty pour homme",
            audience="Men",
            source_path="/perfume/Gucci/Guilty-Pour-Homme-1.html",
        )

        self.assertEqual(
            build_fragrantica_candidates_for_perfume(femme, [homme_source], []),
            [],
        )
        femme_candidates = build_fragrantica_candidates_for_perfume(
            femme,
            [femme_source, homme_source],
            [],
        )
        self.assertEqual(femme_candidates[0].source, femme_source)

        choices = build_fragrantica_candidate_choices(
            [femme_source],
            perfume_manager=Perfume.objects.filter(brand=gucci),
        )

        self.assertEqual(choices[femme_source.pk][0].perfume, femme)
        self.assertNotIn(
            homme,
            [candidate.perfume for candidate in choices[femme_source.pk]],
        )

    def test_fragrantica_review_suggestions_score_only_resolved_source_brand(self):
        from prices.services.catalog_review import build_fragrantica_candidate_choices

        montale = Brand.objects.create(name="Performance Brand A")
        other_brand = Brand.objects.create(name="Performance Other A")
        montale_perfume = Perfume.objects.create(
            brand=montale,
            name="Vanilla Extasy",
            concentration="Eau de Parfum",
        )
        other_perfume = Perfume.objects.create(
            brand=other_brand,
            name="Vanilla Extasy",
            concentration="Eau de Parfum",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Performance Brand A",
            normalized_brand_name="performance brand a",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            source_path="/perfume/Performance-Brand-A/Vanilla-Extasy-1.html",
        )

        with patch(
            "prices.services.catalog_review._fragrantica_perfume_candidate_score",
            return_value=(100, "Mock match"),
        ) as scorer:
            choices = build_fragrantica_candidate_choices([source])

        self.assertEqual(choices[source.pk][0].perfume, montale_perfume)
        scored_perfumes = [call_args.args[1] for call_args in scorer.call_args_list]
        self.assertEqual(scored_perfumes, [montale_perfume])
        self.assertNotIn(other_perfume, scored_perfumes)

    def test_fragrantica_products_treats_et_and_ampersand_as_identity_connectors(
        self,
    ):
        brand = Brand.objects.create(name="100 Bon")
        perfume = Perfume.objects.create(
            brand=brand,
            name="Ambre Et Tonka",
            concentration="Eau de Parfum",
            release_year=2023,
        )
        source = FragranticaProduct.objects.create(
            brand_name="100 Bon",
            normalized_brand_name="100 bon",
            name="Ambre & Tonka",
            normalized_name="ambre & tonka",
            collection_name="L'Atelier",
            release_year=2023,
            source_path="/perfume/100-Bon/Ambre-Tonka-1.html",
        )

        response = self.client.get(
            reverse("prices:fragrantica_product_review"),
            {"brand": "100 Bon", "q": "ambre"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "100 Bon / Ambre Et Tonka")
        self.assertContains(response, "Exact brand and scent identity match")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )
        self.assertNotEqual(perfume.pk, self.perfume.pk)

    def test_fragrantica_products_strips_repeated_brand_prefix_from_scent(self):
        brand = Brand.objects.create(name="Acqua di Parma")
        perfume = Perfume.objects.create(
            brand=brand,
            name="Bergamotto di Calabria",
            concentration="Eau de Toilette",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Acqua di Parma",
            normalized_brand_name="acqua di parma",
            name="Acqua di Parma Blu Mediterraneo Bergamotto di Calabria",
            normalized_name="acqua di parma blu mediterraneo bergamotto di calabria",
            collection_name="BLU MEDITERRANEO",
            audience="Unisex",
            release_year=2010,
            source_path="/perfume/Acqua-di-Parma/Bergamotto-di-Calabria-1.html",
        )

        source.refresh_from_db()
        response = self.client.get(
            reverse("prices:fragrantica_product_review"),
            {"brand": "Acqua di Parma", "q": "Acqua di Parma Bergamotto di Calabria"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            source.normalized_name,
            "blu mediterraneo bergamotto di calabria",
        )
        self.assertContains(response, "Blu Mediterraneo Bergamotto di Calabria")
        self.assertNotContains(
            response,
            "Acqua di Parma Blu Mediterraneo Bergamotto di Calabria",
        )
        self.assertContains(response, "Acqua di Parma / Bergamotto di Calabria")
        self.assertContains(response, "Exact brand and scent identity match")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )
        response = self.client.get(
            reverse("prices:fragrantica_product_review"),
            {"q": "Acqua di Parma BLU MEDITERRANEO Unisex 2010 Bergamotto"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Blu Mediterraneo Bergamotto di Calabria")
        self.assertContains(response, "Exact brand and scent identity match")
        self.assertNotEqual(perfume.pk, self.perfume.pk)

    def test_catalogue_linking_matches_brand_prefixed_fragrantica_scent(self):
        brand = Brand.objects.create(name="Acqua di Parma")
        perfume = Perfume.objects.create(
            brand=brand,
            name="Bergamotto di Calabria",
            concentration="Eau de Toilette",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Acqua di Parma",
            normalized_brand_name="acqua di parma",
            name="Acqua di Parma Blu Mediterraneo Bergamotto di Calabria",
            normalized_name="acqua di parma blu mediterraneo bergamotto di calabria",
            collection_name="BLU MEDITERRANEO",
            audience="Unisex",
            release_year=2010,
            source_path="/perfume/Acqua-di-Parma/Bergamotto-di-Calabria-1.html",
        )

        response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {"q": "Bergamotto di Calabria", "suggestions": "with"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Acqua di Parma / Bergamotto di Calabria / Eau de Toilette",
        )
        self.assertContains(response, "Acqua di Parma / Blu Mediterraneo Bergamotto")
        self.assertNotContains(
            response,
            "Acqua di Parma / Acqua di Parma Blu Mediterraneo",
        )
        self.assertContains(response, "Exact brand and scent identity match")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )
        self.assertNotEqual(perfume.pk, self.perfume.pk)

    def test_catalogue_linking_workbench_uses_same_et_ampersand_identity_logic(self):
        brand = Brand.objects.create(name="100 Bon")
        perfume = Perfume.objects.create(
            brand=brand,
            name="Ambre Et Tonka",
            concentration="Eau de Parfum",
            release_year=2023,
        )
        source = FragranticaProduct.objects.create(
            brand_name="100 Bon",
            normalized_brand_name="100 bon",
            name="Ambre & Tonka",
            normalized_name="ambre & tonka",
            collection_name="L'Atelier",
            release_year=2023,
            source_path="/perfume/100-Bon/Ambre-Tonka-1.html",
        )

        response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {"q": "ambre tonka", "suggestions": "with"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "100 Bon / Ambre Et Tonka / Eau de Parfum")
        self.assertContains(response, "100 Bon / Ambre &amp; Tonka")
        self.assertContains(response, "L&#x27;Atelier")
        self.assertContains(response, "Exact brand and scent identity match")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )
        self.assertNotEqual(perfume.pk, self.perfume.pk)

    def test_catalogue_linking_workbench_matches_compact_ampersand_brand(self):
        brand = Brand.objects.create(name="Dolce & Gabbana")
        perfume = Perfume.objects.create(
            brand=brand,
            name="Light Blue",
            concentration="Eau de Toilette",
            audience="Women",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Dolce&Gabbana",
            normalized_brand_name="dolceandgabbana",
            name="Light Blue Eau de Toilette",
            normalized_name="light blue eau de toilette",
            collection_name="Light Blue",
            audience="Women",
            source_path="/perfume/Dolce-Gabbana/Light-Blue-Eau-de-Toilette.html",
        )

        response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {"q": "light blue", "suggestions": "with"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dolce &amp; Gabbana / Light Blue")
        self.assertContains(response, "Dolce&amp;Gabbana / Light Blue Eau de Toilette")
        self.assertContains(response, "Exact brand, scent, and concentration match")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )
        self.assertNotEqual(perfume.pk, self.perfume.pk)

    def test_catalogue_linking_workbench_uses_brand_alias_for_fragrantica_brand(self):
        brand = Brand.objects.create(name="Dolce & Gabbana")
        BrandAlias.objects.create(
            brand=brand,
            alias_text="D&G",
            normalized_alias="d&g",
            active=True,
        )
        perfume = Perfume.objects.create(
            brand=brand,
            name="Light Blue",
            concentration="Eau de Toilette",
            audience="Women",
        )
        source = FragranticaProduct.objects.create(
            brand_name="D&G",
            normalized_brand_name="dandg",
            name="Light Blue Eau de Toilette",
            normalized_name="light blue eau de toilette",
            collection_name="Light Blue",
            audience="Women",
            source_path="/perfume/Dolce-Gabbana/Light-Blue-Eau-de-Toilette.html",
        )

        response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {"q": "light blue", "suggestions": "with"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dolce &amp; Gabbana / Light Blue")
        self.assertContains(response, "D&amp;G / Light Blue Eau de Toilette")
        self.assertContains(response, "Exact brand, scent, and concentration match")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )
        self.assertNotEqual(perfume.pk, self.perfume.pk)

    def test_catalogue_linking_workbench_default_threshold_keeps_useful_fuzzy_matches(
        self,
    ):
        brand = Brand.objects.create(name="100 Bon")
        Perfume.objects.create(
            brand=brand,
            name="Ambre Et Tonka",
            concentration="Eau de Parfum",
        )
        source = FragranticaProduct.objects.create(
            brand_name="100 Bon",
            normalized_brand_name="100 bon",
            name="Ambre Tonka",
            normalized_name="ambre tonka",
            source_path="/perfume/100-Bon/Ambre-Tonka-2.html",
        )

        default_response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {"q": "ambre tonka", "suggestions": "with"},
        )
        strict_response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {"q": "ambre tonka", "suggestions": "with", "min_score": "90"},
        )

        self.assertEqual(default_response.status_code, 200)
        self.assertEqual(strict_response.status_code, 200)
        self.assertContains(default_response, "Similar same-brand scent name")
        self.assertContains(default_response, "Ambre Tonka")
        self.assertContains(
            default_response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )
        self.assertNotContains(strict_response, "Similar same-brand scent name")

    def test_catalogue_linking_scores_only_resolved_source_brand(self):
        from prices.services.catalog_review import (
            build_catalogue_fragrantica_candidates_for_perfumes,
        )

        montale = Brand.objects.create(name="Performance Brand B")
        other_brand = Brand.objects.create(name="Performance Other B")
        montale_perfume = Perfume.objects.create(
            brand=montale,
            name="Vanilla Extasy",
            concentration="Eau de Parfum",
        )
        other_perfume = Perfume.objects.create(
            brand=other_brand,
            name="Vanilla Extasy",
            concentration="Eau de Parfum",
        )
        FragranticaProduct.objects.create(
            brand_name="Performance Brand B",
            normalized_brand_name="performance brand b",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            source_path="/perfume/Performance-Brand-B/Vanilla-Extasy-1.html",
        )

        with patch(
            "prices.services.catalog_review._fragrantica_perfume_candidate_score",
            return_value=(100, "Mock match"),
        ) as scorer:
            candidate_map = build_catalogue_fragrantica_candidates_for_perfumes(
                [montale_perfume, other_perfume],
                min_score=80,
            )

        self.assertEqual(candidate_map[montale_perfume.pk][0].score, 100)
        self.assertEqual(candidate_map[other_perfume.pk], [])
        scored_perfumes = [call_args.args[1] for call_args in scorer.call_args_list]
        self.assertEqual(scored_perfumes, [montale_perfume])

    def test_catalogue_linking_workbench_lists_two_column_suggestions(self):
        self.perfume.audience = "Women"
        self.perfume.release_year = 2008
        self.perfume.save(update_fields=["audience", "release_year", "updated_at"])
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            collection_name="Fragrantica Collection",
            audience="Women",
            release_year=2008,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )

        response = self.client.get(reverse("prices:catalogue_linking_workbench"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Link Our Products with Fragrantica")
        self.assertContains(response, "Left column")
        self.assertContains(response, "Right column")
        self.assertContains(response, "Search Fragrantica")
        self.assertContains(
            response,
            reverse("prices:catalogue_linking_fragrantica_search"),
        )
        self.assertContains(response, "Bulk link checked")
        self.assertContains(response, "data-catalogue-selection-root")
        self.assertContains(response, "data-catalogue-select-toggle")
        self.assertContains(response, "data-catalogue-select-checkbox")
        self.assertContains(response, "data-catalogue-bulk-primary")
        self.assertContains(response, "Montale / Vanilla Extasy / Eau de Parfum")
        self.assertContains(response, "Classic")
        self.assertContains(response, "Audience")
        self.assertContains(response, "Women")
        self.assertContains(response, "Year")
        self.assertContains(response, "2008")
        self.assertContains(response, "Montale / Vanilla Extasy")
        self.assertContains(response, "Select row to load Fragrantica suggestions")
        self.assertNotContains(response, "Fragrantica Collection")
        self.assertNotContains(response, "Exact brand and scent identity match")
        self.assertNotContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )
        self.assertContains(response, reverse("prices:catalogue_linking_candidates"))

    def test_catalogue_linking_queryset_filters_link_status_without_global_counts(self):
        from prices.services.catalog_review import (
            build_catalogue_linking_perfume_queryset,
        )

        linked_source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
            match_status=FragranticaProduct.STATUS_LINKED,
            matched_perfume=self.perfume,
        )
        extra_perfume = Perfume.objects.create(
            brand=self.perfume.brand,
            name="Vanilla Extasy Rare",
            concentration="Eau de Parfum",
        )
        FragranticaProductLink.objects.create(
            source=linked_source,
            perfume=extra_perfume,
        )
        unlinked_perfume = Perfume.objects.create(
            brand=self.perfume.brand,
            name="Quiet Scent",
            concentration="Eau de Parfum",
        )

        linked_request = RequestFactory().get(
            reverse("prices:catalogue_linking_workbench"),
            {"status": "linked"},
        )
        linked_queryset = build_catalogue_linking_perfume_queryset(linked_request)
        linked_ids = set(linked_queryset.values_list("pk", flat=True))

        unlinked_request = RequestFactory().get(
            reverse("prices:catalogue_linking_workbench"),
            {"status": "unlinked"},
        )
        unlinked_queryset = build_catalogue_linking_perfume_queryset(unlinked_request)
        unlinked_ids = set(unlinked_queryset.values_list("pk", flat=True))

        self.assertIn(self.perfume.pk, linked_ids)
        self.assertIn(extra_perfume.pk, linked_ids)
        self.assertNotIn(unlinked_perfume.pk, linked_ids)
        self.assertIn(unlinked_perfume.pk, unlinked_ids)
        self.assertNotIn("COUNT(", str(linked_queryset.query).upper())

    def test_catalogue_linking_workbench_filters_visible_rows_by_suggestion(self):
        FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy Eau de Parfum",
            normalized_name="vanilla extasy eau de parfum",
            audience="Women",
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )
        Perfume.objects.create(
            brand=self.perfume.brand,
            name="Quiet Scent",
            concentration="Eau de Parfum",
        )

        with_suggestion = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {"suggestions": "with"},
        )
        without_suggestion = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {"suggestions": "without"},
        )

        self.assertEqual(with_suggestion.status_code, 200)
        self.assertContains(with_suggestion, 'name="suggestions"')
        self.assertContains(
            with_suggestion,
            "Montale / Vanilla Extasy / Eau de Parfum",
        )
        self.assertNotContains(with_suggestion, "Montale / Quiet Scent")
        self.assertContains(with_suggestion, "1 shown / 2 visible")

        self.assertEqual(without_suggestion.status_code, 200)
        self.assertContains(without_suggestion, "Montale / Quiet Scent")
        self.assertNotContains(
            without_suggestion,
            "Montale / Vanilla Extasy / Eau de Parfum",
        )
        self.assertContains(without_suggestion, "1 shown / 2 visible")

    def test_catalogue_linking_workbench_refills_filtered_page_after_bulk_links(self):
        brand = Brand.objects.create(name="Pagination Brand")
        for index in range(40):
            Perfume.objects.create(
                brand=brand,
                name=f"No Suggestion {index:02d}",
                concentration="Eau de Parfum",
            )
        for index in range(5):
            name = f"Ready Match {index:02d}"
            Perfume.objects.create(
                brand=brand,
                name=name,
                concentration="Eau de Parfum",
            )
            FragranticaProduct.objects.create(
                brand_name="Pagination Brand",
                normalized_brand_name="pagination brand",
                name=f"{name} Eau de Parfum",
                normalized_name=f"{name.lower()} eau de parfum",
                source_path=f"/perfume/Pagination-Brand/Ready-Match-{index}.html",
            )

        response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {
                "brand": str(brand.pk),
                "status": "unlinked",
                "suggestions": "with",
                "confidence": "100",
                "page": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ready Match 00")
        self.assertContains(response, "5 shown / 5 visible")
        self.assertNotContains(response, "No Our Products rows match these filters.")
        self.assertNotContains(response, "No Suggestion 00")

    def test_catalogue_linking_strict_filter_uses_exact_source_name_prefilter(self):
        brand = Brand.objects.create(name="Strict Filter Brand")
        perfume = Perfume.objects.create(
            brand=brand,
            name="Ambre Et Tonka",
            concentration="Eau de Parfum",
        )
        collection_perfume = Perfume.objects.create(
            brand=brand,
            name="Arancia di Capri",
            concentration="Eau de Toilette",
            collection_name="Blu Mediterraneo",
        )
        quiet = Perfume.objects.create(
            brand=brand,
            name="Quiet Scent",
            concentration="Eau de Parfum",
        )
        FragranticaProduct.objects.create(
            brand_name="Strict Filter Brand",
            name="Ambre Et Tonka Eau de Parfum",
            source_path="/perfume/Strict-Filter-Brand/Ambre-Et-Tonka.html",
        )
        FragranticaProduct.objects.create(
            brand_name="Strict Filter Brand",
            name="Blu Mediterraneo Arancia di Capri Eau de Toilette",
            collection_name="Blu Mediterraneo",
            source_path="/perfume/Strict-Filter-Brand/Arancia-di-Capri.html",
        )
        FragranticaProduct.objects.create(
            brand_name="Strict Filter Brand",
            name="Unrelated Eau de Parfum",
            source_path="/perfume/Strict-Filter-Brand/Unrelated.html",
        )

        response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {
                "brand": str(brand.pk),
                "status": "unlinked",
                "suggestions": "with",
                "confidence": "100",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, catalogue_linking_perfume_label(perfume))
        self.assertContains(
            response,
            catalogue_linking_perfume_label(collection_perfume),
        )
        self.assertNotContains(response, catalogue_linking_perfume_label(quiet))
        self.assertContains(response, "2 shown / 2 visible")

    def test_catalogue_linking_strict_filter_prefilters_exact_matches_before_pagination(
        self,
    ):
        brand = Brand.objects.create(name="Prefilter Strict Brand")
        for index in range(45):
            Perfume.objects.create(
                brand=brand,
                name=f"Quiet Scent {index:02d}",
                concentration="Eau de Parfum",
            )
        for index in range(20):
            name = f"Base Only Scent {index:02d}"
            Perfume.objects.create(
                brand=brand,
                name=name,
                concentration="Eau de Parfum",
            )
            FragranticaProduct.objects.create(
                brand_name="Prefilter Strict Brand",
                normalized_brand_name="prefilter strict brand",
                name=name,
                normalized_name=name.lower(),
                source_path=f"/perfume/Prefilter-Strict-Brand/Base-Only-{index}.html",
            )
        for index in range(35):
            name = f"Ready Scent {index:02d}"
            Perfume.objects.create(
                brand=brand,
                name=name,
                concentration="Eau de Parfum",
            )
            FragranticaProduct.objects.create(
                brand_name="Prefilter Strict Brand",
                normalized_brand_name="prefilter strict brand",
                name=f"{name} Eau de Parfum",
                normalized_name=f"{name.lower()} eau de parfum",
                source_path=f"/perfume/Prefilter-Strict-Brand/Ready-Scent-{index}.html",
            )

        response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {
                "brand": str(brand.pk),
                "status": "unlinked",
                "suggestions": "with",
                "confidence": "100",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ready Scent 00")
        self.assertContains(response, "Base Only Scent 00")
        self.assertContains(response, "40 shown / 55 visible")
        self.assertNotContains(response, "Quiet Scent 00")

        page_two_response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {
                "brand": str(brand.pk),
                "status": "unlinked",
                "suggestions": "with",
                "confidence": "100",
                "page": "2",
            },
        )

        self.assertEqual(page_two_response.status_code, 200)
        self.assertContains(page_two_response, "Ready Scent 34")

    def test_catalogue_linking_strict_filter_recounts_blank_stale_pages(self):
        brand = Brand.objects.create(name="Stale Strict Brand")
        for index in range(45):
            Perfume.objects.create(
                brand=brand,
                name="Shared Scent",
                concentration=f"Edition {index:02d}",
            )
        FragranticaProduct.objects.create(
            brand_name="Stale Strict Brand",
            normalized_brand_name="stale strict brand",
            name="Shared Scent",
            normalized_name="shared scent",
            source_path="/perfume/Stale-Strict-Brand/Shared-Scent.html",
        )

        response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {
                "brand": str(brand.pk),
                "status": "unlinked",
                "suggestions": "with",
                "confidence": "100",
                "page": "2",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "0 shown / 0 visible")
        self.assertNotContains(response, "Page 2 of")
        self.assertContains(response, "No Our Products rows match these filters.")

    def test_catalogue_linking_strict_filter_pages_verified_rows_not_prefilter_rows(
        self,
    ):
        brand = Brand.objects.create(name="Sparse Strict Brand")
        for index in range(40):
            Perfume.objects.create(
                brand=brand,
                name="A Shared Conflict",
                concentration=f"Edition {index:02d}",
            )
        FragranticaProduct.objects.create(
            brand_name="Sparse Strict Brand",
            normalized_brand_name="sparse strict brand",
            name="A Shared Conflict",
            normalized_name="a shared conflict",
            source_path="/perfume/Sparse-Strict-Brand/Shared-Conflict.html",
        )
        for index in range(2):
            name = f"Z Ready Match {index:02d}"
            Perfume.objects.create(
                brand=brand,
                name=name,
                concentration="Eau de Parfum",
            )
            FragranticaProduct.objects.create(
                brand_name="Sparse Strict Brand",
                normalized_brand_name="sparse strict brand",
                name=f"{name} Eau de Parfum",
                normalized_name=f"{name.lower()} eau de parfum",
                source_path=f"/perfume/Sparse-Strict-Brand/Ready-{index}.html",
            )

        response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {
                "brand": str(brand.pk),
                "status": "unlinked",
                "suggestions": "with",
                "confidence": "100",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Z Ready Match 00")
        self.assertContains(response, "Z Ready Match 01")
        self.assertNotContains(response, "A Shared Conflict")
        self.assertContains(response, "2 shown / 2 visible")

    def test_catalogue_linking_workbench_filters_visible_rows_by_confidence(self):
        FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy Eau de Parfum",
            normalized_name="vanilla extasy eau de parfum",
            audience="Women",
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )
        quiet = Perfume.objects.create(
            brand=self.perfume.brand,
            name="Quiet Scent",
            concentration="Eau de Parfum",
        )
        fuzzy_brand = Brand.objects.create(name="100 Bon")
        fuzzy_perfume = Perfume.objects.create(
            brand=fuzzy_brand,
            name="Ambre Et Tonka",
            concentration="Eau de Parfum",
        )
        FragranticaProduct.objects.create(
            brand_name="100 Bon",
            normalized_brand_name="100 bon",
            name="Ambre Tonka",
            normalized_name="ambre tonka",
            source_path="/perfume/100-Bon/Ambre-Tonka-2.html",
        )

        exact_response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {"confidence": "100"},
        )
        lower_response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {"confidence": "80"},
        )

        self.assertEqual(exact_response.status_code, 200)
        self.assertContains(exact_response, 'name="confidence"')
        self.assertContains(
            exact_response,
            "Montale / Vanilla Extasy / Eau de Parfum",
        )
        self.assertNotContains(exact_response, quiet.name)
        self.assertNotContains(exact_response, fuzzy_perfume.name)
        self.assertContains(exact_response, "1 shown / 1 visible")
        self.assertContains(exact_response, "data-linking-payload=")
        self.assertContains(exact_response, "&quot;candidates&quot;")
        self.assertContains(exact_response, "&quot;score&quot;:100")

        self.assertEqual(lower_response.status_code, 200)
        self.assertContains(lower_response, "Montale / Vanilla Extasy")
        self.assertContains(lower_response, "100 Bon / Ambre Et Tonka")
        self.assertNotContains(lower_response, quiet.name)
        self.assertContains(lower_response, "2 shown / 3 visible")

    def test_catalogue_linking_high_confidence_page_does_not_verify_all_rows(self):
        brand = Brand.objects.create(name="Bounded High Confidence Brand")
        for index in range(50):
            name = f"Bounded Match {index:02d}"
            Perfume.objects.create(
                brand=brand,
                name=name,
                concentration="Eau de Parfum",
            )
            FragranticaProduct.objects.create(
                brand_name="Bounded High Confidence Brand",
                normalized_brand_name="bounded high confidence brand",
                name=f"{name} Eau de Parfum",
                normalized_name=f"{name.lower()} eau de parfum",
                source_path=(
                    f"/perfume/Bounded-High-Confidence-Brand/Bounded-{index}.html"
                ),
            )

        with (
            patch(
                "prices.services.catalog_review._catalogue_linking_strict_exact_perfume_ids",
                side_effect=AssertionError("95+ should not prefilter every row"),
            ),
            patch(
                "prices.services.catalog_review._catalogue_linking_verified_filtered_perfume_ids",
                side_effect=AssertionError("95+ should not verify every filtered row"),
            ),
        ):
            response = self.client.get(
                reverse("prices:catalogue_linking_workbench"),
                {
                    "status": "unlinked",
                    "suggestions": "with",
                    "confidence": "95",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bounded Match 00")

    def test_catalogue_linking_high_confidence_pages_fill_from_filtered_matches(self):
        brand = Brand.objects.create(name="A Windowed Linking Brand")
        for index in range(8):
            name = f"Windowed Row {index:03d}"
            Perfume.objects.create(
                brand=brand,
                name=name,
                concentration="Eau de Parfum",
            )
            if index in {0, 1, 6, 7}:
                FragranticaProduct.objects.create(
                    brand_name="A Windowed Linking Brand",
                    normalized_brand_name="a windowed linking brand",
                    name=f"{name} Eau de Parfum",
                    normalized_name=f"{name.lower()} eau de parfum",
                    source_path=(
                        f"/perfume/A-Windowed-Linking-Brand/Windowed-{index}.html"
                    ),
                )

        with (
            patch(
                "prices.views_our_products.CatalogueLinkingWorkbenchView.paginate_by",
                2,
            ),
            patch(
                "prices.services.catalog_review.CATALOGUE_LINKING_FILTER_SCAN_PAGES",
                4,
            ),
        ):
            page_one = self.client.get(
                reverse("prices:catalogue_linking_workbench"),
                {
                    "status": "unlinked",
                    "suggestions": "with",
                    "confidence": "95",
                    "page": "1",
                },
            )
            page_two = self.client.get(
                reverse("prices:catalogue_linking_workbench"),
                {
                    "status": "unlinked",
                    "suggestions": "with",
                    "confidence": "95",
                    "page": "2",
                },
            )

        self.assertEqual(page_one.status_code, 200)
        self.assertEqual(page_two.status_code, 200)
        self.assertContains(page_one, "Windowed Row 000")
        self.assertContains(page_one, "Windowed Row 001")
        self.assertNotContains(page_one, "Windowed Row 006")
        self.assertContains(page_two, "Windowed Row 006")
        self.assertContains(page_two, "Windowed Row 007")
        self.assertNotContains(page_two, "Windowed Row 000")

    def test_catalogue_linking_review_page_does_not_verify_all_rows(self):
        brand = Brand.objects.create(name="Bounded Review Brand")
        Perfume.objects.create(
            brand=brand,
            name="Shared Bounded Mirage",
            concentration="Eau de Parfum",
        )
        Perfume.objects.create(
            brand=brand,
            name="Shared Bounded Mirage",
            concentration="Eau de Toilette",
        )
        FragranticaProduct.objects.create(
            brand_name="Bounded Review Brand",
            normalized_brand_name="bounded review brand",
            name="Shared Bounded Mirage",
            normalized_name="shared bounded mirage",
            source_path="/perfume/Bounded-Review-Brand/Shared-Bounded-Mirage.html",
        )

        with (
            patch(
                "prices.services.catalog_review._catalogue_linking_strict_exact_perfume_ids",
                side_effect=AssertionError(
                    "Needs review should not prefilter every row"
                ),
            ),
            patch(
                "prices.services.catalog_review._catalogue_linking_verified_filtered_perfume_ids",
                side_effect=AssertionError("Needs review should not verify every row"),
            ),
        ):
            response = self.client.get(
                reverse("prices:catalogue_linking_workbench"),
                {
                    "status": "unlinked",
                    "suggestions": "with",
                    "confidence": "review",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shared Bounded Mirage")

    def test_catalogue_linking_workbench_filters_manual_review_separately(self):
        brand = Brand.objects.create(name="Manual Review Brand")
        Perfume.objects.create(
            brand=brand,
            name="Shared Mirage",
            concentration="Eau de Parfum",
        )
        Perfume.objects.create(
            brand=brand,
            name="Shared Mirage",
            concentration="Eau de Toilette",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Manual Review Brand",
            normalized_brand_name="manual review brand",
            name="Shared Mirage",
            normalized_name="shared mirage",
            source_path="/perfume/Manual-Review-Brand/Shared-Mirage.html",
        )
        for index in range(45):
            Perfume.objects.create(
                brand=brand,
                name=f"Quiet Review Scent {index:02d}",
                concentration="Eau de Parfum",
            )

        exact_response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {
                "brand": str(brand.pk),
                "suggestions": "with",
                "confidence": "100",
            },
        )
        review_response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {
                "brand": str(brand.pk),
                "suggestions": "with",
                "confidence": "review",
            },
        )

        self.assertEqual(exact_response.status_code, 200)
        self.assertNotContains(exact_response, "Shared Mirage")

        self.assertEqual(review_response.status_code, 200)
        self.assertContains(review_response, "Needs review")
        self.assertContains(review_response, "Shared Mirage")
        self.assertNotContains(review_response, "Quiet Review Scent")
        self.assertContains(review_response, "2 shown / 2 visible")
        self.assertContains(
            review_response,
            "Manual review: same Fragrantica row is an equal top match",
        )
        self.assertContains(
            review_response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )
        self.assertContains(
            review_response,
            "data-fragrantica-link-submit>Link</button>",
        )
        self.assertNotContains(review_response, "Review manually")

    def test_catalogue_linking_review_filter_includes_linked_source_conflicts(self):
        brand = Brand.objects.create(name="Linked Review Brand")
        primary = Perfume.objects.create(
            brand=brand,
            name="Already Linked Mirage",
            concentration="Eau de Parfum",
        )
        Perfume.objects.create(
            brand=brand,
            name="Already Linked Mirage",
            concentration="Eau de Toilette",
        )
        FragranticaProduct.objects.create(
            brand_name="Linked Review Brand",
            normalized_brand_name="linked review brand",
            name="Already Linked Mirage",
            normalized_name="already linked mirage",
            match_status=FragranticaProduct.STATUS_LINKED,
            matched_perfume=primary,
            source_path="/perfume/Linked-Review-Brand/Already-Linked-Mirage.html",
        )

        response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {
                "brand": str(brand.pk),
                "status": "unlinked",
                "suggestions": "with",
                "confidence": "review",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Linked Review Brand / Already Linked Mirage / Eau de Toilette",
        )
        self.assertContains(
            response, "Manual review: Fragrantica row is already linked"
        )
        self.assertContains(response, "1 shown / 1 visible")

    def test_catalogue_linking_skips_generic_base_when_audience_siblings_exist(self):
        from prices.services.catalog_review import (
            build_catalogue_fragrantica_candidates_for_perfumes,
        )

        brand = Brand.objects.create(name="Abercrombie & Fitch")
        generic = Perfume.objects.create(
            brand=brand,
            name="Authentic",
            concentration="Eau de Parfum",
        )
        men = Perfume.objects.create(
            brand=brand,
            name="Authentic for Men",
            concentration="Eau de Toilette",
            audience="Men",
        )
        women = Perfume.objects.create(
            brand=brand,
            name="Authentic for Women",
            concentration="Eau de Parfum",
            audience="Women",
        )
        FragranticaProduct.objects.create(
            brand_name="Abercrombie & Fitch",
            normalized_brand_name="abercrombie and fitch",
            name="Authentic Man",
            normalized_name="authentic man",
            collection_name="Authentic",
            audience="Men",
            match_status=FragranticaProduct.STATUS_LINKED,
            matched_perfume=men,
            source_path="/perfume/Abercrombie-Fitch/Authentic-Man.html",
        )
        FragranticaProduct.objects.create(
            brand_name="Abercrombie & Fitch",
            normalized_brand_name="abercrombie and fitch",
            name="Authentic Woman",
            normalized_name="authentic woman",
            collection_name="Authentic",
            audience="Women",
            match_status=FragranticaProduct.STATUS_LINKED,
            matched_perfume=women,
            source_path="/perfume/Abercrombie-Fitch/Authentic-Woman.html",
        )

        candidate_map = build_catalogue_fragrantica_candidates_for_perfumes(
            [generic],
            min_score=0,
            limit=5,
        )

        self.assertEqual(candidate_map[generic.id], [])

    def test_catalogue_linking_candidate_endpoint_returns_fragrantica_matches(self):
        self.perfume.audience = "Women"
        self.perfume.release_year = 2008
        self.perfume.save(update_fields=["audience", "release_year", "updated_at"])
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy Eau de Parfum",
            normalized_name="vanilla extasy eau de parfum",
            collection_name="Fragrantica Collection",
            audience="Women",
            release_year=2008,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )

        response = self.client.get(
            reverse("prices:catalogue_linking_candidates"),
            {"perfume": self.perfume.pk, "min_score": "95"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected"]["id"], self.perfume.pk)
        self.assertEqual(payload["selected"]["audience"], "Women")
        self.assertEqual(payload["selected"]["release_year"], 2008)
        self.assertEqual(payload["candidates"][0]["source_id"], source.pk)
        self.assertEqual(payload["candidates"][0]["score"], 100)
        self.assertEqual(
            payload["candidates"][0]["reason"],
            "Exact brand, scent, and concentration match",
        )
        self.assertEqual(
            payload["candidates"][0]["link_url"],
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )

    def test_catalogue_linking_fragrantica_search_endpoint_returns_manual_results(self):
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Manual Search Vanilla",
            normalized_name="manual search vanilla",
            collection_name="Search Collection",
            audience="Women",
            release_year=2010,
            source_path="/perfume/Montale/Manual-Search-Vanilla.html",
        )

        response = self.client.get(
            reverse("prices:catalogue_linking_fragrantica_search"),
            {"perfume": self.perfume.pk, "q": "manual vanilla"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected"]["id"], self.perfume.pk)
        self.assertEqual(payload["results"][0]["source_id"], source.pk)
        self.assertEqual(payload["results"][0]["score"], None)
        self.assertEqual(
            payload["results"][0]["reason"],
            "Manual Fragrantica search result",
        )
        self.assertEqual(payload["results"][0]["collection"], "Search Collection")
        self.assertEqual(payload["results"][0]["audience"], "Women")
        self.assertEqual(payload["results"][0]["release_year"], 2010)
        self.assertEqual(
            payload["results"][0]["link_url"],
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )

    def test_catalogue_linking_fragrantica_search_marks_already_linked_results(self):
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            matched_perfume=self.perfume,
            match_status=FragranticaProduct.STATUS_LINKED,
            source_path="/perfume/Montale/Vanilla-Extasy.html",
        )

        response = self.client.get(
            reverse("prices:catalogue_linking_fragrantica_search"),
            {"perfume": self.perfume.pk, "q": "vanilla extasy"},
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()["results"][0]
        self.assertEqual(result["source_id"], source.pk)
        self.assertEqual(result["match_status"], FragranticaProduct.STATUS_LINKED)
        self.assertFalse(result["can_link"])

    def test_catalogue_linking_scores_explicit_concentration_match_above_conflict(self):
        brand = Brand.objects.create(name="Acca Kappa")
        perfume = Perfume.objects.create(
            brand=brand,
            name="1869",
            concentration="Eau de Parfum",
        )
        matching_source = FragranticaProduct.objects.create(
            brand_name="Acca Kappa",
            normalized_brand_name="acca kappa",
            name="1869 Eau de Parfum",
            normalized_name="1869 eau de parfum",
            audience="Men",
            source_path="/perfume/Acca-Kappa/1869-Eau-de-Parfum.html",
        )
        conflicting_source = FragranticaProduct.objects.create(
            brand_name="Acca Kappa",
            normalized_brand_name="acca kappa",
            name="1869 Eau de Cologne",
            normalized_name="1869 eau de cologne",
            audience="Men",
            release_year=2005,
            source_path="/perfume/Acca-Kappa/1869-Eau-de-Cologne.html",
        )

        response = self.client.get(
            reverse("prices:catalogue_linking_candidates"),
            {"perfume": perfume.pk, "min_score": "0"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        candidates = {
            candidate["source_id"]: candidate for candidate in payload["candidates"]
        }
        self.assertEqual(payload["candidates"][0]["source_id"], matching_source.pk)
        self.assertEqual(candidates[matching_source.pk]["score"], 100)
        self.assertEqual(
            candidates[matching_source.pk]["reason"],
            "Exact brand, scent, and concentration match",
        )
        self.assertEqual(candidates[conflicting_source.pk]["score"], 88)
        self.assertEqual(
            candidates[conflicting_source.pk]["reason"],
            "Same brand and scent; concentration differs",
        )

        exact_response = self.client.get(
            reverse("prices:catalogue_linking_candidates"),
            {"perfume": perfume.pk, "min_score": "100"},
        )

        self.assertEqual(exact_response.status_code, 200)
        exact_payload = exact_response.json()
        self.assertEqual(len(exact_payload["candidates"]), 1)
        self.assertEqual(
            exact_payload["candidates"][0]["source_id"],
            matching_source.pk,
        )

    def test_catalogue_linking_treats_extrait_as_extrait_de_parfum(self):
        brand = Brand.objects.create(name="Matiere Premiere")
        edp = Perfume.objects.create(
            brand=brand,
            name="Crystal Saffron",
            concentration="Eau de Parfum",
        )
        extrait = Perfume.objects.create(
            brand=brand,
            name="Crystal Saffron",
            concentration="Extrait de Parfum",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Matiere Premiere",
            normalized_brand_name="matiere premiere",
            name="Crystal Saffron Extrait",
            normalized_name="crystal saffron extrait",
            collection_name="Extrait de Parfum",
            audience="Unisex",
            release_year=2024,
            source_path="/perfume/Matiere-Premiere/Crystal-Saffron-Extrait.html",
        )

        extrait_response = self.client.get(
            reverse("prices:catalogue_linking_candidates"),
            {"perfume": extrait.pk, "min_score": "100"},
        )
        edp_response = self.client.get(
            reverse("prices:catalogue_linking_candidates"),
            {"perfume": edp.pk, "min_score": "0"},
        )

        self.assertEqual(extrait_response.status_code, 200)
        extrait_payload = extrait_response.json()
        self.assertEqual(len(extrait_payload["candidates"]), 1)
        self.assertEqual(extrait_payload["candidates"][0]["source_id"], source.pk)
        self.assertEqual(extrait_payload["candidates"][0]["score"], 100)
        self.assertEqual(
            extrait_payload["candidates"][0]["reason"],
            "Exact brand, scent, and concentration match",
        )

        self.assertEqual(edp_response.status_code, 200)
        edp_payload = edp_response.json()
        self.assertEqual(edp_payload["candidates"][0]["source_id"], source.pk)
        self.assertEqual(edp_payload["candidates"][0]["score"], 88)
        self.assertEqual(
            edp_payload["candidates"][0]["reason"],
            "Same brand and scent; concentration differs",
        )

    def test_catalogue_linking_treats_parfum_title_as_extrait_de_parfum(self):
        brand = Brand.objects.create(name="Chanel")
        edp = Perfume.objects.create(
            brand=brand,
            name="Coco Noir",
            concentration="Eau de Parfum",
            collection_name="Coco Noir",
        )
        extrait = Perfume.objects.create(
            brand=brand,
            name="Coco Noir",
            concentration="Extrait de Parfum",
            collection_name="Coco Noir",
        )
        generic_source = FragranticaProduct.objects.create(
            brand_name="Chanel",
            normalized_brand_name="chanel",
            name="Coco Noir",
            normalized_name="coco noir",
            collection_name="Coco Noir",
            audience="Women",
            release_year=2012,
            source_path="/perfume/Chanel/Coco-Noir.html",
        )
        parfum_source = FragranticaProduct.objects.create(
            brand_name="Chanel",
            normalized_brand_name="chanel",
            name="Coco Noir Parfum",
            normalized_name="coco noir parfum",
            collection_name="Coco Noir",
            audience="Women",
            release_year=2014,
            source_path="/perfume/Chanel/Coco-Noir-Parfum.html",
        )

        extrait_response = self.client.get(
            reverse("prices:catalogue_linking_candidates"),
            {"perfume": extrait.pk, "min_score": "95"},
        )
        edp_response = self.client.get(
            reverse("prices:catalogue_linking_candidates"),
            {"perfume": edp.pk, "min_score": "0"},
        )

        self.assertEqual(extrait_response.status_code, 200)
        extrait_payload = extrait_response.json()
        self.assertEqual(
            [candidate["source_id"] for candidate in extrait_payload["candidates"]],
            [parfum_source.pk, generic_source.pk],
        )
        self.assertEqual(extrait_payload["candidates"][0]["score"], 100)
        self.assertEqual(
            extrait_payload["candidates"][0]["reason"],
            "Exact brand, scent, and concentration match",
        )
        self.assertEqual(extrait_payload["candidates"][1]["score"], 98)

        self.assertEqual(edp_response.status_code, 200)
        edp_payload = edp_response.json()
        self.assertEqual(edp_payload["candidates"][0]["source_id"], generic_source.pk)
        self.assertEqual(edp_payload["candidates"][0]["score"], 100)
        self.assertEqual(edp_payload["candidates"][1]["source_id"], parfum_source.pk)
        self.assertEqual(edp_payload["candidates"][1]["score"], 88)
        self.assertEqual(
            edp_payload["candidates"][1]["reason"],
            "Same brand and scent; concentration differs",
        )

    def test_catalogue_linking_prefers_explicit_concentration_match_over_generic(self):
        brand = Brand.objects.create(name="Alfred Dunhill")
        perfume = Perfume.objects.create(
            brand=brand,
            name="Driven",
            concentration="Eau de Toilette",
            collection_name="Driven",
        )
        generic_source = FragranticaProduct.objects.create(
            brand_name="Alfred Dunhill",
            normalized_brand_name="alfred dunhill",
            name="Driven",
            normalized_name="driven",
            collection_name="Driven",
            audience="Men",
            release_year=2021,
            source_path="/perfume/Alfred-Dunhill/Driven.html",
        )
        concentration_source = FragranticaProduct.objects.create(
            brand_name="Alfred Dunhill",
            normalized_brand_name="alfred dunhill",
            name="Driven Eau de Toilette",
            normalized_name="driven eau de toilette",
            collection_name="Driven",
            audience="Men",
            release_year=2021,
            source_path="/perfume/Alfred-Dunhill/Driven-Eau-de-Toilette.html",
        )

        response = self.client.get(
            reverse("prices:catalogue_linking_candidates"),
            {"perfume": perfume.pk, "min_score": "100"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["candidates"]), 1)
        self.assertEqual(
            payload["candidates"][0]["source_id"],
            concentration_source.pk,
        )
        self.assertEqual(
            payload["candidates"][0]["reason"],
            "Exact brand, scent, and concentration match",
        )

        lower_response = self.client.get(
            reverse("prices:catalogue_linking_candidates"),
            {"perfume": perfume.pk, "min_score": "95"},
        )

        self.assertEqual(lower_response.status_code, 200)
        lower_payload = lower_response.json()
        self.assertEqual(
            lower_payload["candidates"][1]["source_id"],
            generic_source.pk,
        )
        self.assertEqual(lower_payload["candidates"][1]["score"], 98)
        self.assertEqual(
            lower_payload["candidates"][1]["reason"],
            "Exact brand and scent identity match; source concentration unspecified; "
            "concentration-specific source available",
        )

    def test_catalogue_linking_demotes_generic_source_when_concentration_matters(self):
        brand = Brand.objects.create(name="Antonio Banderas")
        edp = Perfume.objects.create(
            brand=brand,
            name="The Icon",
            concentration="Eau de Parfum",
            audience="Men",
        )
        edt = Perfume.objects.create(
            brand=brand,
            name="The Icon",
            concentration="Eau de Toilette",
            audience="Men",
        )
        explicit_edp = FragranticaProduct.objects.create(
            brand_name="Antonio Banderas",
            normalized_brand_name="antonio banderas",
            name="The Icon Eau de Parfum",
            normalized_name="the icon eau de parfum",
            audience="Men",
            release_year=2022,
            source_path="/perfume/Antonio-Banderas/The-Icon-Eau-de-Parfum.html",
        )
        generic_source = FragranticaProduct.objects.create(
            brand_name="Antonio Banderas",
            normalized_brand_name="antonio banderas",
            name="The Icon",
            normalized_name="the icon",
            audience="Men",
            release_year=2020,
            source_path="/perfume/Antonio-Banderas/The-Icon.html",
        )

        exact_response = self.client.get(
            reverse("prices:catalogue_linking_candidates"),
            {"perfume": edp.pk, "min_score": "100"},
        )
        lower_response = self.client.get(
            reverse("prices:catalogue_linking_candidates"),
            {"perfume": edp.pk, "min_score": "95"},
        )
        edt_exact_response = self.client.get(
            reverse("prices:catalogue_linking_candidates"),
            {"perfume": edt.pk, "min_score": "100"},
        )

        self.assertEqual(exact_response.status_code, 200)
        exact_payload = exact_response.json()
        self.assertEqual(len(exact_payload["candidates"]), 1)
        self.assertEqual(exact_payload["candidates"][0]["source_id"], explicit_edp.pk)
        self.assertEqual(exact_payload["candidates"][0]["score"], 100)

        self.assertEqual(lower_response.status_code, 200)
        lower_payload = lower_response.json()
        self.assertEqual(
            [candidate["source_id"] for candidate in lower_payload["candidates"]],
            [explicit_edp.pk, generic_source.pk],
        )
        self.assertEqual(lower_payload["candidates"][1]["score"], 98)

        self.assertEqual(edt_exact_response.status_code, 200)
        self.assertEqual(
            [
                candidate["source_id"]
                for candidate in edt_exact_response.json()["candidates"]
            ],
            [generic_source.pk],
        )

    def test_catalogue_linking_candidate_endpoint_returns_linked_state_only(self):
        linked_source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            collection_name="Fragrantica Collection",
            audience="Women",
            release_year=2008,
            matched_perfume=self.perfume,
            match_status=FragranticaProduct.STATUS_LINKED,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )
        FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy Intense",
            normalized_name="vanilla extasy intense",
            audience="Women",
            source_path="/perfume/Montale/Vanilla-Extasy-2.html",
        )

        response = self.client.get(
            reverse("prices:catalogue_linking_candidates"),
            {"perfume": self.perfume.pk, "min_score": "0"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["candidates"], [])
        self.assertEqual(payload["linked_sources"][0]["source_id"], linked_source.pk)
        self.assertEqual(
            payload["linked_sources"][0]["label"],
            "Montale / Vanilla Extasy / Women / 2008",
        )
        self.assertEqual(
            payload["linked_sources"][0]["collection"],
            "Fragrantica Collection",
        )
        self.assertEqual(
            payload["linked_sources"][0]["unlink_url"],
            reverse("prices:fragrantica_product_unlink", args=[linked_source.pk]),
        )

    def test_catalogue_linking_candidate_endpoint_allows_reviewed_second_link(self):
        other_perfume = Perfume.objects.create(
            brand=self.perfume.brand,
            name="Vanilla Extasy",
            concentration="Eau de Toilette",
        )
        linked_source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            collection_name="Fragrantica Collection",
            audience="Women",
            release_year=2008,
            matched_perfume=self.perfume,
            match_status=FragranticaProduct.STATUS_LINKED,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )

        response = self.client.get(
            reverse("prices:catalogue_linking_candidates"),
            {"perfume": other_perfume.pk, "min_score": "95"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["candidates"][0]["source_id"], linked_source.pk)
        self.assertTrue(payload["candidates"][0]["manual_review_link"])
        self.assertTrue(payload["candidates"][0]["can_link"])
        self.assertIn(
            "already linked",
            payload["candidates"][0]["manual_review_reason"],
        )

    def test_manual_review_can_add_second_fragrantica_link(self):
        from prices.services.catalog_review import (
            build_linked_fragrantica_sources_by_perfume_ids,
        )

        other_perfume = Perfume.objects.create(
            brand=self.perfume.brand,
            name="Vanilla Extasy",
            concentration="Eau de Toilette",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            collection_name="Fragrantica Collection",
            audience="Women",
            release_year=2008,
            matched_perfume=self.perfume,
            match_status=FragranticaProduct.STATUS_LINKED,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )

        response = self.client.post(
            reverse("prices:fragrantica_product_link", args=[source.pk]),
            {
                "perfume_id": other_perfume.pk,
                "next": reverse("prices:catalogue_linking_workbench"),
                "manual_review_link": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        other_perfume.refresh_from_db()
        self.assertEqual(source.matched_perfume, self.perfume)
        self.assertTrue(
            FragranticaProductLink.objects.filter(
                source=source,
                perfume=other_perfume,
                link_type=FragranticaProductLink.LINK_TYPE_MANUAL_EXTRA,
            ).exists()
        )
        linked_map = build_linked_fragrantica_sources_by_perfume_ids(
            [self.perfume.id, other_perfume.id],
        )
        self.assertEqual(linked_map[self.perfume.id][0], source)
        self.assertEqual(linked_map[other_perfume.id][0], source)

    def test_individual_link_click_approves_second_fragrantica_link(self):
        other_perfume = Perfume.objects.create(
            brand=self.perfume.brand,
            name="Vanilla Extasy",
            concentration="Eau de Toilette",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            matched_perfume=self.perfume,
            match_status=FragranticaProduct.STATUS_LINKED,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )

        response = self.client.post(
            reverse("prices:fragrantica_product_link", args=[source.pk]),
            {
                "perfume_id": other_perfume.pk,
                "next": reverse("prices:catalogue_linking_workbench"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            FragranticaProductLink.objects.filter(
                source=source,
                perfume=other_perfume,
                link_type=FragranticaProductLink.LINK_TYPE_MANUAL_EXTRA,
            ).exists()
        )

    def test_bulk_link_checked_does_not_approve_second_fragrantica_link(self):
        other_perfume = Perfume.objects.create(
            brand=self.perfume.brand,
            name="Vanilla Extasy",
            concentration="Eau de Toilette",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            matched_perfume=self.perfume,
            match_status=FragranticaProduct.STATUS_LINKED,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )

        response = self.client.post(
            reverse("prices:catalogue_linking_workbench"),
            {
                "action": "bulk_link",
                "link_pair": f"{source.pk}:{other_perfume.pk}:0",
                "next": reverse("prices:catalogue_linking_workbench"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            FragranticaProductLink.objects.filter(
                source=source,
                perfume=other_perfume,
                link_type=FragranticaProductLink.LINK_TYPE_MANUAL_EXTRA,
            ).exists()
        )

    def test_catalogue_linking_workbench_can_unlink_primary_fragrantica_link(self):
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            audience="Women",
            matched_perfume=self.perfume,
            match_status=FragranticaProduct.STATUS_LINKED,
        )
        FragranticaProductLink.objects.create(
            source=source,
            perfume=self.perfume,
            link_type=FragranticaProductLink.LINK_TYPE_PRIMARY,
        )

        response = self.client.post(
            reverse("prices:fragrantica_product_unlink", args=[source.pk]),
            {
                "perfume_id": self.perfume.pk,
                "next": reverse("prices:catalogue_linking_workbench"),
            },
        )

        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        self.assertIsNone(source.matched_perfume_id)
        self.assertEqual(source.match_status, FragranticaProduct.STATUS_UNLINKED)
        self.assertFalse(
            FragranticaProductLink.objects.filter(
                source=source,
                perfume=self.perfume,
            ).exists()
        )

    def test_catalogue_linking_workbench_can_unlink_manual_extra_fragrantica_link(self):
        other_perfume = Perfume.objects.create(
            brand=self.perfume.brand,
            name="Vanilla Extasy",
            concentration="Eau de Toilette",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            audience="Women",
            matched_perfume=self.perfume,
            match_status=FragranticaProduct.STATUS_LINKED,
        )
        FragranticaProductLink.objects.create(
            source=source,
            perfume=self.perfume,
            link_type=FragranticaProductLink.LINK_TYPE_PRIMARY,
        )
        FragranticaProductLink.objects.create(
            source=source,
            perfume=other_perfume,
            link_type=FragranticaProductLink.LINK_TYPE_MANUAL_EXTRA,
        )

        response = self.client.post(
            reverse("prices:fragrantica_product_unlink", args=[source.pk]),
            {
                "perfume_id": other_perfume.pk,
                "next": reverse("prices:catalogue_linking_workbench"),
            },
        )

        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        self.assertEqual(source.matched_perfume, self.perfume)
        self.assertEqual(source.match_status, FragranticaProduct.STATUS_LINKED)
        self.assertTrue(
            FragranticaProductLink.objects.filter(
                source=source,
                perfume=self.perfume,
                link_type=FragranticaProductLink.LINK_TYPE_PRIMARY,
            ).exists()
        )
        self.assertFalse(
            FragranticaProductLink.objects.filter(
                source=source,
                perfume=other_perfume,
            ).exists()
        )

    def test_catalogue_linking_workbench_bulk_links_checked_suggestions(self):
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            collection_name="Fragrantica Collection",
            audience="Women",
            release_year=2008,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )

        response = self.client.post(
            reverse("prices:catalogue_linking_workbench"),
            {
                "action": "bulk_link",
                "next": reverse("prices:catalogue_linking_workbench"),
                "link_pair": f"{source.pk}:{self.perfume.pk}:0",
            },
        )

        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        self.perfume.refresh_from_db()
        self.assertEqual(source.matched_perfume, self.perfume)
        self.assertEqual(source.match_status, FragranticaProduct.STATUS_LINKED)
        self.assertEqual(self.perfume.name, "Vanilla Extasy")

    def test_catalogue_linking_workbench_bulk_links_all_filtered_pages(self):
        brand = Brand.objects.create(name="Bulk Filter Brand")
        sources = []
        for index in range(45):
            scent_name = f"Bulk Scent {index:02d}"
            Perfume.objects.create(
                brand=brand,
                name=scent_name,
                concentration="Eau de Parfum",
            )
            sources.append(
                FragranticaProduct.objects.create(
                    brand_name=brand.name,
                    normalized_brand_name="bulk filter brand",
                    name=scent_name,
                    normalized_name=scent_name.lower(),
                    source_path=f"/perfume/Bulk-Filter/{index}.html",
                )
            )

        response = self.client.post(
            reverse("prices:catalogue_linking_workbench"),
            {
                "action": "bulk_link_filtered",
                "next": reverse("prices:catalogue_linking_workbench"),
                "brand": str(brand.pk),
                "status": "unlinked",
                "suggestions": "with",
                "confidence": "100",
            },
        )

        self.assertEqual(response.status_code, 302)
        linked_count = FragranticaProduct.objects.filter(
            pk__in=[source.pk for source in sources],
            match_status=FragranticaProduct.STATUS_LINKED,
            matched_perfume__brand=brand,
        ).count()
        self.assertEqual(linked_count, 45)

    def test_catalogue_linking_workbench_rejects_unsafe_all_page_bulk_link(self):
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            source_path="/perfume/Montale/Vanilla-Extasy-unsafe.html",
        )

        response = self.client.post(
            reverse("prices:catalogue_linking_workbench"),
            {
                "action": "bulk_link_filtered",
                "next": reverse("prices:catalogue_linking_workbench"),
                "status": "unlinked",
                "suggestions": "all",
                "confidence": "all",
            },
        )

        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        self.assertEqual(source.match_status, FragranticaProduct.STATUS_UNLINKED)
        self.assertIsNone(source.matched_perfume_id)

    @override_settings(PERFUMEX_RQ_SYNC=False)
    def test_catalogue_linking_workbench_queues_all_page_bulk_link_in_production(self):
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            source_path="/perfume/Montale/Vanilla-Extasy-queued.html",
        )

        with patch(
            "prices.services.catalog_review.enqueue_management_command",
            return_value=SimpleNamespace(
                job_id="job-123",
                status="queued",
                queue_name="perfumex",
            ),
        ) as enqueue:
            response = self.client.post(
                reverse("prices:catalogue_linking_workbench"),
                {
                    "action": "bulk_link_filtered",
                    "next": reverse("prices:catalogue_linking_workbench"),
                    "status": "unlinked",
                    "suggestions": "with",
                    "confidence": "100",
                },
            )

        self.assertEqual(response.status_code, 302)
        enqueue.assert_called_once()
        self.assertEqual(
            enqueue.call_args.args[0],
            "bulk_link_catalogue_filtered",
        )
        self.assertEqual(enqueue.call_args.kwargs["confidence"], "100")
        self.assertEqual(enqueue.call_args.kwargs["status"], "unlinked")
        source.refresh_from_db()
        self.assertEqual(source.match_status, FragranticaProduct.STATUS_UNLINKED)

    @override_settings(PERFUMEX_RQ_SYNC=False)
    def test_catalogue_linking_workbench_reports_missing_worker_for_all_page_bulk_link(
        self,
    ):
        with patch(
            "prices.services.catalog_review.enqueue_management_command",
            side_effect=RuntimeError("No active RQ worker is registered"),
        ):
            response = self.client.post(
                reverse("prices:catalogue_linking_workbench"),
                {
                    "action": "bulk_link_filtered",
                    "next": reverse("prices:catalogue_linking_workbench"),
                    "status": "unlinked",
                    "suggestions": "with",
                    "confidence": "100",
                },
            )

        self.assertEqual(response.status_code, 302)
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(
            any("No active RQ worker is registered" in message for message in messages)
        )

    def test_catalogue_linking_workbench_selects_rows_without_suggestions(self):
        response = self.client.get(reverse("prices:catalogue_linking_workbench"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-catalogue-bulk-delete")
        self.assertContains(response, 'data-catalogue-selected-name="perfume_id"')
        self.assertContains(response, 'data-catalogue-selected-source="link-pair"')
        self.assertContains(response, "Bulk link all filtered")
        self.assertContains(
            response, 'aria-label="Select Montale / Vanilla Extasy / Eau de Parfum"'
        )
        self.assertNotContains(response, "No bulk suggestion for")

    def test_catalogue_linking_workbench_bulk_deletes_selected_perfumes(self):
        supplier = models.Supplier.objects.create(name="Delete Supplier")
        supplier_product = models.SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="delete-supplier-product",
            name="Montale Vanilla Extasy 100ml",
            catalog_perfume=self.perfume,
            catalog_variant=self.variant,
        )
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            matched_perfume=self.perfume,
            match_status=FragranticaProduct.STATUS_LINKED,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )

        response = self.client.post(
            reverse("prices:catalogue_linking_workbench"),
            {
                "action": "bulk_delete_perfumes",
                "next": reverse("prices:catalogue_linking_workbench"),
                "perfume_id": [str(self.perfume.pk)],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Perfume.objects.filter(pk=self.perfume.pk).exists())
        self.assertFalse(PerfumeVariant.objects.filter(pk=self.variant.pk).exists())
        source.refresh_from_db()
        self.assertIsNone(source.matched_perfume)
        self.assertEqual(source.match_status, FragranticaProduct.STATUS_UNLINKED)
        supplier_product.refresh_from_db()
        self.assertIsNone(supplier_product.catalog_perfume)
        self.assertIsNone(supplier_product.catalog_variant)

    def test_fragrantica_link_normalizes_uppercase_collection_before_applying(self):
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy",
            normalized_name="vanilla extasy",
            collection_name="PARFUM ORIENTAL FOR WOMEN",
            audience="Women",
            release_year=2008,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )

        response = self.client.post(
            reverse("prices:fragrantica_product_link", args=[source.pk]),
            {
                "perfume_id": str(self.perfume.pk),
                "next": reverse("prices:fragrantica_product_review"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.perfume.refresh_from_db()
        self.assertEqual(self.perfume.collection_name, "Parfum Oriental for Women")
        self.assertEqual(self.perfume.collection.name, "Parfum Oriental for Women")

    def test_fragrantica_link_folds_accents_before_applying_local_name(self):
        brand = Brand.objects.create(name="Accent Brand")
        perfume = Perfume.objects.create(
            brand=brand,
            name="L air Barbes",
            concentration="Eau de Parfum",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Accent Brand",
            normalized_brand_name="accent brand",
            name="L’air Barbès Eau de Parfum",
            normalized_name="l air barbes eau de parfum",
            source_path="/perfume/Accent-Brand/L-air-Barbes.html",
        )

        response = self.client.get(
            reverse("prices:catalogue_linking_workbench"),
            {"q": "l air barbes", "suggestions": "with"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Accent Brand / L air Barbes")
        self.assertContains(response, "Exact brand, scent, and concentration match")

        response = self.client.post(
            reverse("prices:fragrantica_product_link", args=[source.pk]),
            {
                "perfume_id": str(perfume.pk),
                "next": reverse("prices:fragrantica_product_review"),
            },
        )

        self.assertEqual(response.status_code, 302)
        perfume.refresh_from_db()
        source.refresh_from_db()
        self.assertEqual(perfume.name, "L'air Barbes")
        self.assertEqual(source.normalized_name, "l air barbes eau de parfum")
        self.assertNotIn("’", perfume.name)
        self.assertNotIn("è", perfume.name)

    def test_fragrantica_link_adds_for_audience_when_same_base_has_men_and_women(self):
        brand = Brand.objects.create(name="Dolce Gabbana")
        women_perfume = Perfume.objects.create(
            brand=brand,
            name="Light Blue",
            concentration="Eau de Toilette",
            audience="Women",
        )
        men_perfume = Perfume.objects.create(
            brand=brand,
            name="Light Blue",
            concentration="Eau de Toilette",
            audience="Men",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Dolce Gabbana",
            normalized_brand_name="dolce gabbana",
            name="Light Blue",
            normalized_name="light blue",
            audience="Women",
            source_path="/perfume/Dolce-Gabbana/Light-Blue-Women.html",
        )
        FragranticaProduct.objects.create(
            brand_name="Dolce Gabbana",
            normalized_brand_name="dolce gabbana",
            name="Light Blue",
            normalized_name="light blue",
            audience="Men",
            source_path="/perfume/Dolce-Gabbana/Light-Blue-Men.html",
        )

        response = self.client.post(
            reverse("prices:fragrantica_product_link", args=[source.pk]),
            {
                "perfume_id": str(women_perfume.pk),
                "apply_identity_group": "1",
                "next": reverse("prices:fragrantica_product_review"),
            },
        )

        self.assertEqual(response.status_code, 302)
        women_perfume.refresh_from_db()
        men_perfume.refresh_from_db()
        self.assertEqual(women_perfume.name, "Light Blue for Women")
        self.assertEqual(women_perfume.audience, "Women")
        self.assertEqual(men_perfume.name, "Light Blue")
        self.assertEqual(men_perfume.audience, "Men")

    def test_fragrantica_link_uses_pour_femme_when_counterpart_uses_pour_homme(self):
        brand = Brand.objects.create(name="Dolce Gabbana")
        perfume = Perfume.objects.create(
            brand=brand,
            name="Light Blue",
            concentration="Eau de Toilette",
            audience="Women",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Dolce Gabbana",
            normalized_brand_name="dolce gabbana",
            name="Light Blue",
            normalized_name="light blue",
            audience="Women",
            source_path="/perfume/Dolce-Gabbana/Light-Blue-Women.html",
        )
        FragranticaProduct.objects.create(
            brand_name="Dolce Gabbana",
            normalized_brand_name="dolce gabbana",
            name="Light Blue Pour Homme",
            normalized_name="light blue pour homme",
            audience="Men",
            source_path="/perfume/Dolce-Gabbana/Light-Blue-Pour-Homme.html",
        )

        response = self.client.post(
            reverse("prices:fragrantica_product_link", args=[source.pk]),
            {
                "perfume_id": str(perfume.pk),
                "next": reverse("prices:fragrantica_product_review"),
            },
        )

        self.assertEqual(response.status_code, 302)
        perfume.refresh_from_db()
        self.assertEqual(perfume.name, "Light Blue Pour Femme")
        self.assertEqual(perfume.audience, "Women")

    def test_fragrantica_link_normalizes_plain_gender_terms_to_for_suffix(self):
        brand = Brand.objects.create(name="Dolce Gabbana")
        perfume = Perfume.objects.create(
            brand=brand,
            name="Light Blue Female",
            concentration="Eau de Toilette",
            audience="Women",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Dolce Gabbana",
            normalized_brand_name="dolce gabbana",
            name="Light Blue Female",
            normalized_name="light blue female",
            audience="Women",
            source_path="/perfume/Dolce-Gabbana/Light-Blue-Female.html",
        )
        FragranticaProduct.objects.create(
            brand_name="Dolce Gabbana",
            normalized_brand_name="dolce gabbana",
            name="Light Blue Male",
            normalized_name="light blue male",
            audience="Men",
            source_path="/perfume/Dolce-Gabbana/Light-Blue-Male.html",
        )

        response = self.client.post(
            reverse("prices:fragrantica_product_link", args=[source.pk]),
            {
                "perfume_id": str(perfume.pk),
                "next": reverse("prices:fragrantica_product_review"),
            },
        )

        self.assertEqual(response.status_code, 302)
        perfume.refresh_from_db()
        self.assertEqual(perfume.name, "Light Blue for Women")
        self.assertEqual(perfume.audience, "Women")

    def test_catalogue_collection_title_case_preserves_known_acronyms(self):
        from prices.services.catalog_review import normalize_catalogue_collection_name

        self.assertEqual(
            normalize_catalogue_collection_name("LEGACY WB AND ORIENTAL II"),
            "Legacy WB and Oriental II",
        )

    def test_fragrantica_products_uses_parser_preprocess_rules_for_identity(self):
        from assistant_core.models import GlobalRule
        from assistant_linking.services.parser_rules import clear_parser_rule_cache

        amouage = Brand.objects.create(name="Amouage")
        Perfume.objects.create(
            brand=amouage,
            name="Reflection for Men Limited Edition",
            audience="Men",
            concentration="Eau de Parfum",
        )
        GlobalRule.objects.create(
            title="Normalize Limited Ed supplier abbreviation",
            rule_kind="regex_preprocess",
            scope_type="global",
            rule_text=r"\blimited\s+ed\.?\b => limited edition",
            active=True,
            approved=True,
        )
        clear_parser_rule_cache()
        source = FragranticaProduct.objects.create(
            brand_name="Amouage",
            normalized_brand_name="amouage",
            name="Reflection for Men Limited Ed.",
            normalized_name="reflection for men limited ed.",
            audience="Men",
            source_path="/perfume/Amouage/Reflection-Limited-Edition-1.html",
        )

        response = self.client.get(
            reverse("prices:fragrantica_product_review"),
            {"brand": "Amouage", "q": "reflection"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Amouage / Reflection for Men Limited Edition")
        self.assertContains(response, "Exact brand and scent identity match")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )

    def test_fragrantica_products_uses_parser_audience_marker_preprocess_rule(self):
        from assistant_core.models import GlobalRule
        from assistant_linking.services.parser_rules import clear_parser_rule_cache

        amouage = Brand.objects.create(name="Amouage")
        Perfume.objects.create(
            brand=amouage,
            name="Ciel for Woman",
            audience="Women",
            concentration="Eau de Parfum",
        )
        GlobalRule.objects.create(
            title="Audience marker: (L) means woman",
            rule_kind="regex_preprocess",
            scope_type="global",
            rule_text=r"\(\s*l\s*\) => woman",
            active=True,
            approved=True,
        )
        clear_parser_rule_cache()
        source = FragranticaProduct.objects.create(
            brand_name="Amouage",
            normalized_brand_name="amouage",
            name="Ciel (L)",
            normalized_name="ciel l",
            audience="Women",
            source_path="/perfume/Amouage/Ciel-Woman-1.html",
        )

        response = self.client.get(
            reverse("prices:fragrantica_product_review"),
            {"brand": "Amouage", "q": "ciel"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Amouage / Ciel for Woman")
        self.assertContains(response, "Exact brand and scent identity match")
        self.assertContains(
            response,
            reverse("prices:fragrantica_product_link", args=[source.pk]),
        )

    def test_staff_can_link_fragrantica_row_to_catalogue_without_changing_concentration(
        self,
    ):
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy Source",
            normalized_name="vanilla extasy source",
            collection_name="Fragrantica Collection",
            audience="Women",
            release_year=2008,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )

        response = self.client.post(
            reverse("prices:fragrantica_product_link", args=[source.pk]),
            {
                "perfume_id": self.perfume.pk,
                "next": reverse("prices:fragrantica_product_review"),
            },
        )

        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        self.perfume.refresh_from_db()
        self.assertEqual(source.matched_perfume, self.perfume)
        self.assertEqual(source.match_status, FragranticaProduct.STATUS_LINKED)
        self.assertEqual(self.perfume.name, "Vanilla Extasy Source")
        self.assertEqual(self.perfume.collection_name, "Fragrantica Collection")
        self.assertEqual(self.perfume.audience, "Women")
        self.assertEqual(self.perfume.release_year, 2008)
        self.assertEqual(self.perfume.concentration, "Eau de Parfum")

    def test_staff_link_strips_source_concentration_from_catalogue_name(self):
        brand = Brand.objects.create(name="Acqua di Parma")
        perfume = Perfume.objects.create(
            brand=brand,
            name="Ambra",
            concentration="Eau de Parfum",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Acqua di Parma",
            normalized_brand_name="acqua di parma",
            name="Ambra Eau de Parfum",
            normalized_name="ambra eau de parfum",
            collection_name="Signatures Of The Sun",
            audience="Unisex",
            release_year=2019,
            source_path="/perfume/Acqua-di-Parma/Ambra-1.html",
        )

        response = self.client.post(
            reverse("prices:fragrantica_product_link", args=[source.pk]),
            {
                "perfume_id": perfume.pk,
                "next": reverse("prices:catalogue_linking_workbench"),
            },
        )

        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        perfume.refresh_from_db()
        self.assertEqual(source.matched_perfume, perfume)
        self.assertEqual(perfume.name, "Ambra")
        self.assertEqual(perfume.concentration, "Eau de Parfum")
        self.assertEqual(perfume.collection_name, "Signatures of the Sun")
        self.assertEqual(perfume.audience, "Unisex")
        self.assertEqual(perfume.release_year, 2019)

    def test_staff_can_link_from_our_products_and_save_alias_for_old_name(self):
        self.perfume.name = "Vanilla Extasy Women"
        self.perfume.save(update_fields=["name", "updated_at"])
        other_concentration = Perfume.objects.create(
            brand=self.perfume.brand,
            name="Vanilla Extasy Women",
            concentration="Eau de Toilette",
        )
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy pour Femme",
            normalized_name="vanilla extasy pour femme",
            collection_name="Fragrantica Collection",
            audience="Women",
            release_year=2008,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )

        response = self.client.post(
            reverse("prices:fragrantica_product_link", args=[source.pk]),
            {
                "perfume_id": self.perfume.pk,
                "next": reverse("prices:our_product_list"),
                "create_alias": "1",
                "apply_identity_group": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        self.perfume.refresh_from_db()
        other_concentration.refresh_from_db()
        self.assertEqual(source.matched_perfume, self.perfume)
        self.assertEqual(self.perfume.name, "Vanilla Extasy pour Femme")
        self.assertEqual(other_concentration.name, "Vanilla Extasy pour Femme")
        self.assertEqual(self.perfume.concentration, "Eau de Parfum")
        self.assertEqual(other_concentration.concentration, "Eau de Toilette")
        self.assertTrue(
            ProductAlias.objects.filter(
                brand=self.perfume.brand,
                alias_text="Vanilla Extasy Women",
                canonical_text="Vanilla Extasy pour Femme",
                active=True,
            ).exists()
        )
        self.assertEqual(
            Source.objects.filter(
                perfume=self.perfume,
                url="https://www.fragrantica.com/perfume/Montale/Vanilla-Extasy-1.html",
            ).count(),
            1,
        )

    def test_staff_can_link_fragrantica_row_without_full_page_redirect(self):
        source = FragranticaProduct.objects.create(
            brand_name="Montale",
            normalized_brand_name="montale",
            name="Vanilla Extasy Source",
            normalized_name="vanilla extasy source",
            collection_name="Fragrantica Collection",
            audience="Women",
            release_year=2008,
            source_path="/perfume/Montale/Vanilla-Extasy-1.html",
        )

        response = self.client.post(
            reverse("prices:fragrantica_product_link", args=[source.pk]),
            {
                "perfume_id": self.perfume.pk,
                "next": reverse("prices:catalogue_linking_workbench"),
                "apply_identity_group": "1",
                "update_name": "0",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        source.refresh_from_db()
        self.perfume.refresh_from_db()
        self.assertTrue(payload["ok"])
        self.assertEqual(source.matched_perfume, self.perfume)
        self.assertEqual(source.match_status, FragranticaProduct.STATUS_LINKED)
        self.assertEqual(payload["selected"]["id"], self.perfume.pk)
        self.assertEqual(payload["linked_source"]["source_id"], source.pk)
        self.assertEqual(
            payload["linked_source"]["collection"], "Fragrantica Collection"
        )
        self.assertEqual(payload["linked_source"]["audience"], "Women")
        self.assertEqual(self.perfume.name, "Vanilla Extasy")

    def test_our_products_search_matches_multi_word_scent(self):
        clive = Brand.objects.create(name="Clive Christian")
        perfume = Perfume.objects.create(
            brand=clive,
            name="Blonde Amber",
            concentration="Extrait de Parfum",
        )
        PerfumeVariant.objects.create(perfume=perfume, size_ml="50.00")

        response = self.client.get(
            reverse("prices:our_product_list"), {"q": "blond amber"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Clive Christian")
        self.assertContains(response, "Blonde Amber")

    def test_staff_can_inline_edit_catalogue_variant_row(self):
        Brand.objects.create(name="Montale Paris")
        response = self.client.post(
            reverse("prices:our_product_variant_inline_update", args=[self.variant.pk]),
            {
                "brand_name": "Montale Paris",
                "perfume_name": "Vanilla Extasy Intense",
                "collection_name": "Intense",
                "concentration": "Extrait de Parfum",
                "size_ml": "50",
                "is_tester": "0",
                "packaging": "no box",
                "variant_type": "travel",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.variant.refresh_from_db()
        self.perfume.refresh_from_db()
        self.assertEqual(self.perfume.brand.name, "Montale Paris")
        self.assertEqual(self.perfume.name, "Vanilla Extasy Intense")
        self.assertEqual(self.perfume.collection_name, "Intense")
        self.assertEqual(self.perfume.collection.name, "Intense")
        self.assertEqual(self.perfume.collection.brand.name, "Montale Paris")
        self.assertEqual(self.perfume.concentration, "Extrait de Parfum")
        self.assertEqual(self.variant.size_ml, 50)
        self.assertFalse(self.variant.is_tester)
        self.assertEqual(self.variant.packaging, "no box")
        self.assertEqual(self.variant.variant_type, "travel")

    def test_our_products_products_tab_bulk_deletes_selected_variants(self):
        supplier = models.Supplier.objects.create(name="Variant Delete Supplier")
        supplier_product = models.SupplierProduct.objects.create(
            supplier=supplier,
            identity_key="variant-delete-supplier-product",
            name="Montale Vanilla Extasy 100ml",
            catalog_perfume=self.perfume,
            catalog_variant=self.variant,
        )
        response = self.client.post(
            reverse("prices:our_product_list"),
            {
                "tab": "products",
                "action": "bulk_delete_variants",
                "variant_id": [str(self.variant.pk)],
                "next": reverse("prices:our_product_list"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(PerfumeVariant.objects.filter(pk=self.variant.pk).exists())
        self.assertTrue(Perfume.objects.filter(pk=self.perfume.pk).exists())
        supplier_product.refresh_from_db()
        self.assertEqual(supplier_product.catalog_perfume, self.perfume)
        self.assertIsNone(supplier_product.catalog_variant)

    def test_our_products_brands_tab_can_add_brand(self):
        response = self.client.post(
            reverse("prices:our_product_list"),
            {"tab": "brands", "action": "add_brand", "name": "New House"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Brand.objects.filter(name="New House").exists())

    def test_our_products_brands_tab_renames_brand_for_existing_products(self):
        brand = self.perfume.brand

        response = self.client.post(
            reverse("prices:our_product_list"),
            {
                "tab": "brands",
                "action": "rename_brand",
                "brand_id": brand.id,
                "new_value": "Montale Paris",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.perfume.refresh_from_db()
        self.assertEqual(self.perfume.brand.name, "Montale Paris")
        self.assertFalse(Brand.objects.filter(name="Montale").exists())

    def test_our_products_brands_tab_rejects_duplicate_brand_name(self):
        brand = self.perfume.brand
        Brand.objects.create(name="Montale Paris")

        response = self.client.post(
            reverse("prices:our_product_list"),
            {
                "tab": "brands",
                "action": "rename_brand",
                "brand_id": brand.id,
                "new_value": "Montale Paris",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.perfume.refresh_from_db()
        self.assertEqual(self.perfume.brand.name, "Montale")

    def test_our_products_brands_tab_does_not_delete_used_brand(self):
        brand = self.perfume.brand

        response = self.client.post(
            reverse("prices:our_product_list"),
            {"tab": "brands", "action": "delete_brand", "brand_id": brand.id},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Brand.objects.filter(id=brand.id).exists())

    def test_our_products_brands_tab_deletes_unused_brand(self):
        brand = Brand.objects.create(name="Unused House")

        response = self.client.post(
            reverse("prices:our_product_list"),
            {"tab": "brands", "action": "delete_brand", "brand_id": brand.id},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Brand.objects.filter(id=brand.id).exists())

    def test_our_products_collection_tab_renames_collection(self):
        other_brand = Brand.objects.create(name="Other House")
        other_perfume = Perfume.objects.create(
            brand=other_brand,
            name="Other Classic",
            collection_name="Classic",
            concentration="Eau de Parfum",
        )

        response = self.client.post(
            reverse("prices:our_product_list"),
            {
                "tab": "collections",
                "action": "rename_collection",
                "brand_id": self.perfume.brand_id,
                "old_value": "Classic",
                "new_value": "Les Classiques",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.perfume.refresh_from_db()
        other_perfume.refresh_from_db()
        self.assertEqual(self.perfume.collection_name, "Les Classiques")
        self.assertEqual(self.perfume.collection.name, "Les Classiques")
        self.assertEqual(self.perfume.collection.brand, self.perfume.brand)
        self.assertEqual(other_perfume.collection_name, "Classic")
        self.assertEqual(other_perfume.collection.brand, other_brand)

    def test_our_products_collections_tab_lists_brand_scoped_collection_rows(self):
        other_brand = Brand.objects.create(name="Other House")
        Perfume.objects.create(
            brand=other_brand,
            name="Other Classic",
            collection_name="Classic",
            concentration="Eau de Parfum",
        )

        response = self.client.get(
            reverse("prices:our_product_list"),
            {"tab": "collections"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Montale")
        self.assertContains(response, "Other House")
        self.assertContains(
            response, f'name="brand_id" value="{self.perfume.brand_id}"'
        )
        self.assertContains(response, f'name="brand_id" value="{other_brand.id}"')

    def test_our_products_concentration_tab_renames_concentration(self):
        response = self.client.post(
            reverse("prices:our_product_list"),
            {
                "tab": "concentrations",
                "action": "rename_concentration",
                "old_value": "Eau de Parfum",
                "new_value": "Extrait de Parfum",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.perfume.refresh_from_db()
        self.assertEqual(self.perfume.concentration, "Extrait de Parfum")


class SupplierImportBoundaryTests(TestCase):
    def setUp(self):
        self.supplier = models.Supplier.objects.create(name="Stas USA", code="stas-usa")
        self.mailbox = models.Mailbox.objects.create(
            name="supplier-mailbox",
            host="imap.example.com",
            username="user@example.com",
            password="secret",
        )

    def test_latest_batch_time_prefers_processed_file_timestamp(self):
        now = timezone.now().replace(microsecond=0)
        older_received = now - timedelta(days=1)
        recent_received = now - timedelta(hours=1)

        old_backlog_batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<old-backlog@example.com>",
            received_at=older_received,
            status=models.ImportStatus.PROCESSED,
        )
        recent_batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<recent@example.com>",
            received_at=recent_received,
            status=models.ImportStatus.PROCESSED,
        )

        models.ImportFile.objects.create(
            import_batch=old_backlog_batch,
            file_kind=models.FileKind.PRICE,
            filename="old.xlsx",
            content_hash="hash-old",
            status=models.ImportStatus.PROCESSED,
            processed_at=now,
        )
        models.ImportFile.objects.create(
            import_batch=recent_batch,
            file_kind=models.FileKind.PRICE,
            filename="recent.xlsx",
            content_hash="hash-recent",
            status=models.ImportStatus.PROCESSED,
            processed_at=now - timedelta(hours=2),
        )

        latest_time = _get_supplier_latest_batch_time(self.supplier)

        self.assertIsNotNone(latest_time)
        self.assertEqual(latest_time, now)

    def test_supplier_board_uses_processed_time_for_last_success(self):
        now = timezone.now().replace(microsecond=0)
        backlog_batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<board-backlog@example.com>",
            received_at=now - timedelta(days=1),
            status=models.ImportStatus.PROCESSED,
        )
        models.ImportFile.objects.create(
            import_batch=backlog_batch,
            file_kind=models.FileKind.PRICE,
            filename="board.xlsx",
            content_hash="hash-board",
            status=models.ImportStatus.PROCESSED,
            processed_at=now,
        )

        latest_batches = _collect_latest_successful_imports()
        latest_batch = latest_batches[self.supplier.id]

        self.assertEqual(_batch_activity_datetime(latest_batch), now)

    def test_price_import_preserves_source_currency_for_history(self):
        from prices.services.importer import process_import_file

        temp_media = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(temp_media, ignore_errors=True))
        settings_obj = models.ImportSettings.get_solo()
        settings_obj.minimum_price_rows = 1
        settings_obj.save(update_fields=["minimum_price_rows"])
        self.supplier.default_currency = models.Currency.RUB
        self.supplier.save(update_fields=["default_currency"])

        received_at = timezone.make_aware(datetime(2020, 10, 20, 9, 0, 0))
        models.ExchangeRate.objects.create(
            rate_date=received_at.date(),
            from_currency=models.Currency.USD,
            to_currency=models.Currency.RUB,
            rate=Decimal("80.000000"),
            source="test",
        )
        batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<mixed-currency@example.com>",
            received_at=received_at,
        )
        mapping = models.SupplierFileMapping.objects.create(
            supplier=self.supplier,
            file_kind=models.FileKind.PRICE,
            header_row=1,
            column_map={"sku": 1, "name": 2, "price": 3, "currency": 4},
        )

        with override_settings(MEDIA_ROOT=temp_media):
            import_file = models.ImportFile.objects.create(
                import_batch=batch,
                mapping=mapping,
                file_kind=models.FileKind.PRICE,
                filename="mixed.csv",
                content_hash="mixed-hash",
            )
            import_file.file.save(
                "mixed.csv",
                ContentFile(b"SKU-1,Static Product,53,USD\n"),
                save=True,
            )

            process_import_file(import_file)

        product = models.SupplierProduct.objects.get(supplier=self.supplier)
        snapshot = models.PriceSnapshot.objects.get(supplier_product=product)
        self.assertEqual(product.current_price, Decimal("53"))
        self.assertEqual(product.currency, models.Currency.USD)
        self.assertEqual(snapshot.price, Decimal("53"))
        self.assertEqual(snapshot.currency, models.Currency.USD)
        self.assertEqual(snapshot.price_usd, Decimal("53.00"))
        self.assertEqual(snapshot.price_rub, Decimal("4240.00"))

    def test_link_payload_uses_download_time_for_product_freshness(self):
        temp_media = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(temp_media, ignore_errors=True))
        settings_obj = models.ImportSettings.get_solo()
        settings_obj.minimum_price_rows = 1
        settings_obj.save(update_fields=["minimum_price_rows"])

        now = timezone.now().replace(microsecond=0)
        old_link_email_time = now - timedelta(days=3)
        previous_batch_time = now - timedelta(days=1)
        previous_batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<previous@example.com>",
            received_at=previous_batch_time,
            status=models.ImportStatus.PROCESSED,
        )
        models.ImportFile.objects.create(
            import_batch=previous_batch,
            file_kind=models.FileKind.PRICE,
            filename="previous.csv",
            content_hash="previous-hash",
            status=models.ImportStatus.PROCESSED,
            processed_at=previous_batch_time,
        )
        product = models.SupplierProduct.objects.create(
            supplier=self.supplier,
            supplier_sku="SKU-1",
            identity_key="SKU-1",
            name="Static Product",
            currency=models.Currency.RUB,
            current_price="10.00",
            last_imported_at=previous_batch_time,
            last_import_batch=previous_batch,
            is_active=True,
        )
        mapping = models.SupplierFileMapping.objects.create(
            supplier=self.supplier,
            file_kind=models.FileKind.PRICE,
            header_row=1,
            column_map={"sku": 1, "name": 2, "price": 3},
        )
        payload = b"SKU-1,Static Product,10\n"

        with override_settings(MEDIA_ROOT=temp_media):
            result = _process_supplier_price_payload(
                supplier=self.supplier,
                mapping=mapping,
                filename="link-price.csv",
                payload=payload,
                content_type="text/csv",
                source_label="Yandex Disk",
                source_url="https://disk.yandex.ru/d/example",
                received_at=old_link_email_time,
            )

        product.refresh_from_db()
        self.assertEqual(result["status"], "imported")
        self.assertEqual(product.last_import_batch_id, result["batch"].id)
        self.assertGreater(product.last_imported_at, previous_batch_time)
        self.assertGreater(result["batch"].received_at, previous_batch_time)

    def test_duplicate_link_payload_refreshes_seen_products(self):
        temp_media = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(temp_media, ignore_errors=True))
        settings_obj = models.ImportSettings.get_solo()
        settings_obj.minimum_price_rows = 1
        settings_obj.save(update_fields=["minimum_price_rows"])

        now = timezone.now().replace(microsecond=0)
        old_seen_at = now - timedelta(days=3)
        payload = b"SKU-1,Static Product,10\n"
        content_hash = hashlib.sha256(payload).hexdigest()
        previous_batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<previous-link@example.com>",
            received_at=old_seen_at,
            status=models.ImportStatus.PROCESSED,
        )
        product = models.SupplierProduct.objects.create(
            supplier=self.supplier,
            supplier_sku="SKU-1",
            identity_key="SKU-1",
            name="Static Product",
            currency=models.Currency.RUB,
            current_price="10.00",
            last_imported_at=old_seen_at,
            last_import_batch=previous_batch,
            is_active=True,
        )
        models.ImportFile.objects.create(
            import_batch=previous_batch,
            file_kind=models.FileKind.PRICE,
            filename="previous-link.csv",
            content_hash=content_hash,
            status=models.ImportStatus.PROCESSED,
            processed_at=old_seen_at,
        )
        models.PriceSnapshot.objects.create(
            supplier_product=product,
            import_batch=previous_batch,
            price="10.00",
            currency=models.Currency.RUB,
            recorded_at=old_seen_at,
        )
        mapping = models.SupplierFileMapping.objects.create(
            supplier=self.supplier,
            file_kind=models.FileKind.PRICE,
            header_row=1,
            column_map={"sku": 1, "name": 2, "price": 3},
        )

        with override_settings(MEDIA_ROOT=temp_media):
            result = _process_supplier_price_payload(
                supplier=self.supplier,
                mapping=mapping,
                filename="link-price.csv",
                payload=payload,
                content_type="text/csv",
                source_label="Yandex Disk",
                source_url="https://disk.yandex.ru/d/example",
                received_at=old_seen_at,
            )

        product.refresh_from_db()
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(product.last_import_batch_id, previous_batch.id)
        self.assertGreater(product.last_imported_at, old_seen_at)

    def test_supplier_board_prefers_newer_autorun_check_over_canceled_run(self):
        now = timezone.now().replace(microsecond=0)
        self.supplier.last_email_check_at = now
        self.supplier.last_email_matched = 1
        self.supplier.last_email_processed = 0
        self.supplier.last_email_errors = 0
        self.supplier.last_email_last_message = "Matching emails found."
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.save(
            update_fields=[
                "last_email_check_at",
                "last_email_matched",
                "last_email_processed",
                "last_email_errors",
                "last_email_last_message",
                "from_address_pattern",
            ]
        )
        canceled_run = models.EmailImportRun.objects.create(
            supplier=self.supplier,
            status=models.EmailImportStatus.CANCELED,
            finished_at=now - timedelta(hours=1),
            last_message="Canceled by user.",
        )

        row = _build_supplier_board_row(
            supplier=self.supplier,
            successful_batch=None,
            latest_run=canceled_run,
        )

        self.assertEqual(row["check_code"], "no-change")
        self.assertEqual(row["check_label"], "current")

    def test_supplier_board_keeps_canceled_run_when_newer_than_autorun_check(self):
        now = timezone.now().replace(microsecond=0)
        self.supplier.last_email_check_at = now - timedelta(hours=1)
        self.supplier.last_email_matched = 1
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.save(
            update_fields=[
                "last_email_check_at",
                "last_email_matched",
                "from_address_pattern",
            ]
        )
        canceled_run = models.EmailImportRun.objects.create(
            supplier=self.supplier,
            status=models.EmailImportStatus.CANCELED,
            finished_at=now,
            last_message="Canceled by user.",
        )

        row = _build_supplier_board_row(
            supplier=self.supplier,
            successful_batch=None,
            latest_run=canceled_run,
        )

        self.assertEqual(row["check_code"], "canceled")
        self.assertEqual(row["check_label"], "canceled")

    def test_supplier_board_successful_run_with_duplicate_issues_stays_current(self):
        now = timezone.now().replace(microsecond=0)
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.expected_import_interval_hours = 24
        self.supplier.save(
            update_fields=["from_address_pattern", "expected_import_interval_hours"]
        )
        batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<mixed-success@example.com>",
            received_at=now - timedelta(hours=1),
            status=models.ImportStatus.PROCESSED,
        )
        models.ImportFile.objects.create(
            import_batch=batch,
            file_kind=models.FileKind.PRICE,
            filename="mixed-success.xlsx",
            content_hash="mixed-success-hash",
            status=models.ImportStatus.PROCESSED,
            processed_at=now - timedelta(hours=1),
        )
        run = models.EmailImportRun.objects.create(
            supplier=self.supplier,
            status=models.EmailImportStatus.FINISHED,
            finished_at=now,
            matched_files=4,
            processed_files=1,
            skipped_duplicates=3,
            errors=3,
            last_message="Imported 1 file, duplicate copies skipped.",
        )

        row = _build_supplier_board_row(
            supplier=self.supplier,
            successful_batch=batch,
            latest_run=run,
        )

        self.assertEqual(row["check_code"], "successful")
        self.assertEqual(row["check_label"], "current")
        self.assertEqual(row["health_code"], "fresh")
        self.assertEqual(row["problem_note"], "")

    def test_supplier_board_keeps_friday_import_fresh_through_weekend(self):
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.expected_import_interval_hours = 24
        self.supplier.save(
            update_fields=["from_address_pattern", "expected_import_interval_hours"]
        )
        friday = timezone.make_aware(datetime(2026, 4, 24, 10, 0, 0))
        sunday = timezone.make_aware(datetime(2026, 4, 26, 12, 0, 0))
        batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<weekend@example.com>",
            received_at=friday,
            status=models.ImportStatus.PROCESSED,
        )
        models.ImportFile.objects.create(
            import_batch=batch,
            file_kind=models.FileKind.PRICE,
            filename="weekend.xlsx",
            content_hash="hash-weekend",
            status=models.ImportStatus.PROCESSED,
            processed_at=friday,
        )

        with patch("prices.services.supplier_board.timezone.now", return_value=sunday):
            row = _build_supplier_board_row(
                supplier=self.supplier,
                successful_batch=batch,
                latest_run=None,
            )

        self.assertEqual(row["health_code"], "fresh")
        self.assertIn("warning after 4d", row["health_note"])

    def test_supplier_board_surfaces_latest_attachment_diagnostic(self):
        diagnostic = models.EmailAttachmentDiagnostic.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_folder="INBOX",
            message_uid="123",
            sender="supplier@example.com",
            subject="price",
            filename="bad.xlsx",
            decision=models.AttachmentDecision.QUARANTINED,
            reason_code=models.AttachmentReason.MAPPING_MISSING,
            message="Mapping is missing.",
        )

        row = _build_supplier_board_row(
            supplier=self.supplier,
            successful_batch=None,
            latest_run=None,
            latest_diagnostic=diagnostic,
        )

        self.assertEqual(
            row["latest_reason_code"], models.AttachmentReason.MAPPING_MISSING
        )
        self.assertEqual(row["source_mailbox_folder"], "supplier-mailbox/INBOX")
        self.assertIn("bad.xlsx", row["problem_note"])
        self.assertIn("Mapping is missing", row["problem_note"])

    def test_supplier_no_file_copy_is_supplier_specific(self):
        now = timezone.now().replace(microsecond=0)
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.last_email_check_at = now
        self.supplier.last_email_matched = 0
        self.supplier.last_email_processed = 0
        self.supplier.last_email_errors = 0
        self.supplier.last_email_last_message = ""
        self.supplier.save(
            update_fields=[
                "from_address_pattern",
                "last_email_check_at",
                "last_email_matched",
                "last_email_processed",
                "last_email_errors",
                "last_email_last_message",
            ]
        )

        row = _build_supplier_board_row(
            supplier=self.supplier,
            successful_batch=None,
            latest_run=None,
        )

        self.assertEqual(row["check_code"], "no-change")
        self.assertEqual(row["check_note"], "")

    def test_global_scan_without_supplier_event_does_not_touch_row_status(self):
        now = timezone.now().replace(microsecond=0)
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.save(update_fields=["from_address_pattern"])
        self.mailbox.last_checked_at = now
        self.mailbox.last_inbox_uid = 123
        self.mailbox.save(update_fields=["last_checked_at", "last_inbox_uid"])

        row = _build_supplier_board_row(
            supplier=self.supplier,
            successful_batch=None,
            latest_run=None,
        )

        self.assertEqual(row["check_code"], "no-change")
        self.assertEqual(row["check_full"], _format_local_datetime(now))
        self.assertEqual(row["check_note"], "")

    def test_fresh_duplicate_event_is_neutral_not_problem(self):
        now = timezone.now().replace(microsecond=0)
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.expected_import_interval_hours = 24
        self.supplier.save(
            update_fields=["from_address_pattern", "expected_import_interval_hours"]
        )
        batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<fresh-duplicate@example.com>",
            received_at=now - timedelta(hours=1),
            status=models.ImportStatus.PROCESSED,
        )
        models.ImportFile.objects.create(
            import_batch=batch,
            file_kind=models.FileKind.PRICE,
            filename="fresh.xlsx",
            content_hash="fresh-hash",
            status=models.ImportStatus.PROCESSED,
            processed_at=now - timedelta(hours=1),
        )
        diagnostic = models.EmailAttachmentDiagnostic.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_folder="INBOX",
            filename="fresh.xlsx",
            decision=models.AttachmentDecision.DUPLICATE,
            reason_code=models.AttachmentReason.DUPLICATE_HASH,
            message="Duplicate price attachment hash.",
            created_at=now,
        )

        row = _build_supplier_board_row(
            supplier=self.supplier,
            successful_batch=batch,
            latest_run=None,
            latest_diagnostic=diagnostic,
        )

        self.assertEqual(row["check_code"], "no-change")
        self.assertEqual(row["health_code"], "fresh")
        self.assertEqual(row["problem_note"], "")

    def test_imported_file_wins_over_non_price_attachment_error(self):
        now = timezone.now().replace(microsecond=0)
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.expected_import_interval_hours = 24
        self.supplier.last_email_check_at = now
        self.supplier.last_email_matched = 2
        self.supplier.last_email_processed = 1
        self.supplier.last_email_errors = 1
        self.supplier.last_email_last_message = "1 imported, 1 skipped"
        self.supplier.save(
            update_fields=[
                "from_address_pattern",
                "expected_import_interval_hours",
                "last_email_check_at",
                "last_email_matched",
                "last_email_processed",
                "last_email_errors",
                "last_email_last_message",
            ]
        )
        batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<price-and-invoice@example.com>",
            received_at=now,
            status=models.ImportStatus.PROCESSED,
        )
        models.ImportFile.objects.create(
            import_batch=batch,
            file_kind=models.FileKind.PRICE,
            filename="price.xlsx",
            content_hash="price-hash",
            status=models.ImportStatus.PROCESSED,
            processed_at=now,
        )

        row = _build_supplier_board_row(
            supplier=self.supplier,
            successful_batch=batch,
            latest_run=None,
        )

        self.assertEqual(row["check_code"], "successful")
        self.assertEqual(row["health_code"], "fresh")
        self.assertEqual(row["problem_note"], "")

    def test_invoice_skip_is_neutral_when_latest_price_is_fresh(self):
        now = timezone.now().replace(microsecond=0)
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.expected_import_interval_hours = 24
        self.supplier.save(
            update_fields=["from_address_pattern", "expected_import_interval_hours"]
        )
        batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<fresh-price@example.com>",
            received_at=now - timedelta(minutes=5),
            status=models.ImportStatus.PROCESSED,
        )
        models.ImportBatch.objects.filter(id=batch.id).update(
            created_at=now - timedelta(minutes=5)
        )
        batch.refresh_from_db()
        models.ImportFile.objects.create(
            import_batch=batch,
            file_kind=models.FileKind.PRICE,
            filename="fresh-price.xlsx",
            content_hash="fresh-price-hash",
            status=models.ImportStatus.PROCESSED,
            processed_at=now - timedelta(minutes=5),
        )
        diagnostic = models.EmailAttachmentDiagnostic.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_folder="INBOX",
            filename="invoice.xlsx",
            decision=models.AttachmentDecision.SKIPPED,
            reason_code=models.AttachmentReason.INVOICE_OR_REPORT,
            message="Attachment looks like an invoice, report, image, or non-price document.",
            message_date=now,
        )

        row = _build_supplier_board_row(
            supplier=self.supplier,
            successful_batch=batch,
            latest_run=None,
            latest_diagnostic=diagnostic,
        )

        self.assertEqual(row["check_code"], "ignored")
        self.assertEqual(row["health_code"], "fresh")
        self.assertEqual(row["problem_note"], "")

    def test_four_day_old_duplicate_event_warns_without_stale(self):
        now = timezone.make_aware(datetime(2026, 4, 27, 12, 0, 0))
        old = timezone.make_aware(datetime(2026, 4, 22, 10, 0, 0))
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.expected_import_interval_hours = 24
        self.supplier.save(
            update_fields=["from_address_pattern", "expected_import_interval_hours"]
        )
        batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<stale-duplicate@example.com>",
            received_at=old,
            status=models.ImportStatus.PROCESSED,
        )
        models.ImportBatch.objects.filter(id=batch.id).update(created_at=old)
        batch.refresh_from_db()
        models.ImportFile.objects.create(
            import_batch=batch,
            file_kind=models.FileKind.PRICE,
            filename="old.xlsx",
            content_hash="old-hash",
            status=models.ImportStatus.PROCESSED,
            processed_at=old,
        )
        diagnostic = models.EmailAttachmentDiagnostic.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_folder="INBOX",
            filename="old.xlsx",
            decision=models.AttachmentDecision.DUPLICATE,
            reason_code=models.AttachmentReason.DUPLICATE_HASH,
            message="Duplicate price attachment hash.",
            created_at=now,
        )

        with patch("prices.services.supplier_board.timezone.now", return_value=now):
            row = _build_supplier_board_row(
                supplier=self.supplier,
                successful_batch=batch,
                latest_run=None,
                latest_diagnostic=diagnostic,
            )

        self.assertEqual(row["check_code"], "no-change")
        self.assertEqual(row["health_code"], "warning")
        self.assertIn("Duplicate found", row["problem_note"])

    def test_six_day_old_import_becomes_stale(self):
        now = timezone.make_aware(datetime(2026, 4, 27, 12, 0, 0))
        old = timezone.make_aware(datetime(2026, 4, 21, 10, 0, 0))
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.expected_import_interval_hours = 24
        self.supplier.save(
            update_fields=["from_address_pattern", "expected_import_interval_hours"]
        )
        batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<six-day-old@example.com>",
            received_at=old,
            status=models.ImportStatus.PROCESSED,
        )
        models.ImportBatch.objects.filter(id=batch.id).update(created_at=old)
        batch.refresh_from_db()
        models.ImportFile.objects.create(
            import_batch=batch,
            file_kind=models.FileKind.PRICE,
            filename="old.xlsx",
            content_hash="six-day-old-hash",
            status=models.ImportStatus.PROCESSED,
            processed_at=old,
        )

        with patch("prices.services.supplier_board.timezone.now", return_value=now):
            row = _build_supplier_board_row(
                supplier=self.supplier,
                successful_batch=batch,
                latest_run=None,
            )

        self.assertEqual(row["health_code"], "stale")

    @patch("prices.services.import_scheduler.read_crontab_lines", return_value=[])
    def test_autoimport_scan_status_reports_recent_backlog(self, _mock_crontab):
        now = timezone.now().replace(microsecond=0)
        settings_obj = models.ImportSettings.get_solo()
        settings_obj.last_run_at = now
        settings_obj.interval_minutes = 20
        settings_obj.save(update_fields=["last_run_at", "interval_minutes"])
        self.mailbox.last_checked_at = now
        self.mailbox.last_all_mail_uid = 12000
        self.mailbox.save(update_fields=["last_checked_at", "last_all_mail_uid"])
        models.EmailAttachmentDiagnostic.objects.create(
            mailbox=self.mailbox,
            decision=models.AttachmentDecision.SKIPPED,
            reason_code=models.AttachmentReason.BACKLOG_REMAINING,
            message="209 message(s) remain after this run.",
        )

        status = _build_autoimport_scan_status()

        self.assertEqual(status["mode_label"], "Backlog catch-up")
        self.assertEqual(status["remaining_backlog"], 209)
        self.assertEqual(status["mailboxes"][0]["all_mail_uid"], 12000)

    def test_file_summary_stays_compact_for_large_duplicate_runs(self):
        run = models.EmailImportRun.objects.create(
            supplier=self.supplier,
            status=models.EmailImportStatus.FINISHED,
            matched_files=1597,
            processed_files=0,
            skipped_duplicates=649,
            errors=0,
        )

        summary = _summarize_latest_files(self.supplier, run)

        self.assertEqual(summary, "Current")
        self.assertNotIn("1597", summary)

    def test_supplier_board_check_time_uses_importer_check_not_email_date(self):
        now = timezone.now().replace(microsecond=0)
        old_email_date = now - timedelta(days=3)
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.save(update_fields=["from_address_pattern"])
        diagnostic = models.EmailAttachmentDiagnostic.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_date=old_email_date,
            sender="supplier@example.com",
            subject="Price",
            filename="price.xlsx",
            decision=models.AttachmentDecision.IMPORTED,
            reason_code="",
        )
        models.EmailAttachmentDiagnostic.objects.filter(pk=diagnostic.pk).update(
            created_at=now
        )
        diagnostic.refresh_from_db()

        row = _build_supplier_board_row(
            supplier=self.supplier,
            successful_batch=None,
            latest_run=None,
            latest_diagnostic=diagnostic,
        )

        self.assertEqual(row["check_full"], _format_local_datetime(now))

    def test_supplier_board_check_time_uses_latest_mailbox_scan(self):
        now = timezone.now().replace(microsecond=0)
        old_event_time = now - timedelta(days=1)
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.save(update_fields=["from_address_pattern"])
        self.mailbox.last_checked_at = now
        self.mailbox.save(update_fields=["last_checked_at"])
        diagnostic = models.EmailAttachmentDiagnostic.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            sender="supplier@example.com",
            subject="Price",
            filename="price.xlsx",
            decision=models.AttachmentDecision.DUPLICATE,
            reason_code=models.AttachmentReason.DUPLICATE_HASH,
        )
        models.EmailAttachmentDiagnostic.objects.filter(pk=diagnostic.pk).update(
            created_at=old_event_time
        )
        diagnostic.refresh_from_db()

        row = _build_supplier_board_row(
            supplier=self.supplier,
            successful_batch=None,
            latest_run=None,
            latest_diagnostic=diagnostic,
        )

        self.assertEqual(row["check_full"], _format_local_datetime(now))
        self.assertEqual(row["check_code"], "no-change")

    def test_supplier_board_check_time_uses_latest_completed_global_scan(self):
        now = timezone.now().replace(microsecond=0)
        old = now - timedelta(hours=1)
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.last_email_check_at = old
        self.supplier.last_email_matched = 0
        self.supplier.last_email_processed = 0
        self.supplier.save(
            update_fields=[
                "from_address_pattern",
                "last_email_check_at",
                "last_email_matched",
                "last_email_processed",
            ]
        )
        self.mailbox.last_checked_at = old
        self.mailbox.save(update_fields=["last_checked_at"])
        settings_obj = models.ImportSettings.get_solo()
        settings_obj.last_run_at = now
        settings_obj.save(update_fields=["last_run_at"])

        row = _build_supplier_board_row(
            supplier=self.supplier,
            successful_batch=None,
            latest_run=None,
        )

        self.assertEqual(row["check_full"], _format_local_datetime(now))
        self.assertEqual(row["check_code"], "no-change")

    def test_running_email_status_shows_live_activity(self):
        run = models.EmailImportRun.objects.create(
            supplier=self.supplier,
            status=models.EmailImportStatus.RUNNING,
            last_message="Importing Supplier: price_24_04.xlsx",
        )

        status = _build_email_run_status(run)

        self.assertEqual(status["code"], "running")
        self.assertEqual(status["progress"], 8)
        self.assertIn("price_24_04.xlsx", status["note"])


class ImportAttachmentPreflightTests(TestCase):
    def test_unnamed_body_parts_are_not_treated_as_attachments(self):
        body_part = EmailMessage()
        body_part.set_content("plain body")
        self.assertTrue(_is_unnamed_body_part(body_part))

        inline_part = EmailMessage()
        inline_part.set_content("inline text")
        inline_part["Content-Disposition"] = "inline"
        self.assertTrue(_is_unnamed_body_part(inline_part))

        unnamed_attachment = EmailMessage()
        unnamed_attachment.set_content(
            b"abc", maintype="application", subtype="octet-stream"
        )
        unnamed_attachment["Content-Disposition"] = "attachment"
        self.assertFalse(_is_unnamed_body_part(unnamed_attachment))

    def test_non_price_classifier_rejects_images_invoices_and_reports(self):
        self.assertTrue(_is_non_price_filename("photo.png", "image/png"))
        self.assertTrue(
            _is_non_price_filename("invoice_123.xlsx", "application/vnd.ms-excel")
        )
        self.assertTrue(
            _is_non_price_filename("акт сверки.xls", "application/vnd.ms-excel")
        )
        self.assertFalse(_is_non_price_filename("price_24_04.csv", "text/csv"))

    def test_spreadsheet_payload_validation_accepts_csv_and_rejects_bad_xlsx(self):
        valid, error = _validate_spreadsheet_payload("price.csv", b"name,price\nA,10\n")
        self.assertTrue(valid)
        self.assertEqual(error, "")

        valid, error = _validate_spreadsheet_payload("price.xlsx", b"not a workbook")
        self.assertFalse(valid)
        self.assertTrue(error)

    def test_processing_errors_map_to_structured_reason_codes(self):
        self.assertEqual(
            _reason_from_error("Mapping is missing."),
            models.AttachmentReason.MAPPING_MISSING,
        )
        self.assertEqual(
            _reason_from_error("Too few products parsed: expected at least 100."),
            models.AttachmentReason.TOO_FEW_PRODUCTS,
        )
        self.assertEqual(
            _reason_from_error("Something unexpected"),
            models.AttachmentReason.PROCESSING_ERROR,
        )


class ImportMediaHygieneTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.settings_override = override_settings(MEDIA_ROOT=self.temp_media)
        self.settings_override.enable()
        self.supplier = models.Supplier.objects.create(
            name="Media Supplier", code="media-supplier"
        )
        self.mailbox = models.Mailbox.objects.create(
            name="media-mailbox",
            host="imap.example.com",
            username="media@example.com",
            password="secret",
        )
        self.batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<media@example.com>",
            status=models.ImportStatus.PENDING,
        )

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.temp_media, ignore_errors=True)

    def test_successful_and_quarantined_files_use_separate_media_roots(self):
        permanent = models.ImportFile.objects.create(
            import_batch=self.batch,
            file_kind=models.FileKind.PRICE,
            filename="price.csv",
            content_hash="hash-permanent",
            status=models.ImportStatus.PROCESSED,
        )
        permanent.file.save("price.csv", ContentFile(b"name,price\nA,10\n"), save=True)

        quarantined = models.ImportFile.objects.create(
            import_batch=self.batch,
            file_kind=models.FileKind.PRICE,
            filename="bad.csv",
            content_hash="hash-quarantine",
            storage_type=models.ImportFileStorage.QUARANTINE,
            status=models.ImportStatus.FAILED,
            reason_code=models.AttachmentReason.MAPPING_MISSING,
            quarantine_until=timezone.now() + timedelta(days=30),
        )
        quarantined.file.save("bad.csv", ContentFile(b"name,price\nA,10\n"), save=True)

        self.assertTrue(permanent.file.name.startswith("imports/"))
        self.assertTrue(quarantined.file.name.startswith("imports_quarantine/"))

    def test_cleanup_import_media_deletes_expired_quarantine_files(self):
        quarantined = models.ImportFile.objects.create(
            import_batch=self.batch,
            file_kind=models.FileKind.PRICE,
            filename="expired.csv",
            content_hash="hash-expired",
            storage_type=models.ImportFileStorage.QUARANTINE,
            status=models.ImportStatus.FAILED,
            reason_code=models.AttachmentReason.PROCESSING_ERROR,
            quarantine_until=timezone.now() - timedelta(days=1),
        )
        quarantined.file.save(
            "expired.csv", ContentFile(b"name,price\nA,10\n"), save=True
        )
        saved_path = quarantined.file.path

        out = io.StringIO()
        call_command("cleanup_import_media", "--delete", stdout=out)

        quarantined.refresh_from_db()
        self.assertFalse(quarantined.file)
        self.assertFalse(Path(saved_path).exists())
        self.assertIn("deleted: 1", out.getvalue())


class ImportDiagnosticsPageTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="diagnostics-staff",
            password="password",
            is_staff=True,
        )
        self.client.force_login(user)
        self.supplier = models.Supplier.objects.create(name="Diagnostic Supplier")
        self.mailbox = models.Mailbox.objects.create(
            name="diagnostic-mailbox",
            host="imap.example.com",
            username="diagnostic@example.com",
            password="secret",
        )
        models.EmailAttachmentDiagnostic.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_folder="INBOX",
            sender="supplier@example.com",
            subject="daily price",
            filename="daily-price.xlsx",
            decision=models.AttachmentDecision.QUARANTINED,
            reason_code=models.AttachmentReason.MAPPING_MISSING,
            message="Mapping is missing.",
            size_bytes=1234,
        )

    def test_detailed_logs_page_renders_attachment_decisions_and_filters(self):
        response = self.client.get(
            reverse("prices:import_detailed_logs"),
            {
                "supplier": str(self.supplier.id),
                "reason": models.AttachmentReason.MAPPING_MISSING,
                "filename": "daily",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Attachment decisions")
        self.assertContains(response, "daily-price.xlsx")
        self.assertContains(response, "mapping_missing")

    def test_supplier_overview_renders_diagnostic_problem_text(self):
        response = self.client.get(reverse("prices:supplier_overview"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "daily-price.xlsx")
        self.assertContains(response, "Mapping is missing")


class ImportSchedulerTests(TestCase):
    def test_cron_line_uses_configured_interval_and_longer_timeout(self):
        settings_obj = models.ImportSettings.get_solo()
        settings_obj.interval_minutes = 20
        settings_obj.save(update_fields=["interval_minutes"])

        line = _build_cron_line(Path("/opt/perfumex/run_import_emails.sh"))

        self.assertTrue(line.startswith("*/20 * * * * "))
        self.assertIn("/usr/bin/timeout 1800s", line)
        self.assertIn("PERFUMEX_IMPORT_CRON", line)

    def test_runner_script_does_not_require_var_log_venv_or_env(self):
        script = _render_runner_script()

        self.assertIn("perfumex_email_import.log", script)
        self.assertIn("if [ -f .env ]; then", script)
        self.assertNotIn("/var/log/perfumex_email_import.log", script)
        self.assertNotIn("source .venv/bin/activate", script)

    @patch(
        "prices.services.import_scheduler.read_crontab_lines",
        return_value=["* * * * echo ok # PERFUMEX_IMPORT_CRON"],
    )
    def test_cron_status_marks_late_scheduler_stale(self, _mock_read_crontab):
        settings_obj = models.ImportSettings.get_solo()
        settings_obj.interval_minutes = 20
        settings_obj.last_run_at = timezone.now() - timedelta(hours=1)
        settings_obj.save(update_fields=["interval_minutes", "last_run_at"])

        status = _get_cron_status()

        self.assertTrue(status["stale"])
        self.assertGreaterEqual(status["late_by_minutes"], 30)

    def test_recent_run_throttle_allows_wall_clock_cron_tick(self):
        settings_obj = models.ImportSettings.get_solo()
        now = timezone.now().replace(microsecond=0)
        settings_obj.interval_minutes = 20
        settings_obj.last_run_at = now - timedelta(minutes=19, seconds=10)
        settings_obj.save(update_fields=["interval_minutes", "last_run_at"])

        self.assertFalse(_should_skip_recent_run(settings_obj, now=now))

        settings_obj.last_run_at = now - timedelta(minutes=18)
        self.assertTrue(_should_skip_recent_run(settings_obj, now=now))

    @patch("prices.management.commands.import_emails.run_import")
    @patch("prices.management.commands.import_emails.timezone.now")
    def test_import_emails_records_run_start_time_for_cron_cadence(
        self, mock_now, mock_run_import
    ):
        start = timezone.make_aware(datetime(2026, 1, 1, 12, 0))
        end = start + timedelta(minutes=2)
        current_time = {"value": start}
        mock_now.side_effect = lambda: current_time["value"]

        def finish_run(*args, **kwargs):
            current_time["value"] = end
            return {
                "matched_files": 0,
                "processed_files": 0,
                "skipped_duplicates": 0,
                "errors": 0,
                "remaining_backlog": 0,
                "timed_out": False,
            }

        mock_run_import.side_effect = finish_run
        settings_obj = models.ImportSettings.get_solo()
        settings_obj.interval_minutes = 20
        settings_obj.supplier_timeout_minutes = 0
        settings_obj.deactivate_products_after_days = 0
        settings_obj.save(
            update_fields=[
                "interval_minutes",
                "supplier_timeout_minutes",
                "deactivate_products_after_days",
            ]
        )
        models.ExchangeRate.objects.create(
            rate_date=start.date(),
            from_currency=models.Currency.USD,
            to_currency=models.Currency.RUB,
            rate="100.000000",
            source="CBR + test",
        )
        models.Mailbox.objects.create(
            name="scheduler",
            host="imap.example.com",
            username="scheduler@example.com",
            password="secret",
        )
        models.Supplier.objects.create(
            name="Scheduler Supplier",
            from_address_pattern="price@example.com",
        )

        call_command("import_emails")

        settings_obj.refresh_from_db()
        self.assertEqual(settings_obj.last_run_at, start)
        self.assertFalse(
            _should_skip_recent_run(settings_obj, now=start + timedelta(minutes=20))
        )


class HiddenProductKeywordTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="viewer",
            password="password",
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.supplier = models.Supplier.objects.create(name="Keyword Supplier")
        self.visible_product = models.SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="visible-1",
            name="Maison Vanilla 100ml",
        )
        self.hidden_product = models.SupplierProduct.objects.create(
            supplier=self.supplier,
            identity_key="hidden-1",
            name="Maison Vanilla Tester 100ml",
        )
        prefs = models.UserPreference.get_for_user(self.user)
        prefs.supplier_exclude_terms = "tester"
        prefs.save(update_fields=["supplier_exclude_terms", "updated_at"])

    def test_supplier_products_list_hides_matching_keywords(self):
        response = self.client.get(reverse("prices:product_list"))

        self.assertEqual(response.status_code, 200)
        products = list(response.context["object_list"])
        self.assertEqual(
            [product.id for product in products], [self.visible_product.id]
        )

    def test_product_linking_hides_matching_keywords(self):
        response = self.client.get(reverse("prices:product_linking"))

        self.assertEqual(response.status_code, 200)
        products = list(response.context["supplier_products"].object_list)
        self.assertEqual(
            [product.id for product in products], [self.visible_product.id]
        )
