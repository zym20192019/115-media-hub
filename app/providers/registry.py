import threading
from typing import Any, Dict, List, Optional

from .base import CloudProvider

_providers: Dict[str, CloudProvider] = {}
_lock = threading.Lock()


def register(provider: CloudProvider) -> None:
    with _lock:
        _providers[provider.name] = provider


def get(name: str) -> CloudProvider:
    with _lock:
        p = _providers.get(name)
    if p is None:
        raise RuntimeError(f"未知的网盘提供者: {name}")
    return p


def get_or_none(name: str) -> Optional[CloudProvider]:
    with _lock:
        return _providers.get(name)


def get_by_link_type(link_type: str) -> Optional[CloudProvider]:
    normalized = str(link_type or "").strip().lower()
    if not normalized:
        return None
    with _lock:
        for p in _providers.values():
            if p.link_type == normalized:
                return p
    return None


def list_all() -> List[CloudProvider]:
    with _lock:
        return list(_providers.values())


def list_enabled(cfg: Optional[Dict[str, Any]] = None) -> List[CloudProvider]:
    enabled_map = {}
    if isinstance(cfg, dict):
        enabled_map = cfg.get("provider_enabled", {})
        if not isinstance(enabled_map, dict):
            enabled_map = {}
    with _lock:
        result = []
        for p in _providers.values():
            is_enabled = enabled_map.get(p.name, p.name in ("115", "quark"))
            if is_enabled:
                result.append(p)
        return result


def get_all_capabilities(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    enabled_map = {}
    if isinstance(cfg, dict):
        enabled_map = cfg.get("provider_enabled", {})
        if not isinstance(enabled_map, dict):
            enabled_map = {}
    with _lock:
        result = []
        for p in _providers.values():
            is_enabled = enabled_map.get(p.name, p.name in ("115", "quark"))
            result.append({
                "name": p.name,
                "label": p.label,
                "link_type": p.link_type,
                "auth_type": p.auth_type,
                "config_keys": list(p.config_keys),
                "enabled": bool(is_enabled),
                "supports_folder_browse": p.supports_folder_browse,
                "supports_share_receive": p.supports_share_receive,
                "supports_subscription": p.supports_subscription,
                "supports_offline": p.supports_offline,
                "supports_fixed_share_link": p.supports_fixed_share_link,
                "supports_strm": p.supports_strm,
                "supports_monitor": p.supports_monitor,
            })
        return result
