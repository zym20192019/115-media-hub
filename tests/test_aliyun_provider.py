import time
import unittest
from unittest.mock import patch

from app.providers.aliyun import ALIYUN_API_BASE, AliyunProvider


class DummyResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class AliyunProviderTest(unittest.TestCase):
    def _provider_with_token(self):
        provider = AliyunProvider()
        provider._access_token = "access-token"
        provider._token_expiry = time.time() + 3600
        provider._drive_id = "drive-id"
        return provider

    def test_list_share_entries_uses_alipan_share_token_flow(self):
        provider = self._provider_with_token()
        calls = []

        def fake_post(url, headers=None, json=None, timeout=None):
            calls.append({"url": url, "headers": headers or {}, "json": json or {}})
            if url == f"{ALIYUN_API_BASE}/v2/share_link/get_share_token":
                self.assertNotIn("Authorization", headers or {})
                self.assertEqual(json["share_id"], "share123")
                return DummyResponse({"share_token": "share-token"})
            if url == f"{ALIYUN_API_BASE}/adrive/v3/file/list":
                self.assertEqual(headers.get("Authorization"), "Bearer access-token")
                self.assertEqual(headers.get("x-share-token"), "share-token")
                self.assertEqual(headers.get("X-Canary"), "client=web,app=share,version=v2.3.1")
                self.assertEqual(json["share_id"], "share123")
                self.assertEqual(json["parent_file_id"], "root")
                return DummyResponse(
                    {
                        "items": [
                            {
                                "file_id": "folder-1",
                                "name": "剧集",
                                "type": "folder",
                                "parent_file_id": "root",
                                "drive_id": "share-drive",
                            },
                            {
                                "file_id": "file-1",
                                "name": "E01.mkv",
                                "type": "file",
                                "size": 1024,
                                "parent_file_id": "root",
                                "drive_id": "share-drive",
                            },
                        ],
                        "next_marker": "",
                    }
                )
            self.fail(f"unexpected url: {url}")

        with patch("app.providers.aliyun.requests.post", side_effect=fake_post):
            result = provider.list_share_entries("refresh-token", {"share_id": "share123"}, cid="0")

        self.assertEqual([call["url"] for call in calls], [
            f"{ALIYUN_API_BASE}/v2/share_link/get_share_token",
            f"{ALIYUN_API_BASE}/adrive/v3/file/list",
        ])
        self.assertEqual(result["entries"][0]["cid"], "folder-1")
        self.assertEqual(result["entries"][1]["fid"], "file-1")
        self.assertFalse(result["has_more"])
        self.assertEqual(result["share"]["share_token"], "share-token")
        self.assertEqual(result["share"]["drive_id"], "share-drive")

    def test_submit_share_receive_uses_batch_copy_with_share_token(self):
        provider = self._provider_with_token()

        def fake_post(url, headers=None, json=None, timeout=None):
            if url != f"{ALIYUN_API_BASE}/adrive/v4/batch":
                self.fail(f"unexpected url: {url}")
            self.assertEqual(headers.get("Authorization"), "Bearer access-token")
            self.assertEqual(headers.get("x-share-token"), "share-token")
            self.assertEqual(json["resource"], "file")
            request = json["requests"][0]
            self.assertEqual(request["url"], "/file/copy")
            self.assertEqual(request["body"]["file_id"], "file-1")
            self.assertEqual(request["body"]["share_id"], "share123")
            self.assertEqual(request["body"]["to_parent_file_id"], "target-folder")
            self.assertEqual(request["body"]["to_drive_id"], "drive-id")
            return DummyResponse({"responses": [{"id": "0", "status": 202, "body": {}}]})

        with patch("app.providers.aliyun.requests.post", side_effect=fake_post):
            result = provider.submit_share_receive(
                "refresh-token",
                {
                    "share_id": "share123",
                    "share_token": "share-token",
                    "target_cid": "target-folder",
                },
                [{"id": "file-1"}],
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 1)


if __name__ == "__main__":
    unittest.main()
