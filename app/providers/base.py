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
