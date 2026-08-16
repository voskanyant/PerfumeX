import hashlib
import io
import shutil
import tempfile
from decimal import Decimal
from email.message import EmailMessage
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.db import connection
from django.test import TestCase, override_settings
from openpyxl import Workbook

from prices import models
from prices.services.email_importer import run_import
from prices.services.importer import process_import_file


class _FakeImapClient:
    def __init__(self, message: EmailMessage, uid: int):
        self.message = message
        self.uid_value = uid

    def search(self, charset, *criteria):
        return "OK", [str(self.uid_value).encode()]

    def fetch(self, msg_id, query):
        if "RFC822.SIZE" in query:
            return "OK", [
                (
                    (
                        f"{self.uid_value} (RFC822.SIZE 100 "
                        'INTERNALDATE "16-Aug-2026 10:00:00 +0000")'
                    ).encode(),
                    b"",
                )
            ]
        return "OK", [
            (
                f"{self.uid_value} (RFC822 {{100}}".encode(),
                self.message.as_bytes(),
            )
        ]

    def logout(self):
        return "BYE", []


class EmailImporterFailureIsolationTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.temp_media, ignore_errors=True))
        settings_obj = models.ImportSettings.get_solo()
        settings_obj.minimum_price_rows = 1
        settings_obj.save(update_fields=["minimum_price_rows"])

    def _supplier_mailbox_mapping(self, suffix: str):
        supplier = models.Supplier.objects.create(
            name=f"Resilience Supplier {suffix}",
            code=f"resilience-{suffix}",
            from_address_pattern=f"{suffix}@example.com",
            price_subject_pattern="price",
            price_filename_pattern="prices",
        )
        mailbox = models.Mailbox(
            name=f"resilience-mailbox-{suffix}",
            host="imap.example.com",
            username=f"mailbox-{suffix}@example.com",
        )
        setattr(mailbox, "password", "test-mailbox-credential")
        mailbox.save()
        models.SupplierFileMapping.objects.create(
            supplier=supplier,
            file_kind=models.FileKind.PRICE,
            header_row=1,
            column_map={"sku": 1, "name": 2, "price": 3},
        )
        return supplier, mailbox

    @staticmethod
    def _message(sender: str, message_id: str, payload: bytes):
        message = EmailMessage()
        message["Subject"] = "Daily price"
        message["From"] = sender
        message["Message-ID"] = message_id
        message["Date"] = "Sun, 16 Aug 2026 10:00:00 +0000"
        message.set_content("attached")
        message.add_attachment(
            payload,
            maintype="text",
            subtype="csv",
            filename="prices.csv",
        )
        return message

    def test_database_error_quarantines_one_file_and_continues_next_mailbox(self):
        failed_supplier, first_mailbox = self._supplier_mailbox_mapping("first")
        successful_supplier, second_mailbox = self._supplier_mailbox_mapping("second")
        first_payload = b"BAD-1,Bad Product,10\n"
        second_payload = b"GOOD-1,Good Product,20\n"
        clients = [
            _FakeImapClient(
                self._message(
                    "first@example.com", "<first@example.com>", first_payload
                ),
                101,
            ),
            _FakeImapClient(
                self._message(
                    "second@example.com", "<second@example.com>", second_payload
                ),
                202,
            ),
        ]

        def import_with_database_failure(import_file):
            if import_file.import_batch.supplier_id == failed_supplier.id:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT CAST(%s AS numeric(12, 2))", ["1e306"])
                return
            models.SupplierProduct.objects.create(
                supplier=successful_supplier,
                supplier_sku="GOOD-1",
                identity_key="GOOD-1",
                name="Good Product",
                current_price=Decimal("20"),
            )

        with override_settings(MEDIA_ROOT=self.temp_media), patch(
            "prices.services.email_importer._connect_imap", side_effect=clients
        ), patch(
            "prices.services.email_importer.process_import_file",
            side_effect=import_with_database_failure,
        ):
            summary = run_import(
                [first_mailbox, second_mailbox],
                use_uid_cursor=True,
            )

        failed_file = models.ImportFile.objects.get(
            import_batch__supplier=failed_supplier
        )
        successful_file = models.ImportFile.objects.get(
            import_batch__supplier=successful_supplier
        )
        first_mailbox.refresh_from_db()
        second_mailbox.refresh_from_db()

        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["failed_files"], 1)
        self.assertEqual(summary["quarantined_files"], 1)
        self.assertEqual(summary["processed_files"], 1)
        self.assertEqual(failed_file.status, models.ImportStatus.FAILED)
        self.assertEqual(failed_file.storage_type, models.ImportFileStorage.QUARANTINE)
        self.assertTrue(failed_file.file.name.startswith("imports_quarantine/"))
        self.assertEqual(successful_file.status, models.ImportStatus.PROCESSED)
        self.assertTrue(
            models.SupplierProduct.objects.filter(
                supplier=successful_supplier,
                supplier_sku="GOOD-1",
            ).exists()
        )
        self.assertEqual(first_mailbox.last_inbox_uid, 101)
        self.assertEqual(second_mailbox.last_inbox_uid, 202)


class ImportPriceBoundsTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.temp_media, ignore_errors=True))
        settings_obj = models.ImportSettings.get_solo()
        settings_obj.minimum_price_rows = 1
        settings_obj.save(update_fields=["minimum_price_rows"])

    def test_out_of_range_price_is_rejected_before_database_write(self):
        supplier = models.Supplier.objects.create(
            name="Price Bounds Supplier",
            code="price-bounds-supplier",
        )
        batch = models.ImportBatch.objects.create(supplier=supplier)
        mapping = models.SupplierFileMapping.objects.create(
            supplier=supplier,
            file_kind=models.FileKind.PRICE,
            header_row=1,
            column_map={"sku": 1, "name": 2, "price": 3},
        )
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(
            [
                5299,
                "Sterling Armaf Odyssey Candee edp 100 ml TESTER",
                1.11111111111111e306,
            ]
        )
        workbook_bytes = io.BytesIO()
        workbook.save(workbook_bytes)
        workbook.close()
        payload = workbook_bytes.getvalue()

        with override_settings(MEDIA_ROOT=self.temp_media):
            import_file = models.ImportFile.objects.create(
                import_batch=batch,
                mapping=mapping,
                file_kind=models.FileKind.PRICE,
                filename="bad-price.xlsx",
                content_hash=hashlib.sha256(payload).hexdigest(),
            )
            import_file.file.save("bad-price.xlsx", ContentFile(payload), save=True)

            with self.assertRaisesRegex(
                RuntimeError,
                "Sterling Armaf Odyssey Candee.*outside the supported range",
            ):
                process_import_file(import_file)

        self.assertFalse(
            models.SupplierProduct.objects.filter(supplier=supplier).exists()
        )
