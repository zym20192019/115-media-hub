import unittest

from app.core import (
    normalize_subscription_task,
    validate_subscription_runtime_config,
)
from app.providers.registry import get_all_capabilities
from app.routes.resource import _compact_resource_browser_entries
from app.services.subscription import _filter_subscription_supported_items


class MultiProviderSubscriptionTest(unittest.TestCase):
    def test_provider_capabilities_split_subscription_and_strm(self):
        caps = {item["name"]: item for item in get_all_capabilities({})}

        for name in ("115", "quark", "tianyi", "123pan", "aliyun"):
            self.assertTrue(caps[name]["supports_subscription"])

        self.assertTrue(caps["115"]["supports_strm"])
        for name in ("quark", "tianyi", "123pan", "aliyun"):
            self.assertFalse(caps[name]["supports_strm"])

    def test_subscription_fixed_link_uses_current_provider_link_type(self):
        task = normalize_subscription_task(
            {
                "name": "演示剧",
                "provider": "aliyun",
                "media_type": "tv",
                "title": "演示剧",
                "savepath": "电视剧/演示剧",
                "share_link_url": "https://www.alipan.com/s/abc123",
                "fixed_link_channel_search": True,
            }
        )

        self.assertEqual(task["provider"], "aliyun")
        self.assertEqual(task["share_link_url"], "https://www.alipan.com/s/abc123")
        self.assertTrue(task["fixed_link_channel_search"])

        wrong_link_task = normalize_subscription_task(
            {
                **task,
                "provider": "tianyi",
                "share_link_url": "https://www.alipan.com/s/abc123",
            }
        )
        self.assertEqual(wrong_link_task["share_link_url"], "")
        self.assertFalse(wrong_link_task["fixed_link_channel_search"])

    def test_subscription_validation_is_registry_driven(self):
        task = normalize_subscription_task(
            {
                "name": "演示电影",
                "provider": "123pan",
                "media_type": "movie",
                "title": "演示电影",
                "savepath": "电影",
            }
        )
        enabled_cfg = {
            "provider_enabled": {"123pan": True},
            "cookie_123pan": "cookie-value",
        }
        self.assertIsNone(validate_subscription_runtime_config(enabled_cfg, task))

        disabled_cfg = {
            "provider_enabled": {"123pan": False},
            "cookie_123pan": "cookie-value",
        }
        self.assertIn("未启用", validate_subscription_runtime_config(disabled_cfg, task))

        missing_cookie_cfg = {"provider_enabled": {"123pan": True}}
        self.assertIn("认证信息", validate_subscription_runtime_config(missing_cookie_cfg, task))

    def test_subscription_candidate_filter_uses_provider_link_type(self):
        items = [
            {"link_url": "https://cloud.189.cn/t/abcdef", "link_type": "tianyi"},
            {"link_url": "https://115.com/s/abcdef", "link_type": "115share"},
        ]

        kept = _filter_subscription_supported_items(items, "tianyi")

        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["link_type"], "tianyi")

    def test_compact_entry_preserves_generic_share_shape(self):
        entries = _compact_resource_browser_entries(
            [
                {
                    "id": "file-1",
                    "name": "E01.mkv",
                    "is_dir": False,
                    "cid": "",
                    "fid": "file-1",
                    "parent_id": "folder-1",
                    "size": 123,
                }
            ],
            include_share_fields=True,
        )

        self.assertEqual(entries[0]["fid"], "file-1")
        self.assertEqual(entries[0]["parent_id"], "folder-1")


if __name__ == "__main__":
    unittest.main()
