from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Brand, Perfume, PerfumeVariant
from prices import models
from prices.management.commands.import_emails import _get_supplier_latest_batch_time
from prices.views import _batch_activity_datetime, _collect_latest_successful_imports


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
