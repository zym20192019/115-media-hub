import logging
import re
import threading
import time

import requests

from .base import CloudProvider
from .registry import register


class Pan123Provider(CloudProvider):
    name = "123pan"
    label = "123云盘"
    link_type = "123pan"
    auth_type = "cookie"
    config_keys = ["123pan_username", "123pan_password"]
    supports_subscription = True
    supports_offline = True
    supports_fixed_share_link = True
    supports_rename = True
    supports_move = True
    supports_copy = True
    supports_delete = True

    def __init__(self):
        super().__init__()
        self._auth_token = None
        self._auth_lock = threading.Lock()

    def _ensure_token(self, cfg: dict) -> str:
        """通过账号密码登录获取 auth token，缓存至过期"""
        now = time.time()
        with self._auth_lock:
            if self._auth_token and now < self._auth_token.get("expires_at", 0) - 300:
                return self._auth_token["token"]

        username = str(cfg.get("123pan_username", "")).strip()
        password = str(cfg.get("123pan_password", "")).strip()
        if not username or not password:
            raise RuntimeError("请先在参数配置中填写 123云盘 账号和密码")

        resp = requests.post(
            "https://www.123pan.com/a/api/user/sign_in",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Content-Type": "application/json",
                "Origin": "https://www.123pan.com",
                "Referer": "https://www.123pan.com/",
            },
            json={"username": username, "password": password},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        code = int(data.get("code", -1) or -1)
        if code != 0 and code != 200:
            raise RuntimeError(f"123云盘登录失败: {data.get('message', '未知错误')}")

        token = str(data.get("data", {}).get("token", "")).strip()
        if not token:
            raise RuntimeError("123云盘登录失败：未获取到 token")

        self._auth_token = {"token": token, "expires_at": now + 86400}
        return token

    def get_cookie(self, cfg: dict) -> str:
        return self._ensure_token(cfg)

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.123pan.com/",
            "Origin": "https://www.123pan.com",
        }

    def _api_call(self, token: str, method: str, url: str, **kwargs) -> dict:
        self.throttle()
        headers = self._headers(token)
        timeout = kwargs.pop("timeout", 30)
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=timeout, **kwargs)
        else:
            resp = requests.post(url, headers=headers, timeout=timeout, **kwargs)
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.text.strip():
            return {}
        try:
            data = resp.json()
        except (ValueError, Exception):
            raise RuntimeError("123云盘返回数据异常")
        code = int(data.get("code", -1) or -1)
        if code != 0 and code != 200:
            raise RuntimeError(f"123云盘 API 错误: {data.get('message', '未知错误')}")
        return data

    def list_entries_payload(self, cookie, cid="0", folders_only=False):
        data = self._api_call(
            cookie, "GET",
            f"https://www.123pan.com/a/api/file/list/new?dirID={cid or '0'}&page=1&size=200",
        )
        entries = []
        items = data.get("data", {}).get("infoList", [])
        for item in items:
            entry_type = "folder" if int(item.get("type", 0) or 0) == 1 else "file"
            if folders_only and entry_type != "folder":
                continue
            entries.append({
                "id": str(item.get("fileId", item.get("fileID", ""))),
                "name": str(item.get("fileName", "")),
                "type": entry_type,
                "is_dir": entry_type == "folder",
                "cid": str(item.get("fileId", item.get("fileID", ""))) if entry_type == "folder" else "",
                "fid": "" if entry_type == "folder" else str(item.get("fileId", item.get("fileID", ""))),
                "size": int(item.get("size", 0) or 0),
                "parent_id": cid or "0",
            })
        folder_count = sum(1 for e in entries if e.get("is_dir"))
        file_count = sum(1 for e in entries if not e.get("is_dir"))
        return {
            "entries": entries,
            "total": len(entries),
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
            "https://www.123pan.com/a/api/file/upload/mkdir",
            json={"driveId": 0, "dirId": int(cid or 0), "name": folder_name},
        )
        return {"cid": str(data.get("data", {}).get("dirId", "")), "name": folder_name}

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
        items = data.get("data", {}).get("infoList", [])
        for item in items:
            entry_type = "folder" if int(item.get("type", 0) or 0) == 1 else "file"
            item_id = str(item.get("fileId", item.get("fileID", "")))
            entries.append({
                "id": str(item.get("fileId", item.get("fileID", ""))),
                "name": str(item.get("fileName", "")),
                "type": entry_type,
                "is_dir": entry_type == "folder",
                "cid": item_id if entry_type == "folder" else "",
                "fid": "" if entry_type == "folder" else item_id,
                "size": int(item.get("size", 0) or 0),
                "parent_id": cid or "0",
                "share_id": share_code,
            })
        folder_count = sum(1 for e in entries if e.get("is_dir"))
        file_count = sum(1 for e in entries if not e.get("is_dir"))
        return {
            "entries": entries,
            "total": len(entries),
            "summary": {
                "folder_count": folder_count,
                "file_count": file_count,
            },
            "share_title": share_payload,
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
                "dirId": int(target_cid or 0),
                "fileIdList": [int(fid) for fid in file_ids],
            },
            timeout=60,
        )
        return {"success": True, "count": len(file_ids)}

    def submit_offline_task(self, cookie, resource_url, folder_id="0"):
        data = self._api_call(
            cookie, "POST",
            "https://www.123pan.com/a/api/offline/download",
            json={"url": resource_url, "dirId": int(folder_id or 0)},
            timeout=30,
        )
        return {"task_id": str(data.get("data", {}).get("taskId", ""))}

    def probe_connectivity(self, cookie):
        try:
            self._api_call(cookie, "GET", "https://www.123pan.com/a/api/user/info")
            return True
        except Exception as e:
            logging.warning(f"123云盘连接检测失败: {e}")
            return False

    def rename_entry(self, cookie, entry_id, new_name, parent_id=""):
        data = self._api_call(
            cookie, "POST",
            "https://www.123pan.com/a/api/file/rename",
            json={"fileId": int(entry_id), "fileName": new_name.strip()},
        )
        return {"ok": True, "id": entry_id, "name": new_name}

    def move_entries(self, cookie, entry_ids, target_id, source_id=""):
        data = self._api_call(
            cookie, "POST",
            "https://www.123pan.com/a/api/file/mod_pid",
            json={
                "fileIdList": [int(eid) for eid in entry_ids],
                "toDirId": int(target_id) if target_id != "0" else 0,
            },
        )
        return {"ok": True, "ids": entry_ids, "target_cid": target_id}

    def copy_entries(self, cookie, entry_ids, target_id, source_id=""):
        data = self._api_call(
            cookie, "POST",
            "https://www.123pan.com/a/api/file/copy",
            json={
                "fileIdList": [int(eid) for eid in entry_ids],
                "toDirId": int(target_id) if target_id != "0" else 0,
            },
        )
        return {"ok": True, "ids": entry_ids, "target_cid": target_id}

    def delete_entries(self, cookie, entry_ids, parent_id=""):
        data = self._api_call(
            cookie, "POST",
            "https://www.123pan.com/a/api/file/trash",
            json={"fileIdList": [int(eid) for eid in entry_ids]},
        )
        return {"ok": True, "ids": entry_ids}

register(Pan123Provider())
