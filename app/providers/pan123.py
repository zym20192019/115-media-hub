import hashlib
import random
import re
import threading
import time
import urllib.parse
import zlib

import requests

from .base import CloudProvider
from .registry import register


class Pan123Provider(CloudProvider):
    name = "123pan"
    label = "123云盘"
    link_type = "123pan"
    auth_type = "password"
    config_keys = ["123pan_username", "123pan_password"]
    supports_subscription = True
    supports_offline = True
    supports_fixed_share_link = True
    supports_rename = True
    supports_move = True
    supports_copy = False
    supports_delete = True
    drive_id = 0
    app_version = "3"
    platform = "web"

    def __init__(self):
        super().__init__()
        self._auth_token = None
        self._auth_lock = threading.Lock()

    def _credential_cache_key(self, username: str, password: str) -> str:
        password_fingerprint = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return f"{username}|{password_fingerprint}"

    def _sign_path(self, path: str) -> tuple:
        table = ["a", "d", "e", "f", "g", "h", "l", "m", "y", "i", "j", "n", "o", "p", "k", "q", "r", "s", "t", "u", "b", "c", "v", "w", "s", "z"]
        random_value = str(round(1e7 * random.random()))
        now = time.time()
        timestamp = str(int(now))
        cst = time.gmtime(now + 8 * 3600)
        now_text = time.strftime("%Y%m%d%H%M", cst)
        mapped_time = "".join(table[int(ch)] for ch in now_text)
        time_sign = str(zlib.crc32(mapped_time.encode("utf-8")) & 0xFFFFFFFF)
        data = "|".join([timestamp, random_value, str(path or ""), self.platform, self.app_version, time_sign])
        data_sign = str(zlib.crc32(data.encode("utf-8")) & 0xFFFFFFFF)
        return time_sign, "-".join([timestamp, random_value, data_sign])

    def _signed_api_url(self, raw_url: str) -> str:
        parsed = urllib.parse.urlparse(str(raw_url or ""))
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query.append(self._sign_path(parsed.path))
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))

    def _extract_auth_token(self, payload: dict) -> str:
        if not isinstance(payload, dict):
            return ""
        data = payload.get("data")
        sources = [data, payload] if isinstance(data, dict) else [payload]
        for source in sources:
            for key in ("token", "Token", "accessToken", "access_token", "loginToken", "jwt"):
                token = str(source.get(key, "") or "").strip()
                if token:
                    if token.lower().startswith("bearer "):
                        token = token[7:].strip()
                    return token
        return ""

    def _response_code(self, payload: dict) -> int:
        if not isinstance(payload, dict):
            return -1
        try:
            return int(payload.get("code", -1))
        except (TypeError, ValueError):
            return -1

    def _response_data(self, payload: dict) -> dict:
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        return data if isinstance(data, dict) else {}

    def _extract_item_list(self, payload: dict) -> list:
        data = self._response_data(payload)
        for key in ("infoList", "InfoList", "fileList", "FileList", "list", "List"):
            items = data.get(key)
            if isinstance(items, list):
                return items
        return []

    def _item_value(self, item: dict, *keys, default=""):
        if not isinstance(item, dict):
            return default
        for key in keys:
            if key in item and item.get(key) is not None:
                return item.get(key)
        return default

    def _int_or_zero(self, value) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _require_positive_int_id(self, value, action: str) -> int:
        try:
            numeric_id = int(str(value or "").strip())
        except (TypeError, ValueError):
            numeric_id = 0
        if numeric_id <= 0:
            raise RuntimeError(f"123云盘{action}失败：文件 ID 无效")
        return numeric_id

    def _normalize_entry_ids(self, entry_ids, action: str) -> list:
        ids = [self._require_positive_int_id(item, action) for item in (entry_ids or [])]
        if not ids:
            raise RuntimeError(f"123云盘{action}失败：请选择要操作的文件")
        return ids

    def _is_folder_item(self, item: dict) -> bool:
        raw_type = self._item_value(item, "type", "Type", "fileType", "FileType", default=0)
        try:
            return int(raw_type or 0) == 1
        except (TypeError, ValueError):
            return str(raw_type or "").strip().lower() in {"folder", "dir", "directory"}

    def _normalize_entry(self, item: dict, cid: str) -> dict:
        item_id = str(self._item_value(item, "fileId", "fileID", "FileId", "FileID", "FileIdStr", "id", "Id", default="") or "")
        name = str(self._item_value(item, "fileName", "FileName", "filename", "Filename", "name", "Name", default="") or "")
        is_dir = self._is_folder_item(item)
        try:
            size = int(self._item_value(item, "size", "Size", "fileSize", "FileSize", default=0) or 0)
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
            "parent_id": cid or "0",
        }

    def _extract_created_folder_id(self, payload: dict) -> str:
        data = self._response_data(payload)
        sources = [data]
        for key in ("info", "Info", "file", "File"):
            nested = data.get(key)
            if isinstance(nested, dict):
                sources.append(nested)
        for source in sources:
            for key in ("fileId", "fileID", "FileId", "FileID", "FileIdStr", "dirId", "DirId", "id", "Id"):
                value = source.get(key) if isinstance(source, dict) else ""
                if value is not None and str(value).strip():
                    return str(value).strip()
        return ""

    def _ensure_token(self, cfg: dict) -> str:
        """通过账号密码登录获取 auth token，缓存至过期"""
        now = time.time()
        username = str(cfg.get("123pan_username", "")).strip()
        password = str(cfg.get("123pan_password", "")).strip()
        if not username or not password:
            raise RuntimeError("请先在参数配置中填写 123云盘 账号和密码")
        cache_key = self._credential_cache_key(username, password)

        with self._auth_lock:
            if (
                self._auth_token
                and self._auth_token.get("cache_key") == cache_key
                and now < self._auth_token.get("expires_at", 0) - 300
            ):
                return self._auth_token["token"]

            if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", username):
                login_body = {"mail": username, "password": password, "type": 2}
            else:
                login_body = {"passport": username, "password": password, "remember": True}

            resp = requests.post(
                "https://login.123pan.com/api/user/sign_in",
                headers={
                    "User-Agent": "Dart/2.19(dart:io)-115-media-hub",
                    "Content-Type": "application/json",
                    "Origin": "https://www.123pan.com",
                    "Referer": "https://www.123pan.com/",
                    "platform": self.platform,
                    "app-version": self.app_version,
                },
                json=login_body,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            code = self._response_code(data)
            if code != 0 and code != 200:
                raise RuntimeError(f"123云盘登录失败: {data.get('message', '未知错误')}")

            token = self._extract_auth_token(data)
            if not token:
                raise RuntimeError("123云盘登录失败：未获取到 token")

            self._auth_token = {"token": token, "expires_at": now + 86400, "cache_key": cache_key}
            return token

    def get_cookie(self, cfg: dict) -> str:
        return self._ensure_token(cfg)

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "platform": self.platform,
            "app-version": self.app_version,
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) 115-media-hub",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.123pan.com/",
            "Origin": "https://www.123pan.com",
        }

    def _api_call(self, token: str, method: str, url: str, **kwargs) -> dict:
        self.throttle()
        headers = self._headers(token)
        timeout = kwargs.pop("timeout", 30)
        signed_url = self._signed_api_url(url)
        if method == "GET":
            resp = requests.get(signed_url, headers=headers, timeout=timeout, **kwargs)
        else:
            resp = requests.post(signed_url, headers=headers, timeout=timeout, **kwargs)
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.text.strip():
            return {}
        try:
            data = resp.json()
        except (ValueError, Exception):
            raise RuntimeError("123云盘返回数据异常")
        code = self._response_code(data)
        if code != 0 and code != 200:
            raise RuntimeError(f"123云盘 API 错误: {data.get('message', '未知错误')}")
        return data

    def list_entries_payload(self, cookie, cid="0", folders_only=False):
        data = self._api_call(
            cookie, "GET",
            "https://www.123pan.com/b/api/file/list/new",
            params={
                "driveId": str(self.drive_id),
                "limit": "100",
                "next": "0",
                "orderBy": "file_id",
                "orderDirection": "desc",
                "parentFileId": str(self._int_or_zero(cid)),
                "trashed": "false",
                "SearchData": "",
                "Page": "1",
                "OnlyLookAbnormalFile": "0",
                "event": "homeListFile",
                "operateType": "4",
                "inDirectSpace": "false",
            },
        )
        entries = []
        items = self._extract_item_list(data)
        for item in items:
            entry = self._normalize_entry(item, cid)
            if folders_only and not entry["is_dir"]:
                continue
            entries.append(entry)
        folder_count = sum(1 for e in entries if e.get("is_dir"))
        file_count = sum(1 for e in entries if not e.get("is_dir"))
        payload_data = self._response_data(data)
        total = self._int_or_zero(payload_data.get("total", payload_data.get("Total", len(entries))))
        return {
            "entries": entries,
            "total": total or len(entries),
            "summary": {
                "folder_count": folder_count,
                "file_count": file_count,
            },
        }

    def list_entries(self, cookie, cid="0"):
        return self.list_entries_payload(cookie, cid)["entries"]

    def create_folder(self, cookie, cid="0", folder_name=""):
        data = self._api_call(
            cookie, "POST",
            "https://www.123pan.com/b/api/file/upload_request",
            json={
                "driveId": self.drive_id,
                "DriveId": self.drive_id,
                "etag": "",
                "fileName": folder_name,
                "parentFileId": self._int_or_zero(cid),
                "size": 0,
                "type": 1,
            },
        )
        folder_id = self._extract_created_folder_id(data)
        if not folder_id:
            raise RuntimeError("123云盘创建文件夹失败：未获取到文件夹 ID")
        return {"cid": folder_id, "name": folder_name}

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
            raise RuntimeError("无法识别123云盘分享链接")
        return {
            "share_code": share_code_match.group(1),
            "receive_code": str(receive_code or "").strip(),
        }

    def list_share_entries(self, cookie, share_payload, cid="0", offset=0, limit=200):
        share_code = share_payload["share_code"]
        receive_code = share_payload.get("receive_code", "")
        url = f"https://www.123pan.com/a/api/share/info?shareKey={share_code}"
        if receive_code:
            url += f"&sharePwd={receive_code}"
        data = self._api_call(cookie, "GET", url)
        entries = []
        items = self._extract_item_list(data)
        for item in items:
            entry = self._normalize_entry(item, cid)
            entry["share_id"] = share_code
            entries.append(entry)
        folder_count = sum(1 for e in entries if e.get("is_dir"))
        file_count = sum(1 for e in entries if not e.get("is_dir"))
        return {
            "entries": entries,
            "total": len(entries),
            "summary": {
                "folder_count": folder_count,
                "file_count": file_count,
            },
            "share": dict(share_payload),
            "share_title": str(share_payload.get("title", "") or share_payload.get("share_name", "") or "").strip(),
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

        data = self._api_call(
            cookie, "POST",
            "https://www.123pan.com/a/api/share/save",
            json={
                "shareKey": share_code,
                "sharePwd": receive_code,
                "driveId": self.drive_id,
                "DriveId": self.drive_id,
                "dirId": self._int_or_zero(target_cid),
                "fileIdList": [int(fid) for fid in file_ids],
            },
            timeout=60,
        )
        return {"success": True, "count": len(file_ids)}

    def submit_offline_task(self, cookie, resource_url, folder_id="0"):
        data = self._api_call(
            cookie, "POST",
            "https://www.123pan.com/a/api/offline/download",
            json={"url": resource_url, "driveId": self.drive_id, "DriveId": self.drive_id, "dirId": self._int_or_zero(folder_id)},
            timeout=30,
        )
        return {"task_id": str(data.get("data", {}).get("taskId", ""))}

    def probe_connectivity(self, cookie):
        self._api_call(cookie, "GET", "https://www.123pan.com/b/api/user/info")
        return True

    def rename_entry(self, cookie, entry_id, new_name, parent_id=""):
        numeric_id = self._require_positive_int_id(entry_id, "重命名")
        self._api_call(
            cookie, "POST",
            "https://www.123pan.com/b/api/file/rename",
            json={"driveId": self.drive_id, "DriveId": self.drive_id, "fileId": numeric_id, "fileName": new_name.strip()},
        )
        return {"ok": True, "id": entry_id, "name": new_name}

    def move_entries(self, cookie, entry_ids, target_id, source_id=""):
        numeric_ids = self._normalize_entry_ids(entry_ids, "移动")
        self._api_call(
            cookie, "POST",
            "https://www.123pan.com/b/api/file/mod_pid",
            json={
                "driveId": self.drive_id,
                "DriveId": self.drive_id,
                "fileIdList": [{"FileId": item_id} for item_id in numeric_ids],
                "parentFileId": self._int_or_zero(target_id),
            },
        )
        return {"ok": True, "ids": entry_ids, "target_cid": target_id}

    def copy_entries(self, cookie, entry_ids, target_id, source_id=""):
        raise RuntimeError("123云盘暂不支持复制")

    def delete_entries(self, cookie, entry_ids, parent_id=""):
        numeric_ids = self._normalize_entry_ids(entry_ids, "删除")
        self._api_call(
            cookie, "POST",
            "https://www.123pan.com/b/api/file/trash",
            json={
                "driveId": self.drive_id,
                "DriveId": self.drive_id,
                "fileIdList": numeric_ids,
                "operation": True,
                "Operation": True,
            },
        )
        return {"ok": True, "ids": entry_ids}

register(Pan123Provider())
