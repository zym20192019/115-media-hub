import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List

class CloudProvider(ABC):
    # === 元数据（子类覆盖） ===
    name: str = ""
    label: str = ""
    link_type: str = ""

    # === 认证 ===
    auth_type: str = "cookie"
    config_keys: List[str] = []

    # === 能力声明 ===
    supports_folder_browse: bool = True
    supports_share_receive: bool = True
    supports_subscription: bool = False
    supports_offline: bool = False
    supports_fixed_share_link: bool = False
    supports_strm: bool = False
    supports_monitor: bool = False
    # NEW — file operations
    supports_rename: bool = False
    supports_move: bool = False
    supports_copy: bool = False
    supports_delete: bool = False

    # === 限流 ===
    rate_limit_seconds: float = 0.0
    _rate_limit_lock: threading.Lock = threading.Lock()
    _last_request_monotonic: float = 0.0

    # === 核心 API ===
    @abstractmethod
    def list_entries_payload(self, cookie: str, cid: str = "0", folders_only: bool = False) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def list_entries(self, cookie: str, cid: str = "0") -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def create_folder(self, cookie: str, cid: str = "0", folder_name: str = "") -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def resolve_folder_id_by_path(self, cookie: str, relative_path: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def ensure_folder_id_by_path(self, cookie: str, relative_path: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def resolve_share_payload(self, cookie: str, share_url: str, raw_text: str = "", receive_code: str = "") -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def list_share_entries(self, cookie: str, share_payload: Dict[str, Any], cid: str = "0", offset: int = 0, limit: int = 200) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def prepare_share_receive(self, cookie: str, share_payload: Dict[str, Any], cid: str = "0") -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def submit_share_receive(self, cookie: str, receive_payload: Dict[str, Any], files: List[Dict[str, Any]]) -> Dict[str, Any]:
        raise NotImplementedError

    def submit_offline_task(self, cookie: str, resource_url: str, folder_id: str = "0") -> Dict[str, Any]:
        raise NotImplementedError(f"{self.label} 不支持离线下载")

    @abstractmethod
    def probe_connectivity(self, cookie: str) -> bool:
        raise NotImplementedError

    def resolve_download_url(self, cookie: str, file_id: str) -> str:
        raise NotImplementedError(f"{self.label} 不支持 STRM 直链")

    # === 文件操作（可选，子类按能力覆写） ===
    def rename_entry(self, cookie: str, entry_id: str, new_name: str, parent_id: str = "") -> Dict[str, Any]:
        raise NotImplementedError(f"{self.label} 不支持重命名")

    def move_entries(self, cookie: str, entry_ids: List[str], target_id: str, source_id: str = "") -> Dict[str, Any]:
        raise NotImplementedError(f"{self.label} 不支持移动")

    def copy_entries(self, cookie: str, entry_ids: List[str], target_id: str, source_id: str = "") -> Dict[str, Any]:
        raise NotImplementedError(f"{self.label} 不支持复制")

    def delete_entries(self, cookie: str, entry_ids: List[str], parent_id: str = "") -> Dict[str, Any]:
        raise NotImplementedError(f"{self.label} 不支持删除")

    # === 限流工具 ===
    def throttle(self) -> None:
        if self.rate_limit_seconds <= 0:
            return
        with self._rate_limit_lock:
            elapsed = time.monotonic() - self._last_request_monotonic
            if elapsed < self.rate_limit_seconds:
                time.sleep(self.rate_limit_seconds - elapsed)
            self._last_request_monotonic = time.monotonic()

    def get_cookie(self, cfg: Dict[str, Any]) -> str:
        """从配置字典中获取认证凭据"""
        if not self.name:
            return ""
        candidates = self.config_keys if self.config_keys else [f"cookie_{self.name}"]
        for key in candidates:
            value = str(cfg.get(key, "")).strip()
            if value:
                return value
        return ""

    def is_configured(self, cfg: Dict[str, Any]) -> bool:
        """判断认证配置是否填写完整，不触发远端登录或 token 刷新。"""
        if not self.name:
            return False
        source = cfg if isinstance(cfg, dict) else {}
        candidates = self.config_keys if self.config_keys else [f"cookie_{self.name}"]
        if not candidates:
            return False
        if self.auth_type == "password":
            return all(str(source.get(key, "") or "").strip() for key in candidates)
        return any(str(source.get(key, "") or "").strip() for key in candidates)

    def auth_label(self) -> str:
        if self.auth_type == "cookie":
            return "Cookie"
        if self.auth_type == "password_cookie":
            return "账号密码 / Cookie"
        if self.auth_type == "refresh_token":
            return "refresh_token"
        return "认证信息"
