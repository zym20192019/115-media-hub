import asyncio
import unittest
from unittest.mock import patch

from app import core


class ResourceSourceUsageTest(unittest.TestCase):
    def test_legacy_enabled_values_map_to_usage(self):
        enabled = core.normalize_resource_source({"channel_id": "EnabledChannel", "enabled": True})
        disabled = core.normalize_resource_source({"channel_id": "DisabledChannel", "enabled": False})

        self.assertEqual(enabled["usage"], "sync_search")
        self.assertTrue(enabled["sync_enabled"])
        self.assertTrue(enabled["search_enabled"])

        self.assertEqual(disabled["usage"], "off")
        self.assertFalse(disabled["sync_enabled"])
        self.assertFalse(disabled["search_enabled"])

    def test_sync_and_search_channel_sets_are_separate(self):
        sources = [
            core.normalize_resource_source({"channel_id": "OffChannel", "usage": "off"}),
            core.normalize_resource_source({"channel_id": "SearchOnly", "usage": "search_only"}),
            core.normalize_resource_source({"channel_id": "SyncSearch", "usage": "sync_search"}),
        ]

        self.assertEqual(core.list_sync_resource_channel_ids(sources), {"SyncSearch"})
        self.assertEqual(core.list_search_resource_channel_ids(sources), {"SearchOnly", "SyncSearch"})

    def test_search_resource_sources_uses_search_enabled_channels(self):
        cfg = {
            "resource_sources": [
                {"channel_id": "OffChannel", "usage": "off"},
                {"channel_id": "SearchOnly", "usage": "search_only"},
                {"channel_id": "SyncSearch", "usage": "sync_search"},
            ],
            "tg_channel_threads": 3,
        }
        searched_channels = []

        def fake_search(_cfg, source, *_args, **_kwargs):
            channel_id = source["channel_id"]
            searched_channels.append(channel_id)
            return {
                "items": [
                    {
                        "title": f"{channel_id} 命中",
                        "source_name": channel_id,
                        "channel_name": channel_id,
                        "raw_text": f"{channel_id} resource",
                        "link_url": f"https://example.com/{channel_id}",
                    }
                ],
                "pages_scanned": 1,
                "next_before": "",
                "has_more": False,
            }

        with patch.object(core, "get_config", return_value=cfg), patch.object(
            core,
            "search_telegram_channel_resource_items",
            side_effect=fake_search,
        ):
            result = asyncio.run(core.search_resource_sources("命中"))

        self.assertEqual(searched_channels, ["SearchOnly", "SyncSearch"])
        self.assertEqual(result["searched_sources"], 2)

    def test_channel_sections_only_include_sync_search_channels(self):
        sources = [
            core.normalize_resource_source({"channel_id": "OffChannel", "usage": "off"}),
            core.normalize_resource_source({"channel_id": "SearchOnly", "usage": "search_only"}),
            core.normalize_resource_source({"channel_id": "SyncSearch", "usage": "sync_search"}),
        ]
        items = [
            {"channel_name": "OffChannel", "title": "关闭频道缓存"},
            {"channel_name": "SearchOnly", "title": "仅搜索频道缓存"},
            {"channel_name": "SyncSearch", "title": "同步频道缓存"},
        ]

        sections = core.build_resource_channel_sections(sources, items=items, per_channel=10)

        self.assertEqual([section["channel_id"] for section in sections], ["SyncSearch"])


if __name__ == "__main__":
    unittest.main()
