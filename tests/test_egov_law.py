import io
import unittest
import zipfile
from unittest.mock import Mock

import egov_law


class EgovLawTests(unittest.TestCase):
    def test_sanitize_filename_replaces_invalid_chars(self) -> None:
        self.assertEqual(
            egov_law.sanitize_filename('民法:/?"<>|*'),
            "民法________",
        )

    def test_pick_date_str_uses_priority_order(self) -> None:
        law = {
            "current_revision_info": {
                "amendment_enforcement_date": "2025-04-01",
                "amendment_promulgate_date": "2025-03-15",
            },
            "law_info": {
                "promulgation_date": "1896-04-27",
            },
        }
        self.assertEqual(egov_law.pick_date_str(law), "20250401")

    def test_extract_laws_supports_wrapped_response(self) -> None:
        data = {"results": [{"law_info": {"law_id": "1"}}]}
        self.assertEqual(egov_law.extract_laws(data), data["results"])

    def test_extract_first_pdf_from_zip_returns_pdf(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zip_file:
            zip_file.writestr("law.pdf", b"%PDF-1.7 test")
            zip_file.writestr("readme.txt", b"note")

        self.assertEqual(
            egov_law.extract_first_pdf_from_zip(buffer.getvalue()),
            b"%PDF-1.7 test",
        )

    def test_build_output_filename_uses_title_and_date(self) -> None:
        law = {
            "law_info": {
                "law_title": "民法",
                "promulgation_date": "1896-04-27",
            }
        }
        self.assertEqual(
            egov_law.build_output_filename(law),
            "民法_18960427.pdf",
        )

    def test_try_download_pdf_bytes_accepts_pdf_response(self) -> None:
        response = Mock()
        response.status_code = 200
        response.headers = {"Content-Type": "application/pdf"}
        response.content = b"%PDF-1.4 dummy"

        request_session = Mock()
        request_session.get.return_value = response

        self.assertEqual(
            egov_law.try_download_pdf_bytes("123", request_session=request_session),
            b"%PDF-1.4 dummy",
        )

    def test_select_law_rejects_out_of_range_selection(self) -> None:
        with self.assertRaises(ValueError):
            egov_law.select_law([{"law_info": {"law_id": "1"}}], 2)


if __name__ == "__main__":
    unittest.main()
