import io
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase
from django.test.utils import override_settings
from django.template import Context, Template
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from catalog.models import Brand, Perfume, PerfumeVariant
from prices import models
from prices.management.commands.import_emails import _get_supplier_latest_batch_time
from prices.services.email_importer import (
    _is_non_price_filename,
    _reason_from_error,
    _validate_spreadsheet_payload,
)
from prices.views import (
    _batch_activity_datetime,
    _build_cron_line,
    _build_supplier_board_row,
    _collect_latest_successful_imports,
    _render_runner_script,
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
        self.assertContains(response, "100 ml")
        self.assertContains(response, "tester")
        self.assertContains(response, "box")

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
        self.assertEqual(row["check_label"], "no change")

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
        self.assertIn("Mon", row["health_note"])

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


class ImportAttachmentPreflightTests(TestCase):
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


class HiddenProductKeywordTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="viewer",
            password="password",
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
