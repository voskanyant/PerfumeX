import base64
import email

from django.test import SimpleTestCase

from prices.services.email_importer import _decode_header, _get_part_filename


class EmailHeaderDecodingTests(SimpleTestCase):
    @staticmethod
    def _encoded_header(value: str, charset: str) -> str:
        payload = base64.b64encode(value.encode(charset)).decode("ascii")
        return f"=?unknown-8bit?B?{payload}?="

    def test_unknown_8bit_header_falls_back_to_utf8(self):
        header = self._encoded_header("Прайс.xlsx", "utf-8")

        self.assertEqual(_decode_header(header), "Прайс.xlsx")

    def test_unknown_8bit_header_falls_back_to_cp1251(self):
        header = self._encoded_header("Прайс.xlsx", "cp1251")

        self.assertEqual(_decode_header(header), "Прайс.xlsx")

    def test_known_header_encoding_is_preserved(self):
        payload = base64.b64encode("Тест.xlsx".encode("utf-8")).decode("ascii")

        self.assertEqual(
            _decode_header(f"=?utf-8?B?{payload}?="),
            "Тест.xlsx",
        )

    def test_unknown_8bit_subject_and_attachment_filename_are_usable(self):
        encoded_subject = self._encoded_header("Новый прайс", "cp1251")
        encoded_filename = self._encoded_header("Прайс.xlsx", "cp1251")
        raw_message = (
            f"Subject: {encoded_subject}\r\n"
            "MIME-Version: 1.0\r\n"
            f'Content-Type: application/octet-stream; name="{encoded_filename}"\r\n'
            f'Content-Disposition: attachment; filename="{encoded_filename}"\r\n'
            "Content-Transfer-Encoding: base64\r\n"
            "\r\n"
            "ZGF0YQ==\r\n"
        ).encode("ascii")

        message = email.message_from_bytes(raw_message)

        self.assertEqual(_decode_header(message.get("Subject")), "Новый прайс")
        self.assertEqual(_get_part_filename(message), "Прайс.xlsx")
