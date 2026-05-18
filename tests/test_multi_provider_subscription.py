import base64
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.core import (
    build_cookie_health_payload,
    normalize_subscription_task,
    normalize_config,
    validate_subscription_runtime_config,
)
from app.providers.registry import get_all_capabilities, get_or_none
from app.providers.pan123 import Pan123Provider
from app.providers.quark import list_quark_share_entries_fast
from app.providers.tianyi import TianyiProvider
from app.routes import resource as resource_routes
from app.routes.resource import _compact_resource_browser_entries
from app.services.scraper import build_scraper_providers_payload
from app.services.subscription import _filter_subscription_supported_items


class _FakeResponse:
    def __init__(self, payload=None, url="", text="", headers=None, history=None, status_code=200):
        self._payload = payload
        self.url = url
        self.text = text if text else (json.dumps(payload) if payload is not None else "")
        self.headers = headers or {}
        self.history = history or []
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _der_length(length):
    if length < 0x80:
        return bytes([length])
    raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _der_integer(value):
    raw = int(value).to_bytes((int(value).bit_length() + 7) // 8, "big") or b"\x00"
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return b"\x02" + _der_length(len(raw)) + raw


def _der_sequence(*parts):
    payload = b"".join(parts)
    return b"\x30" + _der_length(len(payload)) + payload


def _rsa_public_key_b64():
    modulus = (1 << 127) + 123456789
    exponent = 65537
    der = _der_sequence(_der_integer(modulus), _der_integer(exponent))
    return base64.b64encode(der).decode("ascii")


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
            "123pan_username": "demo",
            "123pan_password": "secret",
        }
        self.assertIsNone(validate_subscription_runtime_config(enabled_cfg, task))

        disabled_cfg = {
            "provider_enabled": {"123pan": False},
            "123pan_username": "demo",
            "123pan_password": "secret",
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

    def test_password_provider_configuration_does_not_fall_back_to_115_cookie(self):
        provider = get_or_none("123pan")

        self.assertIsNotNone(provider)
        self.assertFalse(provider.is_configured({"123pan_username": "demo"}))
        self.assertTrue(provider.is_configured({"123pan_username": "demo", "123pan_password": "secret"}))

        payload = build_cookie_health_payload({"cookie_115": "valid-looking-cookie"})
        self.assertFalse(payload["123pan"]["configured"])
        self.assertEqual(payload["123pan"]["state"], "missing")

    def test_saved_config_key_order_matches_settings_page_sections(self):
        cfg = normalize_config(
            {
                "zzz_extra": "last",
                "aaa_extra": "first-extra",
                "123pan_username": "demo",
                "123pan_password": "secret",
                "aliyun_refresh_token": "refresh-token",
                "notify_wecom_webhook": "https://example.invalid/webhook",
                "strm_proxy_base_url": "http://127.0.0.1:18080",
                "resource_sources": [],
                "username": "admin",
                "password": "admin-secret",
            }
        )
        keys = list(cfg.keys())

        self.assertLess(keys.index("cookie_115"), keys.index("strm_proxy_base_url"))
        self.assertLess(keys.index("123pan_username"), keys.index("provider_enabled"))
        self.assertLess(keys.index("aliyun_refresh_token"), keys.index("provider_enabled"))
        self.assertLess(keys.index("resource_sources"), keys.index("pansou_enabled"))
        self.assertLess(keys.index("notify_wecom_webhook"), keys.index("username"))
        self.assertEqual(list(cfg["provider_enabled"].keys())[:5], ["115", "quark", "tianyi", "123pan", "aliyun"])
        self.assertLess(keys.index("aaa_extra"), keys.index("zzz_extra"))
        self.assertGreater(keys.index("aaa_extra"), keys.index("subscription_tasks"))

    def test_tianyi_password_cookie_configuration_and_label(self):
        provider = TianyiProvider()

        self.assertEqual(provider.auth_label(), "账号密码 / Cookie")
        self.assertFalse(provider.is_configured({"tianyi_username": "demo"}))
        self.assertTrue(provider.is_configured({"tianyi_username": "demo", "tianyi_password": "secret"}))
        self.assertTrue(provider.is_configured({"cookie_tianyi": "SESSION=old"}))

        payload = build_cookie_health_payload({
            "tianyi_username": "demo",
            "tianyi_password": "secret",
        })
        self.assertTrue(payload["tianyi"]["configured"])
        self.assertEqual(payload["tianyi"]["state"], "unknown")
        self.assertIn("账号密码 / Cookie", payload["tianyi"]["message"])

    def test_tianyi_get_cookie_prefers_password_login_and_keeps_cookie_fallback(self):
        provider = TianyiProvider()
        cfg = {
            "tianyi_username": "demo",
            "tianyi_password": "secret",
            "cookie_tianyi": "SESSION=fallback",
        }

        with patch.object(provider, "_ensure_login_cookie", return_value="SESSION=login") as mocked_login:
            self.assertEqual(provider.get_cookie(cfg), "SESSION=login")

        mocked_login.assert_called_once_with("demo", "secret")

        with patch.object(provider, "_ensure_login_cookie", side_effect=RuntimeError("需要验证码")):
            self.assertEqual(provider.get_cookie(cfg), "SESSION=fallback")

        with patch.object(provider, "_ensure_login_cookie", side_effect=RuntimeError("需要验证码")):
            with self.assertRaisesRegex(RuntimeError, "需要验证码"):
                provider.get_cookie({"tianyi_username": "demo", "tianyi_password": "secret"})

    def test_tianyi_device_lock_login_error_suggests_cookie_fallback(self):
        provider = TianyiProvider()
        message = provider._format_login_error("设备ID不存在，需要二次设备校验")

        self.assertIn("设备ID不存在，需要二次设备校验", message)
        self.assertIn("关闭天翼账号设备锁", message)
        self.assertIn("Cookie", message)

    def test_tianyi_rsa_login_encryption_uses_hex_payload(self):
        provider = TianyiProvider()
        with patch("app.providers.tianyi.secrets.token_bytes", return_value=b"\x01" * 32):
            encrypted = provider._rsa_encrypt_hex("u", _rsa_public_key_b64())

        self.assertEqual(len(encrypted), 32)
        self.assertRegex(encrypted, r"^[0-9a-f]+$")
        self.assertNotIn("=", encrypted)
        self.assertNotIn("+", encrypted)
        self.assertNotIn("/", encrypted)

    def test_scraper_provider_payload_does_not_login_password_provider(self):
        provider = get_or_none("123pan")
        tianyi_provider = get_or_none("tianyi")

        self.assertIsNotNone(provider)
        self.assertIsNotNone(tianyi_provider)
        cfg = {
            "provider_enabled": {"115": False, "quark": False, "123pan": True, "tianyi": True},
            "123pan_username": "demo",
            "123pan_password": "secret",
            "tianyi_username": "demo",
            "tianyi_password": "secret",
        }
        with (
            patch.object(provider, "get_cookie", side_effect=AssertionError("should not login")),
            patch.object(tianyi_provider, "get_cookie", side_effect=AssertionError("should not login")),
        ):
            payload = build_scraper_providers_payload(cfg)

        self.assertTrue(payload["ok"])
        providers = {item["provider"]: item for item in payload["providers"]}
        self.assertTrue(providers["tianyi"]["configured"])
        self.assertTrue(providers["123pan"]["configured"])
        self.assertTrue(providers["123pan"]["operations"]["rename"])
        self.assertTrue(providers["123pan"]["operations"]["move"])
        self.assertTrue(providers["123pan"]["operations"]["delete"])
        self.assertTrue(providers["123pan"]["operations"]["scrape"])
        self.assertFalse(providers["123pan"]["operations"]["copy"])

    def test_pan123_login_uses_passport_and_cache_matches_credentials(self):
        provider = Pan123Provider()
        calls = []
        responses = [
            _FakeResponse({"code": 0, "data": {"token": "token-a"}}),
            _FakeResponse({"code": 0, "data": {"token": "token-b"}}),
        ]

        def fake_post(url, headers=None, json=None, timeout=0):
            calls.append({"url": url, "json": json})
            return responses[len(calls) - 1]

        cfg = {"123pan_username": "13800138000", "123pan_password": "secret-a"}
        next_cfg = {"123pan_username": "13800138000", "123pan_password": "secret-b"}
        with patch("app.providers.pan123.requests.post", side_effect=fake_post):
            self.assertEqual(provider.get_cookie(cfg), "token-a")
            self.assertEqual(provider.get_cookie(cfg), "token-a")
            self.assertEqual(provider.get_cookie(next_cfg), "token-b")

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["url"], "https://login.123pan.com/api/user/sign_in")
        self.assertEqual(calls[0]["json"]["passport"], "13800138000")
        self.assertTrue(calls[0]["json"]["remember"])

    def test_pan123_email_login_uses_mail_payload(self):
        provider = Pan123Provider()
        calls = []

        def fake_post(url, headers=None, json=None, timeout=0):
            calls.append({"headers": headers or {}, "json": json or {}})
            return _FakeResponse({"code": 200, "data": {"token": "mail-token"}})

        with patch("app.providers.pan123.requests.post", side_effect=fake_post):
            self.assertEqual(
                provider.get_cookie({"123pan_username": "demo@example.com", "123pan_password": "secret"}),
                "mail-token",
            )

        self.assertEqual(calls[0]["json"]["mail"], "demo@example.com")
        self.assertEqual(calls[0]["json"]["type"], 2)
        self.assertEqual(calls[0]["headers"]["platform"], "web")
        self.assertEqual(calls[0]["headers"]["app-version"], "3")

    def test_pan123_list_entries_sends_drive_id_and_normalizes_new_shape(self):
        provider = Pan123Provider()
        captured = {}

        def fake_get(url, headers=None, timeout=0, params=None):
            captured["url"] = url
            captured["params"] = params or {}
            captured["headers"] = headers or {}
            return _FakeResponse({
                "code": 0,
                "data": {
                    "Total": 2,
                    "FileList": [
                        {"FileId": 100, "FileName": "电影", "Type": 1, "Size": 0},
                        {"FileId": 101, "FileName": "demo.mkv", "Type": 0, "Size": 123},
                    ],
                },
            })

        with patch("app.providers.pan123.requests.get", side_effect=fake_get):
            payload = provider.list_entries_payload("token", "0")

        self.assertIn("/b/api/file/list/new", captured["url"])
        self.assertEqual(captured["params"]["driveId"], str(provider.drive_id))
        self.assertEqual(captured["params"]["parentFileId"], "0")
        self.assertEqual(captured["params"]["trashed"], "false")
        self.assertEqual(captured["params"]["OnlyLookAbnormalFile"], "0")
        self.assertEqual(captured["params"]["event"], "homeListFile")
        self.assertEqual(captured["params"]["operateType"], "4")
        self.assertEqual(captured["params"]["inDirectSpace"], "false")
        self.assertEqual(captured["headers"]["platform"], "web")
        self.assertEqual(captured["headers"]["app-version"], "3")
        self.assertRegex(captured["url"], r"\?\d+=\d+-\d+-\d+")
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["entries"][0]["cid"], "100")
        self.assertEqual(payload["entries"][1]["fid"], "101")

    def test_pan123_basic_file_operations_use_signed_b_api_shapes(self):
        provider = Pan123Provider()
        calls = []

        def fake_post(url, headers=None, json=None, timeout=0):
            calls.append({"url": url, "headers": headers or {}, "json": json or {}})
            payload = {"code": 0, "data": {}}
            if "/file/upload_request" in url:
                payload = {"code": 0, "data": {"FileId": 999}}
            return _FakeResponse(payload)

        with patch("app.providers.pan123.requests.post", side_effect=fake_post):
            folder = provider.create_folder("token", "12", "新目录")
            provider.rename_entry("token", "101", "新名字.mkv", "12")
            provider.move_entries("token", ["101", "102"], "88", "12")
            provider.delete_entries("token", ["101"], "88")

        self.assertEqual(folder["cid"], "999")
        self.assertIn("/b/api/file/upload_request", calls[0]["url"])
        self.assertEqual(calls[0]["json"]["parentFileId"], 12)
        self.assertEqual(calls[0]["json"]["type"], 1)
        self.assertIn("/b/api/file/rename", calls[1]["url"])
        self.assertEqual(calls[1]["json"]["fileId"], 101)
        self.assertEqual(calls[1]["json"]["fileName"], "新名字.mkv")
        self.assertIn("/b/api/file/mod_pid", calls[2]["url"])
        self.assertEqual(calls[2]["json"]["fileIdList"], [{"FileId": 101}, {"FileId": 102}])
        self.assertEqual(calls[2]["json"]["parentFileId"], 88)
        self.assertIn("/b/api/file/trash", calls[3]["url"])
        self.assertEqual(calls[3]["json"]["fileIdList"], [101])
        self.assertTrue(calls[3]["json"]["operation"])
        for call in calls:
            self.assertEqual(call["headers"]["platform"], "web")
            self.assertEqual(call["headers"]["app-version"], "3")
            self.assertRegex(call["url"], r"\?\d+=\d+-\d+-\d+")

        with self.assertRaisesRegex(RuntimeError, "暂不支持复制"):
            provider.copy_entries("token", ["101"], "88", "12")

    def test_tianyi_list_entries_uses_signed_web_cookie_api(self):
        provider = TianyiProvider()
        captured = {}

        def fake_get(url, headers=None, params=None, timeout=0):
            captured["url"] = url
            captured["headers"] = headers or {}
            captured["params"] = params or {}
            return _FakeResponse({
                "res_code": 0,
                "fileListAO": {
                    "count": 2,
                    "folderList": [{"id": "folder-1", "name": "剧集", "icon": "folder"}],
                    "fileList": [{"id": "file-1", "name": "E01.mkv", "size": 1024, "fileCata": "1"}],
                },
            })

        with patch("app.providers.tianyi.requests.get", side_effect=fake_get):
            payload = provider.list_entries_payload("COOKIE=ok", "0")

        self.assertEqual(captured["url"], "https://cloud.189.cn/api/open/file/listFiles.action")
        self.assertEqual(captured["params"]["folderId"], provider.root_folder_id)
        self.assertNotIn("iconOption", captured["params"])
        self.assertEqual(captured["params"]["pageSize"], 100)
        self.assertEqual(captured["headers"]["AppKey"], provider.web_app_key)
        self.assertTrue(captured["headers"]["Signature"])
        self.assertIn("COOKIE=ok", captured["headers"]["Cookie"])
        self.assertEqual(payload["entries"][0]["cid"], "folder-1")
        self.assertEqual(payload["entries"][1]["fid"], "file-1")
        self.assertEqual(payload["summary"]["folder_count"], 1)

    def test_tianyi_ipv6_cookie_ip_mismatch_retries_web_api_with_ipv4(self):
        provider = TianyiProvider()
        calls = []
        responses = [
            _FakeResponse({
                "res_code": -1,
                "res_msg": "check ip error - curIp=240e:32d:fa8:cc00:8864:8687:b882:4ada, cookiesIp=124.31.75.165",
            }),
            _FakeResponse({
                "res_code": 0,
                "fileListAO": {
                    "count": 1,
                    "fileList": [{"id": "file-1", "name": "E01.mkv", "size": 1024, "fileCata": "1"}],
                },
            }),
        ]

        def fake_get(url, headers=None, params=None, timeout=0, allow_redirects=False):
            calls.append({"url": url, "headers": headers or {}, "params": params or {}})
            return responses[len(calls) - 1]

        with patch("app.providers.tianyi.requests.get", side_effect=fake_get):
            payload = provider.list_entries_payload("COOKIE=ip-bound", "0")

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["url"], "https://cloud.189.cn/api/open/file/listFiles.action")
        self.assertEqual(calls[1]["url"], "https://cloud.189.cn/api/open/file/listFiles.action")
        self.assertTrue(provider._should_force_ipv4_for_cookie("COOKIE=ip-bound"))
        self.assertEqual(payload["entries"][0]["fid"], "file-1")
        self.assertEqual(payload["summary"]["file_count"], 1)

    def test_tianyi_ip_binding_error_falls_back_to_open_api_list(self):
        provider = TianyiProvider()
        calls = []
        responses = [
            _FakeResponse({
                "res_code": -1,
                "res_msg": "check ip error - curIp=240e:32d:fa8:cc00:8864:8687:b882:4ada, cookiesIp=124.31.75.165",
            }),
            _FakeResponse({
                "res_code": -1,
                "res_msg": "check ip error - curIp=212.135.214.2, cookiesIp=124.31.75.165",
            }),
            _FakeResponse({"data": {"accessToken": "open-token", "expiresIn": 3600}}, headers={"Content-Type": "application/json"}),
            _FakeResponse({
                "res_code": 0,
                "data": {
                    "total": 1,
                    "items": [{"fileId": "file-1", "fileName": "E01.mkv", "fileSize": 1024, "isFolder": False}],
                },
            }),
        ]

        def fake_get(url, headers=None, params=None, timeout=0, allow_redirects=False):
            calls.append({"url": url, "headers": headers or {}, "params": params or {}})
            return responses[len(calls) - 1]

        with patch("app.providers.tianyi.requests.get", side_effect=fake_get):
            payload = provider.list_entries_payload("COOKIE=ip-bound", "0")

        self.assertEqual(calls[0]["url"], "https://cloud.189.cn/api/open/file/listFiles.action")
        self.assertEqual(calls[1]["url"], "https://cloud.189.cn/api/open/file/listFiles.action")
        self.assertEqual(calls[2]["url"], "https://api.cloud.189.cn/open/oauth2/ssoH5.action")
        self.assertEqual(calls[3]["url"], "https://api.cloud.189.cn/open/file/listFiles.action")
        self.assertEqual(calls[3]["params"]["folderId"], "0")
        self.assertEqual(payload["entries"][0]["fid"], "file-1")
        self.assertEqual(payload["summary"]["file_count"], 1)

    def test_tianyi_ip_binding_error_message_includes_both_ips(self):
        provider = TianyiProvider()
        message = provider._format_cookie_ip_binding_error(
            "check ip error - curIp=240e:32d:fa8:cc00:8864:8687:b882:4ada, cookiesIp=124.31.75.165"
        )

        self.assertIn("当前出口 IP：240e:32d:fa8:cc00:8864:8687:b882:4ada", message)
        self.assertIn("Cookie 登录 IP：124.31.75.165", message)
        self.assertIn("重新获取 cloud.189.cn Cookie", message)

    def test_tianyi_sso_token_parses_redirect_url_and_uses_cookie_cache_key(self):
        provider = TianyiProvider()
        responses = [
            _FakeResponse(
                url="https://cloud.189.cn/web/main.action?accessToken=redirect-token",
                text="<html></html>",
                headers={"Content-Type": "text/html"},
            ),
            _FakeResponse(
                url="https://cloud.189.cn/web/main.action?accessToken=next-token",
                text="<html></html>",
                headers={"Content-Type": "text/html"},
            ),
        ]
        with patch("app.providers.tianyi.requests.get", side_effect=responses) as mocked_get:
            self.assertEqual(provider._ensure_token("SESSION=one"), "redirect-token")
            self.assertEqual(provider._ensure_token("SESSION=one"), "redirect-token")
            self.assertEqual(provider._ensure_token("SESSION=next"), "next-token")

        self.assertEqual(mocked_get.call_count, 2)

    def test_tianyi_sso_token_parses_nested_json(self):
        provider = TianyiProvider()
        response = _FakeResponse(
            {"data": {"accessToken": "json-token", "expiresIn": 7200}},
            headers={"Content-Type": "application/json"},
        )
        with patch("app.providers.tianyi.requests.get", return_value=response):
            self.assertEqual(provider._ensure_token("SESSION=two"), "json-token")

    def test_tianyi_sso_token_parses_fragment_url(self):
        provider = TianyiProvider()
        response = _FakeResponse(
            url="https://cloud.189.cn/web/main.action#/home?accessToken=fragment-token",
            text="<html></html>",
            headers={"Content-Type": "text/html"},
        )
        with patch("app.providers.tianyi.requests.get", return_value=response):
            self.assertEqual(provider._ensure_token("SESSION=fragment"), "fragment-token")


class ResourceProviderLoadingTest(unittest.IsolatedAsyncioTestCase):
    async def test_generic_quark_share_route_uses_fast_share_reader_when_paged(self):
        provider = get_or_none("quark")
        mocked_runner = AsyncMock(return_value={"entries": [], "summary": {}, "elapsed_ms": 1})

        with patch.object(resource_routes, "run_resource_browse_io", mocked_runner):
            await resource_routes._list_resource_share_entries_with_provider(
                provider,
                "cookie=value",
                "https://pan.quark.cn/s/abcdef",
                "",
                "",
                "0",
                0,
                50,
                paged=True,
                folders_only=False,
            )

        args, kwargs = mocked_runner.call_args
        self.assertIs(args[0], list_quark_share_entries_fast)
        self.assertIs(kwargs["executor"], resource_routes.resource_quark_share_executor)
        self.assertTrue(kwargs["include_diagnostics"])


if __name__ == "__main__":
    unittest.main()
