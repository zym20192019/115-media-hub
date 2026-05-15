import tempfile
import unittest
from pathlib import Path

from app.core import build_strm_play_url
from app.services.monitor import write_strm_file


class MonitorStrmWriteTest(unittest.TestCase):
    def test_build_strm_play_url_uses_path_only(self):
        url = build_strm_play_url(
            {"strm_proxy_base_url": "http://127.0.0.1:18080"},
            "/115/115连载中/剑来/Season 01/S01E01.mkv",
            pick_code="abc123def456",
        )

        self.assertEqual(
            url,
            "http://127.0.0.1:18080/strm/proxy?path=%2F115%2F115%E8%BF%9E%E8%BD%BD%E4%B8%AD%2F%E5%89%91%E6%9D%A5%2FSeason+01%2FS01E01.mkv",
        )
        self.assertNotIn("pickcode", url)

    def test_write_skips_same_path_url(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "item.strm"
            target.write_text("/strm/proxy?path=%2F115%2Fitem.mkv\n", encoding="utf-8")

            changed = write_strm_file(
                str(target),
                "/strm/proxy?path=%2F115%2Fitem.mkv",
            )

            self.assertFalse(changed)
            self.assertEqual(target.read_text(encoding="utf-8"), "/strm/proxy?path=%2F115%2Fitem.mkv\n")

    def test_write_migrates_legacy_pickcode_url_to_path_only_url(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "item.strm"
            target.write_text("/strm/proxy?path=%2F115%2Fitem.mkv&pickcode=old123", encoding="utf-8")

            changed = write_strm_file(
                str(target),
                "/strm/proxy?path=%2F115%2Fitem.mkv",
            )

            self.assertTrue(changed)
            self.assertEqual(target.read_text(encoding="utf-8"), "/strm/proxy?path=%2F115%2Fitem.mkv")

    def test_write_updates_when_proxy_base_changes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "item.strm"
            target.write_text("https://old.example.com/strm/proxy?path=%2F115%2Fitem.mkv", encoding="utf-8")

            changed = write_strm_file(
                str(target),
                "https://new.example.com/strm/proxy?path=%2F115%2Fitem.mkv",
            )

            self.assertTrue(changed)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "https://new.example.com/strm/proxy?path=%2F115%2Fitem.mkv",
            )


if __name__ == "__main__":
    unittest.main()
