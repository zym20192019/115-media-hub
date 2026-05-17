import logging
import re
import threading
import time

import requests

from .base import CloudProvider
from .registry import register


class TianyiProvider(CloudProvider):
    name = "tianyi"
    label = "天翼云盘"
    link_type = "tianyi"
    auth_type = "oauth2"
    config_keys = ["cookie_tianyi"]
    supports_subscription = True
    supports_offline = False
    supports_fixed_share_link = True

    def __init__(self):
        super().__init__()
        self._token_cache = None
        self._token_expiry = 0.0
        self._token_lock = threading.Lock()

    def _ensure_token(self, cookie: str) -> str:
        now = time.time()
        with self._token_lock:
            if self._token_cache and now < self._token_expiry - 60:
                return self._token_cache
            resp = requests.get(
                "https://api.cloud.189.cn/open/oauth2/ssoH5.action",
                headers={
                    "Cookie": cookie,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("accessToken") or data.get("access_token")
            if not token:
                raise RuntimeError("天翼云盘 AccessToken 获取失败，请检查 Cookie 是否有效")
            expires_in = int(data.get("expiresIn") or data.get("expires_in") or 3600)
            self._token_cache = token
            self._token_expiry = now + expires_in
            return token

    def _api_headers(self, cookie: str) -> dict:
        token = self._ensure_token(cookie)
        return {
            "Cookie": cookie,
            "AccessToken": token,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json;charset=UTF-8",
        }

    def list_entries_payload(self, cookie, cid="0", folders_only=False):
        self.throttle()
        params = {
            "folderId": cid or "0",
            "pageNum": 1,
            "pageSize": 200,
            "orderBy": "lastOpTime",
            "descending": "true",
            "recursive": "false",
        }
        if folders_only:
            params["mediaType"] = "folder"
        resp = requests.get(
            "https://api.cloud.189.cn/open/file/listFiles.action",
            headers=self._api_headers(cookie),
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
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
            })
        return {
            "entries": entries,
            "total": data.get("data", {}).get("total", len(entries)),
        }

    def list_entries(self, cookie, cid="0"):
        payload = self.list_entries_payload(cookie, cid)
        return payload["entries"]

    def create_folder(self, cookie, cid="0", folder_name=""):
        self.throttle()
        resp = requests.post(
            "https://api.cloud.189.cn/open/file/createFolder.action",
            headers=self._api_headers(cookie),
            data={
                "parentFileId": cid or "0",
                "fileName": folder_name,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
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
        self.throttle()
        resp = requests.post(
            "https://api.cloud.189.cn/open/share/listShareDir.action",
            headers=self._api_headers(cookie),
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
        data = resp.json()
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
        return {
            "entries": entries,
            "total": data.get("data", {}).get("total", len(entries)),
            "share": share_payload,
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

        self.throttle()
        resp = requests.post(
            "https://api.cloud.189.cn/open/share/saveShareFiles.action",
            headers=self._api_headers(cookie),
            data={
                "shareCode": share_code,
                "accessCode": receive_code,
                "targetFileId": target_cid,
                "fileIds": ",".join(file_ids),
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("res_code") != 0:
            raise RuntimeError(f"天翼云盘转存失败: {data.get('res_msg', '')}")
        return {"success": True, "count": len(file_ids)}

    def probe_connectivity(self, cookie):
        try:
            self._ensure_token(cookie)
            return True
        except Exception as e:
            logging.warning(f"天翼云盘连接检测失败: {e}")
            return False


register(TianyiProvider())
