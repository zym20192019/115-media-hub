import base64
import hashlib
import re
import secrets
import socket
import threading
import time
import urllib.parse

import requests
import urllib3.util.connection as urllib3_connection

from .base import CloudProvider
from .registry import register


class TianyiProvider(CloudProvider):
    name = "tianyi"
    label = "天翼云盘"
    link_type = "tianyi"
    auth_type = "password_cookie"
    config_keys = ["cookie_tianyi", "tianyi_username", "tianyi_password"]
    supports_subscription = True
    supports_offline = False
    supports_fixed_share_link = True
    supports_rename = True
    supports_move = True
    supports_copy = True
    supports_delete = True
    web_app_key = "600100422"
    root_folder_id = "-11"
    _ipv4_request_lock = threading.Lock()

    def __init__(self):
        super().__init__()
        self._token_cache = None
        self._token_expiry = 0.0
        self._token_cache_key = ""
        self._login_cookie_cache = None
        self._cookie_network_family = {}
        self._token_lock = threading.Lock()
        self._login_lock = threading.Lock()

    def _credential_cache_key(self, username: str, password: str) -> str:
        password_fingerprint = hashlib.sha256(str(password or "").encode("utf-8")).hexdigest()
        return f"{str(username or '').strip()}|{password_fingerprint}"

    def _cookie_cache_key(self, cookie: str) -> str:
        return hashlib.sha256(str(cookie or "").encode("utf-8")).hexdigest()

    def is_configured(self, cfg: dict) -> bool:
        source = cfg if isinstance(cfg, dict) else {}
        username = str(source.get("tianyi_username", "") or "").strip()
        password = str(source.get("tianyi_password", "") or "").strip()
        cookie = str(source.get("cookie_tianyi", "") or "").strip()
        return bool((username and password) or cookie)

    def get_cookie(self, cfg: dict) -> str:
        source = cfg if isinstance(cfg, dict) else {}
        username = str(source.get("tianyi_username", "") or "").strip()
        password = str(source.get("tianyi_password", "") or "").strip()
        fallback_cookie = str(source.get("cookie_tianyi", "") or "").strip()
        if username and password:
            try:
                return self._ensure_login_cookie(username, password)
            except Exception:
                if fallback_cookie:
                    return fallback_cookie
                raise
        return fallback_cookie

    def _normalize_folder_id(self, cid: str) -> str:
        token = str(cid or "").strip()
        return self.root_folder_id if token in {"", "0", "root"} else token

    def _normalize_open_folder_id(self, cid: str) -> str:
        token = str(cid or "").strip()
        return "0" if token in {"", "0", "root", self.root_folder_id} else token

    def _web_timestamp(self) -> str:
        return str(int(time.time() * 1000))

    def _error_detail(self, data: dict, fallback: str = "") -> str:
        if not isinstance(data, dict):
            return str(fallback or "").strip()
        for key in ("errorMsg", "errorMessage", "errorCode", "message", "msg", "res_message", "res_msg"):
            detail = str(data.get(key, "") or "").strip()
            if detail:
                return detail
        return str(fallback or "").strip()

    def _is_cookie_ip_binding_error(self, detail: str) -> bool:
        text = str(detail or "").lower()
        return "check ip error" in text or ("curip=" in text and "cookiesip=" in text) or "ip 不一致" in text

    def _extract_cookie_ip_binding_ips(self, detail: str) -> tuple:
        text = str(detail or "").strip()
        cur_ip = ""
        cookie_ip = ""
        cur_match = re.search(r"curIp=([^,\s；）]+)", text, re.IGNORECASE) or re.search(r"当前出口 IP：([^；）]+)", text)
        cookie_match = re.search(r"cookiesIp=([^,\s；）]+)", text, re.IGNORECASE) or re.search(r"Cookie 登录 IP：([^；）]+)", text)
        if cur_match:
            cur_ip = cur_match.group(1).strip()
        if cookie_match:
            cookie_ip = cookie_match.group(1).strip()
        return cur_ip, cookie_ip

    def _should_retry_ipv4_for_ip_error(self, detail: str) -> bool:
        cur_ip, cookie_ip = self._extract_cookie_ip_binding_ips(detail)
        return bool(cur_ip and cookie_ip and ":" in cur_ip and "." in cookie_ip)

    def _format_cookie_ip_binding_error(self, detail: str) -> str:
        cur_ip, cookie_ip = self._extract_cookie_ip_binding_ips(detail)
        suffix_parts = []
        if cur_ip:
            suffix_parts.append(f"当前出口 IP：{cur_ip}")
        if cookie_ip:
            suffix_parts.append(f"Cookie 登录 IP：{cookie_ip}")
        suffix = f"（{'；'.join(suffix_parts)}）" if suffix_parts else ""
        return f"天翼云盘 Cookie 与当前服务出口 IP 不一致{suffix}，请在运行本服务的同一网络/IP 下重新获取 cloud.189.cn Cookie，或让服务使用与获取 Cookie 相同的出口。"

    def _format_login_error(self, detail: str) -> str:
        message = str(detail or "").strip() or "未知错误"
        lowered = message.lower()
        captcha_hints = (
            "captcha",
            "validate",
            "验证码",
            "滑块",
            "短信",
            "sms",
            "风控",
            "安全验证",
            "设备",
            "device",
            "二次",
            "设备锁",
        )
        suffix = "；如触发验证码、短信、滑块或设备锁，请先关闭天翼账号设备锁，或改用 Cookie 方式。"
        if any(hint in lowered for hint in captcha_hints) or any(hint in message for hint in captcha_hints):
            return f"天翼云盘账号密码登录失败：{message}{suffix}"
        return f"天翼云盘账号密码登录失败：{message}"

    def _read_der_length(self, data: bytes, offset: int) -> tuple:
        if offset >= len(data):
            raise ValueError("DER length missing")
        first = data[offset]
        offset += 1
        if first < 0x80:
            return first, offset
        count = first & 0x7F
        if count <= 0 or offset + count > len(data):
            raise ValueError("DER length invalid")
        return int.from_bytes(data[offset:offset + count], "big"), offset + count

    def _read_der_tlv(self, data: bytes, offset: int) -> tuple:
        if offset >= len(data):
            raise ValueError("DER tag missing")
        tag = data[offset]
        length, value_offset = self._read_der_length(data, offset + 1)
        end = value_offset + length
        if end > len(data):
            raise ValueError("DER value truncated")
        return tag, data[value_offset:end], end

    def _decode_rsa_public_numbers(self, pub_key: str) -> tuple:
        cleaned = re.sub(r"-----BEGIN [^-]+-----|-----END [^-]+-----|\s+", "", str(pub_key or ""))
        if not cleaned:
            raise ValueError("public key empty")
        der = base64.b64decode(cleaned)
        tag, outer, end = self._read_der_tlv(der, 0)
        if tag != 0x30 or end != len(der):
            raise ValueError("public key sequence invalid")

        inner_offset = 0
        first_tag, first_value, first_end = self._read_der_tlv(outer, inner_offset)
        if first_tag == 0x02:
            second_tag, second_value, second_end = self._read_der_tlv(outer, first_end)
            if second_tag != 0x02 or second_end != len(outer):
                raise ValueError("PKCS#1 public key invalid")
            return int.from_bytes(first_value, "big"), int.from_bytes(second_value, "big")

        _, bit_string, bit_end = self._read_der_tlv(outer, first_end)
        if bit_end != len(outer) or not bit_string:
            raise ValueError("SubjectPublicKeyInfo invalid")
        rsa_der = bit_string[1:]
        rsa_tag, rsa_outer, rsa_end = self._read_der_tlv(rsa_der, 0)
        if rsa_tag != 0x30 or rsa_end != len(rsa_der):
            raise ValueError("RSA key sequence invalid")
        mod_tag, mod_value, mod_end = self._read_der_tlv(rsa_outer, 0)
        exp_tag, exp_value, exp_end = self._read_der_tlv(rsa_outer, mod_end)
        if mod_tag != 0x02 or exp_tag != 0x02 or exp_end != len(rsa_outer):
            raise ValueError("RSA key integers invalid")
        return int.from_bytes(mod_value, "big"), int.from_bytes(exp_value, "big")

    def _rsa_encrypt_hex(self, text: str, pub_key: str) -> str:
        modulus, exponent = self._decode_rsa_public_numbers(pub_key)
        key_len = (modulus.bit_length() + 7) // 8
        message = str(text or "").encode("utf-8")
        if len(message) > key_len - 11:
            raise ValueError("RSA message too long")
        padding_len = key_len - len(message) - 3
        padding = bytearray()
        while len(padding) < padding_len:
            chunk = secrets.token_bytes(padding_len - len(padding))
            padding.extend(byte for byte in chunk if byte != 0)
        encoded = b"\x00\x02" + bytes(padding[:padding_len]) + b"\x00" + message
        encrypted = pow(int.from_bytes(encoded, "big"), exponent, modulus).to_bytes(key_len, "big")
        return encrypted.hex()

    def _login_json(self, resp: requests.Response) -> dict:
        try:
            payload = resp.json()
        except (ValueError, requests.JSONDecodeError):
            text = str(getattr(resp, "text", "") or "").strip()
            raise RuntimeError(text or "天翼登录返回数据异常")
        return payload if isinstance(payload, dict) else {}

    def _session_cookie_header(self, session: requests.Session) -> str:
        items = []
        for cookie in session.cookies:
            if cookie.name and cookie.value:
                items.append(f"{cookie.name}={cookie.value}")
        return "; ".join(items)

    def _ensure_login_cookie(self, username: str, password: str) -> str:
        now = time.time()
        cache_key = self._credential_cache_key(username, password)
        with self._login_lock:
            if (
                self._login_cookie_cache
                and self._login_cookie_cache.get("cache_key") == cache_key
                and now < self._login_cookie_cache.get("expires_at", 0) - 300
            ):
                return self._login_cookie_cache["cookie"]

            session = requests.Session()
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json, text/plain, */*",
            }
            login_url = "https://cloud.189.cn/api/portal/loginUrl.action?redirectURL=https%3A%2F%2Fcloud.189.cn%2Fmain.action"
            resp = session.get(login_url, headers=headers, allow_redirects=True, timeout=20)
            resp.raise_for_status()
            redirect_url = str(getattr(resp, "url", "") or "")
            parsed = urllib.parse.urlparse(redirect_url)
            query = urllib.parse.parse_qs(parsed.query)
            lt = (query.get("lt") or [""])[0]
            req_id = (query.get("reqId") or query.get("reqid") or [""])[0]
            app_id = (query.get("appId") or query.get("appKey") or [""])[0]
            if not (lt and req_id and app_id):
                cookie = self._session_cookie_header(session)
                if cookie and "cloud.189.cn" in redirect_url:
                    self._login_cookie_cache = {"cookie": cookie, "expires_at": now + 12 * 3600, "cache_key": cache_key}
                    return cookie
                raise RuntimeError(self._format_login_error("登录页参数获取失败，可能需要验证码或 Cookie 登录"))

            login_headers = {
                **headers,
                "lt": lt,
                "reqid": req_id,
                "Referer": redirect_url,
                "Origin": "https://open.e.189.cn",
            }
            app_conf_resp = session.post(
                "https://open.e.189.cn/api/logbox/oauth2/appConf.do",
                headers=login_headers,
                data={"version": "2.0", "appKey": app_id},
                timeout=20,
            )
            app_conf_resp.raise_for_status()
            app_conf = self._login_json(app_conf_resp)
            if str(app_conf.get("result", "")) != "0":
                raise RuntimeError(self._format_login_error(app_conf.get("msg", "") or "获取登录配置失败"))
            app_data = app_conf.get("data", {}) if isinstance(app_conf.get("data"), dict) else {}

            encrypt_resp = session.post(
                "https://open.e.189.cn/api/logbox/config/encryptConf.do",
                headers=login_headers,
                data={"appId": app_id},
                timeout=20,
            )
            encrypt_resp.raise_for_status()
            encrypt_conf = self._login_json(encrypt_resp)
            try:
                encrypt_result = int(encrypt_conf.get("result", -1))
            except (TypeError, ValueError):
                encrypt_result = -1
            if encrypt_result != 0:
                raise RuntimeError(self._format_login_error(encrypt_conf.get("msg", "") or "获取加密配置失败"))
            encrypt_data = encrypt_conf.get("data", {}) if isinstance(encrypt_conf.get("data"), dict) else {}
            pub_key = str(encrypt_data.get("pubKey", "") or "").strip()
            prefix = str(encrypt_data.get("pre", "") or "")
            if not pub_key:
                raise RuntimeError(self._format_login_error("加密公钥为空"))

            login_body = {
                "version": "v2.0",
                "apToken": "",
                "appKey": app_id,
                "accountType": str(app_data.get("accountType", "") or ""),
                "userName": prefix + self._rsa_encrypt_hex(username, pub_key),
                "epd": prefix + self._rsa_encrypt_hex(password, pub_key),
                "captchaType": "",
                "validateCode": "",
                "smsValidateCode": "",
                "captchaToken": "",
                "returnUrl": str(app_data.get("returnUrl", "") or ""),
                "mailSuffix": str(app_data.get("mailSuffix", "") or ""),
                "dynamicCheck": "FALSE",
                "clientType": str(app_data.get("clientType", "") or "10010"),
                "cb_SaveName": "3",
                "isOauth2": "true" if bool(app_data.get("isOauth2", False)) else "false",
                "state": "",
                "paramId": str(app_data.get("paramId", "") or ""),
            }
            submit_resp = session.post(
                "https://open.e.189.cn/api/logbox/oauth2/loginSubmit.do",
                headers=login_headers,
                data=login_body,
                timeout=20,
            )
            submit_resp.raise_for_status()
            login_result = self._login_json(submit_resp)
            try:
                result_code = int(login_result.get("result", -1))
            except (TypeError, ValueError):
                result_code = -1
            if result_code != 0:
                raise RuntimeError(self._format_login_error(login_result.get("msg", "") or "登录失败"))

            to_url = str(login_result.get("toUrl", "") or login_result.get("toURL", "") or "").strip()
            if to_url:
                session.get(to_url, headers=headers, allow_redirects=True, timeout=20)
            cookie = self._session_cookie_header(session)
            if not cookie:
                raise RuntimeError(self._format_login_error("登录成功但未获取到 Cookie"))
            self._login_cookie_cache = {"cookie": cookie, "expires_at": now + 12 * 3600, "cache_key": cache_key}
            return cookie

    def _remember_ipv4_for_cookie(self, cookie: str) -> None:
        cache_key = self._cookie_cache_key(str(cookie or "").strip())
        if cache_key:
            self._cookie_network_family[cache_key] = "ipv4"

    def _should_force_ipv4_for_cookie(self, cookie: str) -> bool:
        cache_key = self._cookie_cache_key(str(cookie or "").strip())
        return bool(cache_key and self._cookie_network_family.get(cache_key) == "ipv4")

    def _request_get(self, url: str, *, force_ipv4: bool = False, **kwargs):
        if not force_ipv4:
            return requests.get(url, **kwargs)
        with self._ipv4_request_lock:
            original_allowed_gai_family = urllib3_connection.allowed_gai_family
            urllib3_connection.allowed_gai_family = lambda: socket.AF_INET
            try:
                return requests.get(url, **kwargs)
            finally:
                urllib3_connection.allowed_gai_family = original_allowed_gai_family

    def _request_post(self, url: str, *, force_ipv4: bool = False, **kwargs):
        if not force_ipv4:
            return requests.post(url, **kwargs)
        with self._ipv4_request_lock:
            original_allowed_gai_family = urllib3_connection.allowed_gai_family
            urllib3_connection.allowed_gai_family = lambda: socket.AF_INET
            try:
                return requests.post(url, **kwargs)
            finally:
                urllib3_connection.allowed_gai_family = original_allowed_gai_family

    def _web_signature(self, params: dict, timestamp: str) -> str:
        sign_params = {
            **{str(k): str(v) for k, v in (params or {}).items() if v is not None},
            "Timestamp": str(timestamp),
            "AppKey": self.web_app_key,
        }
        sign_text = "&".join(f"{key}={sign_params[key]}" for key in sorted(sign_params))
        return hashlib.md5(sign_text.encode("utf-8")).hexdigest()

    def _web_headers(self, cookie: str, params: dict) -> dict:
        timestamp = self._web_timestamp()
        return {
            "Cookie": str(cookie or "").strip(),
            "AppKey": self.web_app_key,
            "Timestamp": timestamp,
            "Sign-Type": "1",
            "Signature": self._web_signature(params, timestamp),
            "Accept": "application/json;charset=UTF-8",
            "Referer": "https://cloud.189.cn/web/main.action",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

    def _web_get_json(self, cookie: str, url: str, params: dict, timeout: int = 30, force_ipv4=None) -> dict:
        normalized_cookie = str(cookie or "").strip()
        if not normalized_cookie:
            raise RuntimeError("请先在参数配置中填写天翼云盘 Cookie")
        use_ipv4 = self._should_force_ipv4_for_cookie(normalized_cookie) if force_ipv4 is None else bool(force_ipv4)
        self.throttle()
        resp = self._request_get(
            url,
            force_ipv4=use_ipv4,
            headers=self._web_headers(normalized_cookie, params),
            params=params,
            timeout=timeout,
        )
        try:
            data = resp.json()
        except (ValueError, requests.JSONDecodeError):
            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                raise RuntimeError(f"天翼云盘请求失败（HTTP {resp.status_code}），Cookie 可能未登录或已过期") from exc
            raise RuntimeError("天翼云盘返回数据异常，Cookie 可能未登录或已过期")
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            detail = self._error_detail(data, f"HTTP {resp.status_code}")
            if self._is_cookie_ip_binding_error(detail):
                raise RuntimeError(self._format_cookie_ip_binding_error(detail)) from exc
            raise RuntimeError(f"天翼云盘请求失败: {detail}") from exc
        return data

    def _assert_web_success(self, data: dict, action: str) -> None:
        if not isinstance(data, dict):
            raise RuntimeError(f"天翼云盘{action}失败：返回数据异常")
        code = data.get("res_code", data.get("resCode", data.get("code", 0)))
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            code_int = 0 if str(code or "").lower() in {"success", "ok"} else -1
        if code_int == 0:
            return
        detail = self._error_detail(data, "Cookie 可能未登录或已过期")
        if self._is_cookie_ip_binding_error(detail):
            raise RuntimeError(self._format_cookie_ip_binding_error(detail))
        raise RuntimeError(f"天翼云盘{action}失败: {detail}")

    def _web_list_items(self, data: dict) -> list:
        source = data.get("fileListAO", data.get("data", {})) if isinstance(data, dict) else {}
        if not isinstance(source, dict):
            source = {}
        merged = []
        for key in ("folderList", "fileList"):
            items = source.get(key)
            if isinstance(items, list):
                merged.extend(items)
        if merged:
            return merged
        for key in ("items", "list"):
            items = source.get(key)
            if isinstance(items, list):
                return items
        return []

    def _web_list_total(self, data: dict, fallback: int) -> int:
        source = data.get("fileListAO", data.get("data", {})) if isinstance(data, dict) else {}
        if not isinstance(source, dict):
            source = {}
        for key in ("count", "total", "totalCount"):
            try:
                total = int(source.get(key) or 0)
            except (TypeError, ValueError):
                total = 0
            if total > 0:
                return total
        return fallback

    def _entry_value(self, item: dict, *keys, default=""):
        if not isinstance(item, dict):
            return default
        for key in keys:
            if key in item and item.get(key) is not None:
                return item.get(key)
        return default

    def _normalize_web_entry(self, item: dict, parent_id: str) -> dict:
        item_id = str(self._entry_value(item, "id", "fileId", "fileIdStr", "fileID", default="") or "")
        name = str(self._entry_value(item, "name", "fileName", "filename", default="") or "")
        icon = str(self._entry_value(item, "icon", "fileIcon", default="") or "").lower()
        media_type = str(self._entry_value(item, "mediaType", "fileCata", "fileType", default="") or "").lower()
        is_dir = bool(
            self._entry_value(item, "isFolder", "folder", default=False)
            or media_type in {"folder", "0"}
            or icon == "folder"
            or str(self._entry_value(item, "fileCata", default="")) == "0"
        )
        try:
            size = int(self._entry_value(item, "size", "fileSize", default=0) or 0)
        except (TypeError, ValueError):
            size = 0
        return {
            "id": item_id,
            "name": name,
            "type": "folder" if is_dir else "file",
            "is_dir": is_dir,
            "cid": item_id if is_dir else "",
            "fid": "" if is_dir else item_id,
            "size": size,
            "parent_id": parent_id or "0",
        }

    def _extract_token_from_mapping(self, payload: dict) -> str:
        if not isinstance(payload, dict):
            return ""
        data = payload.get("data")
        sources = [data, payload] if isinstance(data, dict) else [payload]
        for source in sources:
            for key in ("accessToken", "access_token", "AccessToken"):
                token = str(source.get(key, "") or "").strip()
                if token:
                    return token
        return ""

    def _extract_expires_in_from_mapping(self, payload: dict) -> int:
        if not isinstance(payload, dict):
            return 3600
        data = payload.get("data")
        sources = [data, payload] if isinstance(data, dict) else [payload]
        for source in sources:
            for key in ("expiresIn", "expires_in", "expireIn", "expires"):
                try:
                    expires_in = int(source.get(key) or 0)
                except (TypeError, ValueError):
                    expires_in = 0
                if expires_in > 0:
                    return expires_in
        return 3600

    def _extract_token_from_url(self, url: str) -> str:
        raw_url = str(url or "")
        parsed = urllib.parse.urlparse(raw_url)
        for raw_part in (parsed.query, parsed.fragment):
            query = urllib.parse.parse_qs(raw_part)
            for key in ("accessToken", "access_token", "AccessToken"):
                values = query.get(key) or []
                for value in values:
                    token = str(value or "").strip()
                    if token:
                        return token
        match = re.search(r"(?:accessToken|access_token|AccessToken)=([^&\"'\\s<>#]+)", raw_url)
        if match:
            return urllib.parse.unquote(match.group(1)).strip()
        return ""

    def _extract_token_from_response(self, resp: requests.Response) -> tuple:
        payload = None
        text = str(getattr(resp, "text", "") or "")
        content_type = str(getattr(resp, "headers", {}).get("Content-Type", "") or "").lower()
        if "json" in content_type or text.lstrip().startswith("{"):
            try:
                payload = resp.json()
            except (ValueError, requests.JSONDecodeError):
                payload = None
        if isinstance(payload, dict):
            token = self._extract_token_from_mapping(payload)
            if token:
                return token, self._extract_expires_in_from_mapping(payload)

        urls = []
        for item in list(getattr(resp, "history", []) or []):
            location = str(getattr(item, "headers", {}).get("Location", "") or "").strip()
            if location:
                urls.append(location)
            history_url = str(getattr(item, "url", "") or "").strip()
            if history_url:
                urls.append(history_url)
        urls.append(str(getattr(resp, "url", "") or ""))
        for url in urls:
            token = self._extract_token_from_url(url)
            if token:
                return token, 3600

        match = re.search(r"(?:accessToken|access_token|AccessToken)=([^&\"'\\s<>]+)", text)
        if match:
            token = urllib.parse.unquote(match.group(1)).strip()
            if token:
                return token, 3600
        return "", 3600

    def _ensure_token(self, cookie: str, force_ipv4=None) -> str:
        now = time.time()
        normalized_cookie = str(cookie or "").strip()
        if not normalized_cookie:
            raise RuntimeError("请先在参数配置中填写天翼云盘 Cookie")
        cache_key = self._cookie_cache_key(normalized_cookie)
        use_ipv4 = self._should_force_ipv4_for_cookie(normalized_cookie) if force_ipv4 is None else bool(force_ipv4)
        with self._token_lock:
            if self._token_cache and self._token_cache_key == cache_key and now < self._token_expiry - 60:
                return self._token_cache
            resp = self._request_get(
                "https://api.cloud.189.cn/open/oauth2/ssoH5.action",
                force_ipv4=use_ipv4,
                headers={
                    "Cookie": normalized_cookie,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                },
                allow_redirects=True,
                timeout=15,
            )
            resp.raise_for_status()
            token, expires_in = self._extract_token_from_response(resp)
            if not token:
                text = str(getattr(resp, "text", "") or "")
                if self._is_cookie_ip_binding_error(text):
                    raise RuntimeError(self._format_cookie_ip_binding_error(text))
                raise RuntimeError("天翼云盘 AccessToken 获取失败，请检查 Cookie 是否来自已登录的 cloud.189.cn")
            self._token_cache = token
            self._token_cache_key = cache_key
            self._token_expiry = now + expires_in
            return token

    def _api_headers(self, cookie: str, force_ipv4=None) -> dict:
        token = self._ensure_token(cookie, force_ipv4=force_ipv4)
        return {
            "Cookie": cookie,
            "AccessToken": token,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json;charset=UTF-8",
        }

    def _open_get_json(self, cookie: str, url: str, params: dict, timeout: int = 30, force_ipv4=None) -> dict:
        use_ipv4 = self._should_force_ipv4_for_cookie(cookie) if force_ipv4 is None else bool(force_ipv4)
        self.throttle()
        resp = self._request_get(
            url,
            force_ipv4=use_ipv4,
            headers=self._api_headers(cookie, force_ipv4=use_ipv4),
            params=params,
            timeout=timeout,
        )
        try:
            data = resp.json()
        except (ValueError, requests.JSONDecodeError):
            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                raise RuntimeError(f"天翼云盘请求失败（HTTP {resp.status_code}），Cookie 可能未登录或已过期") from exc
            raise RuntimeError("天翼云盘返回数据异常，Cookie 可能未登录或已过期")
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            detail = self._error_detail(data, f"HTTP {resp.status_code}")
            if self._is_cookie_ip_binding_error(detail):
                raise RuntimeError(self._format_cookie_ip_binding_error(detail)) from exc
            raise RuntimeError(f"天翼云盘请求失败: {detail}") from exc
        return data

    def _normalize_open_entry(self, item: dict, parent_id: str) -> dict:
        item_id = str(self._entry_value(item, "fileId", "fileIdStr", "id", "fileID", default="") or "")
        name = str(self._entry_value(item, "fileName", "name", "filename", default="") or "")
        is_dir = bool(self._entry_value(item, "isFolder", "folder", default=False))
        try:
            size = int(self._entry_value(item, "fileSize", "size", default=0) or 0)
        except (TypeError, ValueError):
            size = 0
        return {
            "id": item_id,
            "name": name,
            "type": "folder" if is_dir else "file",
            "is_dir": is_dir,
            "cid": item_id if is_dir else "",
            "fid": "" if is_dir else item_id,
            "size": size,
            "parent_id": parent_id or "0",
        }

    def _list_entries_payload_via_open_api(self, cookie, cid="0", folders_only=False, force_ipv4=None):
        folder_id = self._normalize_open_folder_id(cid)
        params = {
            "folderId": folder_id,
            "pageNum": 1,
            "pageSize": 200,
            "orderBy": "lastOpTime",
            "descending": "true",
            "recursive": "false",
        }
        if folders_only:
            params["mediaType"] = "folder"
        data = self._open_get_json(
            cookie,
            "https://api.cloud.189.cn/open/file/listFiles.action",
            params=params,
            timeout=30,
            force_ipv4=force_ipv4,
        )
        self._assert_web_success(data, "读取目录")
        entries = []
        source = data.get("data", {}) if isinstance(data, dict) else {}
        items = source.get("items", []) if isinstance(source, dict) else []
        for item in (items if isinstance(items, list) else []):
            entry = self._normalize_open_entry(item, cid or "0")
            if folders_only and not entry["is_dir"]:
                continue
            entries.append(entry)
        folder_count = sum(1 for e in entries if e.get("is_dir"))
        file_count = sum(1 for e in entries if not e.get("is_dir"))
        return {
            "entries": entries,
            "total": int(source.get("total", len(entries)) or len(entries)) if isinstance(source, dict) else len(entries),
            "summary": {
                "folder_count": folder_count,
                "file_count": file_count,
            },
        }

    def list_entries_payload(self, cookie, cid="0", folders_only=False):
        folder_id = self._normalize_folder_id(cid)
        params = {
            "folderId": folder_id,
            "orderBy": "lastOpTime",
            "descending": "true",
            "pageNum": 1,
            "pageSize": 100,
        }
        if folders_only:
            params["mediaType"] = "folder"
        try:
            data = self._web_get_json(
                cookie,
                "https://cloud.189.cn/api/open/file/listFiles.action",
                params=params,
                timeout=30,
            )
            self._assert_web_success(data, "读取目录")
        except RuntimeError as exc:
            if self._is_cookie_ip_binding_error(str(exc)):
                if self._should_retry_ipv4_for_ip_error(str(exc)):
                    try:
                        data = self._web_get_json(
                            cookie,
                            "https://cloud.189.cn/api/open/file/listFiles.action",
                            params=params,
                            timeout=30,
                            force_ipv4=True,
                        )
                        self._assert_web_success(data, "读取目录")
                        self._remember_ipv4_for_cookie(cookie)
                    except Exception:
                        try:
                            return self._list_entries_payload_via_open_api(cookie, cid, folders_only, force_ipv4=True)
                        except Exception as fallback_exc:
                            raise exc from fallback_exc
                else:
                    try:
                        return self._list_entries_payload_via_open_api(cookie, cid, folders_only)
                    except Exception as fallback_exc:
                        raise exc from fallback_exc
            else:
                raise
        entries = []
        for item in self._web_list_items(data):
            entry = self._normalize_web_entry(item, cid or "0")
            if folders_only and not entry["is_dir"]:
                continue
            entries.append(entry)
        folder_count = sum(1 for e in entries if e.get("is_dir"))
        file_count = sum(1 for e in entries if not e.get("is_dir"))
        return {
            "entries": entries,
            "total": self._web_list_total(data, len(entries)),
            "summary": {
                "folder_count": folder_count,
                "file_count": file_count,
            },
        }

    def list_entries(self, cookie, cid="0"):
        payload = self.list_entries_payload(cookie, cid)
        return payload["entries"]

    def create_folder(self, cookie, cid="0", folder_name=""):
        use_ipv4 = self._should_force_ipv4_for_cookie(cookie)
        self.throttle()
        resp = self._request_post(
            "https://api.cloud.189.cn/open/file/createFolder.action",
            force_ipv4=use_ipv4,
            headers=self._api_headers(cookie, force_ipv4=use_ipv4),
            data={
                "parentFileId": cid or "0",
                "fileName": folder_name,
            },
            timeout=15,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except (ValueError, requests.JSONDecodeError):
            raise RuntimeError("天翼云盘返回数据异常")
        if data.get("res_code") != 0:
            raise RuntimeError(f"天翼云盘创建文件夹失败: {data.get('res_msg', '')}")
        return {"cid": str(data.get("fileId", "")), "name": folder_name}

    def resolve_folder_id_by_path(self, cookie, relative_path):
        parts = [p.strip() for p in str(relative_path).split("/") if p.strip()]
        cid = "0"
        for name in parts:
            entries = self.list_entries(cookie, cid)
            found = next((e for e in entries if e.get("name") == name), None)
            if not found:
                return ""
            cid = found["id"]
        return cid

    def ensure_folder_id_by_path(self, cookie, relative_path):
        parts = [p.strip() for p in str(relative_path).split("/") if p.strip()]
        cid = "0"
        for name in parts:
            entries = self.list_entries(cookie, cid)
            found = next((e for e in entries if e.get("name") == name), None)
            if found:
                cid = found["id"]
            else:
                result = self.create_folder(cookie, cid, name)
                cid = result["cid"]
        return cid

    def resolve_share_payload(self, cookie, share_url, raw_text="", receive_code=""):
        share_code_match = re.search(r'/s/([A-Za-z0-9]+)', str(share_url))
        if not share_code_match:
            raise RuntimeError("无法识别天翼云盘分享链接")
        return {
            "share_code": share_code_match.group(1),
            "receive_code": str(receive_code or "").strip(),
        }

    def list_share_entries(self, cookie, share_payload, cid="0", offset=0, limit=200):
        share_code = share_payload["share_code"]
        receive_code = share_payload.get("receive_code", "")
        use_ipv4 = self._should_force_ipv4_for_cookie(cookie)
        self.throttle()
        resp = self._request_post(
            "https://api.cloud.189.cn/open/share/listShareDir.action",
            force_ipv4=use_ipv4,
            headers=self._api_headers(cookie, force_ipv4=use_ipv4),
            data={
                "shareCode": share_code,
                "accessCode": receive_code,
                "fileId": cid or "0",
                "pageNum": 1,
                "pageSize": limit,
            },
            timeout=30,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except (ValueError, requests.JSONDecodeError):
            raise RuntimeError("天翼云盘返回数据异常")
        if data.get("res_code") != 0:
            raise RuntimeError(f"天翼云盘读取分享目录失败: {data.get('res_msg', '')}")
        entries = []
        for item in data.get("data", {}).get("items", []):
            is_dir = bool(item.get("isFolder"))
            item_id = str(item.get("fileId", ""))
            entries.append({
                "id": str(item.get("fileId", "")),
                "name": str(item.get("fileName", "")),
                "type": "folder" if item.get("isFolder") else "file",
                "is_dir": is_dir,
                "cid": item_id if is_dir else "",
                "fid": "" if is_dir else item_id,
                "size": int(item.get("fileSize", 0) or 0),
                "parent_id": cid or "0",
                "share_id": share_code,
            })
        folder_count = sum(1 for e in entries if e.get("is_dir"))
        file_count = sum(1 for e in entries if not e.get("is_dir"))
        return {
            "entries": entries,
            "total": data.get("data", {}).get("total", len(entries)),
            "share": dict(share_payload),
            "share_title": str(data.get("data", {}).get("shareName", "") or "").strip(),
            "summary": {
                "folder_count": folder_count,
                "file_count": file_count,
            },
        }

    def prepare_share_receive(self, cookie, share_payload, cid="0"):
        return {**share_payload, "target_cid": cid or "0"}

    def submit_share_receive(self, cookie, receive_payload, files):
        share_code = receive_payload["share_code"]
        receive_code = receive_payload.get("receive_code", "")
        target_cid = receive_payload.get("target_cid", "0")
        file_ids = [
            str(f.get("id", "")).strip()
            for f in (files or [])
            if str(f.get("id", "")).strip()
        ]
        if not file_ids:
            raise RuntimeError("未选择要转存的文件")

        use_ipv4 = self._should_force_ipv4_for_cookie(cookie)
        self.throttle()
        resp = self._request_post(
            "https://api.cloud.189.cn/open/share/saveShareFiles.action",
            force_ipv4=use_ipv4,
            headers=self._api_headers(cookie, force_ipv4=use_ipv4),
            data={
                "shareCode": share_code,
                "accessCode": receive_code,
                "targetFileId": target_cid,
                "fileIds": ",".join(file_ids),
            },
            timeout=60,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except (ValueError, requests.JSONDecodeError):
            raise RuntimeError("天翼云盘返回数据异常")
        if data.get("res_code") != 0:
            raise RuntimeError(f"天翼云盘转存失败: {data.get('res_msg', '')}")
        return {"success": True, "count": len(file_ids)}

    def probe_connectivity(self, cookie):
        self.list_entries_payload(cookie, self.root_folder_id, folders_only=False)
        return True

    def rename_entry(self, cookie, entry_id, new_name, parent_id=""):
        """重命名文件/文件夹"""
        use_ipv4 = self._should_force_ipv4_for_cookie(cookie)
        self.throttle()
        resp = self._request_post(
            "https://api.cloud.189.cn/open/file/renameFile.action",
            force_ipv4=use_ipv4,
            headers=self._api_headers(cookie, force_ipv4=use_ipv4),
            data={
                "fileId": str(entry_id),
                "destFileName": str(new_name),
            },
            timeout=30,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except (ValueError, requests.JSONDecodeError):
            raise RuntimeError("天翼云盘返回数据异常")
        if data.get("res_code") != 0:
            raise RuntimeError(f"天翼云盘重命名失败: {data.get('res_msg', '')}")
        return {"ok": True, "id": str(entry_id), "name": str(new_name)}

    def move_entries(self, cookie, entry_ids, target_id, source_id=""):
        """移动文件/文件夹（天翼云盘仅支持单文件移动，逐个调用）"""
        entry_ids = [str(e) for e in entry_ids]
        target_id = str(target_id or "0")
        use_ipv4 = self._should_force_ipv4_for_cookie(cookie)
        self.throttle()
        for eid in entry_ids:
            resp = self._request_post(
                "https://api.cloud.189.cn/open/file/moveFile.action",
                force_ipv4=use_ipv4,
                headers=self._api_headers(cookie, force_ipv4=use_ipv4),
                data={
                    "fileId": eid,
                    "destParentFolderId": target_id,
                },
                timeout=30,
            )
            resp.raise_for_status()
            try:
                data = resp.json()
            except (ValueError, requests.JSONDecodeError):
                raise RuntimeError("天翼云盘返回数据异常")
            if data.get("res_code") != 0:
                raise RuntimeError(f"天翼云盘移动失败: {data.get('res_msg', '')}")
        return {"ok": True, "ids": entry_ids, "target_cid": target_id}

    def copy_entries(self, cookie, entry_ids, target_id, source_id=""):
        """复制文件/文件夹（天翼云盘仅支持单文件复制，逐个调用）"""
        entry_ids = [str(e) for e in entry_ids]
        target_id = str(target_id or "0")
        use_ipv4 = self._should_force_ipv4_for_cookie(cookie)
        self.throttle()
        for eid in entry_ids:
            resp = self._request_post(
                "https://api.cloud.189.cn/open/file/copyFile.action",
                force_ipv4=use_ipv4,
                headers=self._api_headers(cookie, force_ipv4=use_ipv4),
                data={
                    "fileId": eid,
                    "destParentFolderId": target_id,
                },
                timeout=30,
            )
            resp.raise_for_status()
            try:
                data = resp.json()
            except (ValueError, requests.JSONDecodeError):
                raise RuntimeError("天翼云盘返回数据异常")
            if data.get("res_code") != 0:
                raise RuntimeError(f"天翼云盘复制失败: {data.get('res_msg', '')}")
        return {"ok": True, "ids": entry_ids, "target_cid": target_id}

    def delete_entries(self, cookie, entry_ids, parent_id=""):
        """删除文件/文件夹"""
        entry_ids = [str(e) for e in entry_ids]
        use_ipv4 = self._should_force_ipv4_for_cookie(cookie)
        self.throttle()
        for eid in entry_ids:
            resp = self._request_post(
                "https://api.cloud.189.cn/open/file/deleteFile.action",
                force_ipv4=use_ipv4,
                headers=self._api_headers(cookie, force_ipv4=use_ipv4),
                data={
                    "fileId": eid,
                },
                timeout=30,
            )
            resp.raise_for_status()
            # 天翼云盘删除成功返回空 body
            if not resp.text.strip():
                continue
            try:
                data = resp.json()
            except (ValueError, requests.JSONDecodeError):
                raise RuntimeError("天翼云盘返回数据异常")
            if data.get("res_code") != 0:
                raise RuntimeError(f"天翼云盘删除失败: {data.get('res_msg', '')}")
        return {"ok": True, "ids": entry_ids}


register(TianyiProvider())
