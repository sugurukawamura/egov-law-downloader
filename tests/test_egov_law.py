import tempfile
import unittest
from pathlib import Path
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

    def test_build_output_filename_uses_title_date_and_extension(self) -> None:
        law = {
            "law_info": {
                "law_title": "民法",
                "promulgation_date": "1896-04-27",
            }
        }
        self.assertEqual(
            egov_law.build_output_filename(law, "html"),
            "民法_18960427.html",
        )

    def test_get_law_identifier_prefers_revision_id(self) -> None:
        law = {
            "current_revision_info": {
                "law_revision_id": "123_20240101_456",
            },
            "law_info": {
                "law_id": "123",
            },
        }
        self.assertEqual(
            egov_law.get_law_identifier(law),
            "123_20240101_456",
        )

    def test_validate_file_types_accepts_multiple_inputs(self) -> None:
        self.assertEqual(
            egov_law.validate_file_types(["html,json", "docx"]),
            ["html", "json", "docx"],
        )

    def test_validate_file_types_rejects_unsupported_type(self) -> None:
        with self.assertRaises(ValueError):
            egov_law.validate_file_types(["pdf"])

    def test_parse_selection_text_supports_comma_separated_values(self) -> None:
        self.assertEqual(egov_law.parse_selection_text("1, 3,5"), [1, 3, 5])

    def test_serialize_laws_returns_browser_friendly_shape(self) -> None:
        laws = [
            {
                "law_info": {
                    "law_id": "123",
                    "law_title": "民法",
                    "law_num": "明治二十九年法律第八十九号",
                    "promulgation_date": "1896-04-27",
                }
            }
        ]
        serialized = egov_law.serialize_laws(laws)
        self.assertEqual(serialized[0]["index"], 1)
        self.assertEqual(serialized[0]["title"], "民法")
        self.assertIn("法令番号", serialized[0]["summary"])

    def test_build_web_ui_page_mentions_browser_ui(self) -> None:
        page = egov_law.build_web_ui_page()
        self.assertIn("e-Gov 法令ダウンローダー", page)
        self.assertIn("法令を検索", page)
        self.assertIn("選択した法令を保存", page)

    def test_download_law_file_returns_binary_content(self) -> None:
        response = Mock()
        response.content = b"<html>ok</html>"
        response.raise_for_status.return_value = None

        request_session = Mock()
        request_session.get.return_value = response

        self.assertEqual(
            egov_law.download_law_file("123", "html", request_session=request_session),
            b"<html>ok</html>",
        )

    def test_select_laws_rejects_out_of_range_selection(self) -> None:
        with self.assertRaises(ValueError):
            egov_law.select_laws([{"law_info": {"law_id": "1"}}], [2])

    def test_download_selected_laws_saves_multiple_formats(self) -> None:
        law = {
            "law_info": {
                "law_id": "123",
                "law_title": "民法",
                "promulgation_date": "1896-04-27",
            }
        }

        request_session = Mock()
        request_session.get.side_effect = [
            self._make_response(b"<html>ok</html>"),
            self._make_response(b'{"ok": true}'),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            saved_paths = egov_law.download_selected_laws(
                [law],
                ["html", "json"],
                Path(temp_dir),
                request_session=request_session,
            )

            self.assertEqual(len(saved_paths), 2)
            self.assertTrue((Path(temp_dir) / "民法_18960427.html").exists())
            self.assertTrue((Path(temp_dir) / "民法_18960427.json").exists())

    @staticmethod
    def _make_response(content: bytes) -> Mock:
        response = Mock()
        response.content = content
        response.raise_for_status.return_value = None
        response.text = content.decode("utf-8", errors="replace")
        return response


if __name__ == "__main__":
    unittest.main()
