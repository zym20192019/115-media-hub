import logging
import re

import requests

from .base import CloudProvider
from .registry import register


class Pan123Provider(CloudProvider):
    name = "123pan"
    label = "123云盘"
    link_type = "123pan"
    auth_type = "cookie"
    config_keys = ["cookie_123pan"]
    supports_subscription = True
    supports_offline = True
    supports_fixed_share_link = True

    def _headers(self, cookie: str) -> dict:
        return {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.123pan.com/",
            "Origin": "https://www.123pan.com",
        }

    def _api_call(self, cookie: str, method: str, url: str, **kwargs) -> dict:
        self.throttle()
        headers = self._headers(cookie)
        timeout = kwargs.pop("timeout", 30)
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=timeout, **kwargs)
        else:
            resp = requests.post(url, headers=headers, timeout=timeout, **kwargs)
        resp.raise_for_status()
        data = resp.json()
        code = int(data.get("code", -1) or -1)
        if code != 0 and code != 200:
            raise RuntimeError(f"123云盘 API 错误: {data.get('message', '未知错误')}")
        return data

    def list_entries_payload(self, cookie, cid="0", folders_only=False):
        data = self._api_call(
            cookie, "GET",
            f"https://www.123pan.com/api/file/list/new?dirID={cid or '0'}&page=1&size=200",
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
        return {"entries": entries, "total": len(entries)}

    def list_entries(self, cookie, cid="0"):
        return self.list_entries_payload(cookie, cid)["entries"]

    def create_folder(self, cookie, cid="0", folder_name=""):
        data = self._api_call(
            cookie, "POST",
            "https://www.123pan.com/api/file/upload/mkdir",
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
        url = f"https://www.123pan.com/api/share/info?shareKey={share_code}"
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
        return {"entries": entries, "total": len(entries), "share": share_payload}

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
            "https://www.123pan.com/api/share/save",
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
            "https://www.123pan.com/api/offline/download",
            json={"url": resource_url, "dirId": int(folder_id or 0)},
            timeout=30,
        )
        return {"task_id": str(data.get("data", {}).get("taskId", ""))}

    def probe_connectivity(self, cookie):
        try:
            self._api_call(cookie, "GET", "https://www.123pan.com/api/user/info")
            return True
        except Exception as e:
            logging.warning(f"123云盘连接检测失败: {e}")
            return False


register(Pan123Provider())
