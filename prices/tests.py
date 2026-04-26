import io
import hashlib
import shutil
import tempfile
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.test import TestCase, TransactionTestCase
from django.test.utils import override_settings
from django.template import Context, Template
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from catalog.models import Brand, Perfume, PerfumeVariant
from prices import forms, models
from prices.management.commands.import_emails import (
    _get_supplier_latest_batch_time,
    _should_skip_recent_run,
)
from prices.services.email_importer import (
    _advance_mailbox_uid_cursor,
    _is_non_price_filename,
    _is_unnamed_body_part,
    _reason_from_error,
    run_import,
    _validate_spreadsheet_payload,
)
from prices.services import link_importer
from prices.services.background import run_in_background
from prices.views import (
    _batch_activity_datetime,
    _build_autoimport_scan_status,
    _build_cron_line,
    _build_email_run_status,
    _build_supplier_board_row,
    _collect_latest_successful_imports,
    _format_local_datetime,
    _get_cron_status,
    _process_supplier_price_payload,
    _render_runner_script,
    _summarize_latest_files,
)


class SharedUiComponentTests(TestCase):
    def test_page_query_preserves_filters_and_replaces_page_param(self):
        request = RequestFactory().get("/admin/products/", {"q": "mango", "page": "2", "supplier": "7"})
        rendered = Template(
            "{% load prices_extras %}{% page_query 3 %}"
        ).render(Context({"request": request}))

        self.assertIn("q=mango", rendered)
        self.assertIn("supplier=7", rendered)
        self.assertIn("page=3", rendered)
        self.assertNotIn("page=2", rendered)

    def test_page_query_supports_custom_page_param(self):
        request = RequestFactory().get("/admin/linking/", {"q": "mango", "sp_page": "2"})
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

    def test_supplier_import_page_renders_server_side_tabs(self):
        supplier = models.Supplier.objects.create(
            name="Workbench Supplier",
            from_address_pattern="supplier@example.com",
        )

        response = self.client.get(reverse("prices:supplier_import", args=[supplier.pk]), secure=True)

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
            cursor.execute("SELECT password FROM prices_mailbox WHERE id = %s", [mailbox.pk])
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
            models.ImportBatch.objects.filter(mailbox=self.mailbox, message_id="").count(),
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
            models.ImportBatch.objects.filter(message_id="<rollback@example.com>").exists()
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
        self.assertTrue(
            models.ImportBatch.objects.filter(message_id="<commit@example.com>").exists()
        )

    def test_duplicate_message_id_skipped_not_crashed(self):
        models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<duplicate@example.com>",
            status=models.ImportStatus.PROCESSED,
        )
        message = EmailMessage()
        message["Subject"] = "Daily price"
        message["From"] = "supplier@example.com"
        message["Message-ID"] = "<duplicate@example.com>"
        message["Date"] = "Sat, 25 Apr 2026 10:00:00 +0000"
        message.set_content("attached")
        message.add_attachment(
            b"sku,price\nA,1\n",
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
        self.assertEqual(models.ImportFile.objects.count(), 0)
        self.assertEqual(self.mailbox.last_inbox_uid, 7)


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
            ("prices:supplier_import_email_all", {}),
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
            HTTP_REFERER="https://testserver" + reverse("prices:supplier_import", args=[self.supplier.pk]),
            secure=True,
        )
        self.assertEqual(ok_response.status_code, 200)
        self.assertIn("rows", ok_response.json())


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

        response = self.client.get(reverse("prices:stuck_email_import_runs"), secure=True)

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

        with patch("prices.views._spawn_management_command") as spawn:
            response = self.client.post(
                reverse("prices:supplier_import_email_all"),
                secure=True,
            )

        self.assertEqual(response.status_code, 302)
        spawn.assert_called_once_with("import_emails", "--force")
        self.assertFalse(models.EmailImportRun.objects.exists())

    def test_cbr_range_sync_failure_is_visible(self):
        with patch("prices.views.upsert_cbr_markup_rates_range", side_effect=RuntimeError("CBR down")):
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
        self.assertTrue(any("Failed to sync CBR range: CBR down" in message for message in messages))


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
        self.assertContains(response, "Montale")
        self.assertContains(response, "Vanilla Extasy")
        self.assertContains(response, "Eau de Parfum")
        self.assertContains(response, "100ml")
        self.assertContains(response, "tester")
        self.assertContains(response, "box")

    def test_our_products_search_matches_multi_word_scent(self):
        clive = Brand.objects.create(name="Clive Christian")
        perfume = Perfume.objects.create(
            brand=clive,
            name="Blonde Amber",
            concentration="Extrait de Parfum",
        )
        PerfumeVariant.objects.create(perfume=perfume, size_ml="50.00")

        response = self.client.get(reverse("prices:our_product_list"), {"q": "blond amber"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Clive Christian")
        self.assertContains(response, "Blonde Amber")

    def test_staff_can_inline_edit_catalogue_variant_row(self):
        response = self.client.post(
            reverse("prices:our_product_variant_inline_update", args=[self.variant.pk]),
            {
                "brand_name": "Montale Paris",
                "perfume_name": "Vanilla Extasy Intense",
                "concentration": "Extrait de Parfum",
                "size_ml": "50",
                "is_tester": "0",
                "packaging": "no box",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.variant.refresh_from_db()
        self.perfume.refresh_from_db()
        self.assertEqual(self.perfume.brand.name, "Montale Paris")
        self.assertEqual(self.perfume.name, "Vanilla Extasy Intense")
        self.assertEqual(self.perfume.concentration, "Extrait de Parfum")
        self.assertEqual(self.variant.size_ml, 50)
        self.assertFalse(self.variant.is_tester)
        self.assertEqual(self.variant.packaging, "no box")


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

    def test_supplier_board_keeps_friday_import_fresh_through_weekend(self):
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.expected_import_interval_hours = 24
        self.supplier.save(update_fields=["from_address_pattern", "expected_import_interval_hours"])
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

        with patch("prices.views.timezone.now", return_value=sunday):
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

        self.assertEqual(row["latest_reason_code"], models.AttachmentReason.MAPPING_MISSING)
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
        self.assertIn("Manual check", row["check_note"])

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

        self.assertEqual(row["check_code"], "idle")
        self.assertEqual(row["check_relative"], "Not checked")

    def test_fresh_duplicate_event_is_neutral_not_problem(self):
        now = timezone.now().replace(microsecond=0)
        self.supplier.from_address_pattern = "supplier@example.com"
        self.supplier.expected_import_interval_hours = 24
        self.supplier.save(update_fields=["from_address_pattern", "expected_import_interval_hours"])
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
        self.supplier.save(update_fields=["from_address_pattern", "expected_import_interval_hours"])
        batch = models.ImportBatch.objects.create(
            supplier=self.supplier,
            mailbox=self.mailbox,
            message_id="<fresh-price@example.com>",
            received_at=now - timedelta(minutes=5),
            status=models.ImportStatus.PROCESSED,
        )
        models.ImportBatch.objects.filter(id=batch.id).update(created_at=now - timedelta(minutes=5))
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
        self.supplier.save(update_fields=["from_address_pattern", "expected_import_interval_hours"])
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

        with patch("prices.views.timezone.now", return_value=now):
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
        self.supplier.save(update_fields=["from_address_pattern", "expected_import_interval_hours"])
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

        with patch("prices.views.timezone.now", return_value=now):
            row = _build_supplier_board_row(
                supplier=self.supplier,
                successful_batch=batch,
                latest_run=None,
            )

        self.assertEqual(row["health_code"], "stale")

    @patch("prices.views._read_crontab_lines", return_value=[])
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
        models.EmailAttachmentDiagnostic.objects.filter(pk=diagnostic.pk).update(created_at=now)
        diagnostic.refresh_from_db()

        row = _build_supplier_board_row(
            supplier=self.supplier,
            successful_batch=None,
            latest_run=None,
            latest_diagnostic=diagnostic,
        )

        self.assertEqual(row["check_full"], _format_local_datetime(now))

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
        unnamed_attachment.set_content(b"abc", maintype="application", subtype="octet-stream")
        unnamed_attachment["Content-Disposition"] = "attachment"
        self.assertFalse(_is_unnamed_body_part(unnamed_attachment))

    def test_non_price_classifier_rejects_images_invoices_and_reports(self):
        self.assertTrue(_is_non_price_filename("photo.png", "image/png"))
        self.assertTrue(_is_non_price_filename("invoice_123.xlsx", "application/vnd.ms-excel"))
        self.assertTrue(_is_non_price_filename("акт сверки.xls", "application/vnd.ms-excel"))
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
        self.supplier = models.Supplier.objects.create(name="Media Supplier", code="media-supplier")
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
        quarantined.file.save("expired.csv", ContentFile(b"name,price\nA,10\n"), save=True)
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

    @patch("prices.views._read_crontab_lines", return_value=["* * * * echo ok # PERFUMEX_IMPORT_CRON"])
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
        self.assertEqual([product.id for product in products], [self.visible_product.id])

    def test_product_linking_hides_matching_keywords(self):
        response = self.client.get(reverse("prices:product_linking"))

        self.assertEqual(response.status_code, 200)
        products = list(response.context["supplier_products"].object_list)
        self.assertEqual([product.id for product in products], [self.visible_product.id])
