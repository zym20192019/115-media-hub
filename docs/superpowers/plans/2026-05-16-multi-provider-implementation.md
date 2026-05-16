# 多网盘扩展实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增天翼云盘、123云盘、阿里云盘支持，建立 Provider 抽象层，前端数据驱动渲染。

**Architecture:** 定义 `CloudProvider` 抽象基类 + 注册表模式；115/Quark 重构为类并向后兼容；core.py 中硬编码 provider 逻辑改为能力驱动；前端从 `/api/providers` 动态加载 provider 清单渲染 UI。

**Tech Stack:** Python 3 (FastAPI), vanilla JS (SPA), SQLite, Go templates

**Branch:** `feat/multi-provider`

---

## Phase 1: 抽象层 + 改造 115/Quark

### Task 1: 创建 CloudProvider 抽象基类

**Files:**
- Create: `app/providers/base.py`

- [ ] **Step 1: 创建 base.py**

```python
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

class CloudProvider:
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
    supports_offline: bool = False
    supports_fixed_share_link: bool = False
    supports_strm: bool = False
    supports_monitor: bool = False

    # === 限流 ===
    rate_limit_seconds: float = 0.0
    _rate_limit_lock: threading.Lock = None
    _last_request_monotonic: float = 0.0

    def __init__(self):
        if self._rate_limit_lock is None:
            object.__setattr__(self, '_rate_limit_lock', threading.Lock())

    # === 核心 API ===
    def list_entries_payload(self, cookie: str, cid: str = "0", folders_only: bool = False) -> Dict[str, Any]:
        raise NotImplementedError

    def list_entries(self, cookie: str, cid: str = "0") -> List[Dict[str, Any]]:
        raise NotImplementedError

    def create_folder(self, cookie: str, cid: str = "0", folder_name: str = "") -> Dict[str, Any]:
        raise NotImplementedError

    def resolve_folder_id_by_path(self, cookie: str, relative_path: str) -> str:
        raise NotImplementedError

    def ensure_folder_id_by_path(self, cookie: str, relative_path: str) -> str:
        raise NotImplementedError

    def resolve_share_payload(self, cookie: str, share_url: str, raw_text: str = "", receive_code: str = "") -> Dict[str, Any]:
        raise NotImplementedError

    def list_share_entries(self, cookie: str, share_payload: Dict[str, Any], cid: str = "0", offset: int = 0, limit: int = 200) -> Dict[str, Any]:
        raise NotImplementedError

    def prepare_share_receive(self, cookie: str, share_payload: Dict[str, Any], cid: str = "0") -> Dict[str, Any]:
        raise NotImplementedError

    def submit_share_receive(self, cookie: str, receive_payload: Dict[str, Any], files: List[Dict[str, Any]]) -> Dict[str, Any]:
        raise NotImplementedError

    def submit_offline_task(self, cookie: str, resource_url: str, folder_id: str = "0") -> Dict[str, Any]:
        raise NotImplementedError

    def probe_connectivity(self, cookie: str) -> bool:
        raise NotImplementedError

    def resolve_download_url(self, cookie: str, file_id: str) -> str:
        raise NotImplementedError

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
        candidates = self.config_keys if self.config_keys else [f"cookie_{self.name}"]
        for key in candidates:
            value = str(cfg.get(key, "")).strip()
            if value:
                return value
        return ""
```

- [ ] **Step 2: 验证语法**

```bash
.venv/bin/python -m compileall app/providers/base.py
```

- [ ] **Step 3: Commit**

```bash
git add app/providers/base.py
git commit -m "feat: add CloudProvider abstract base class"
```

---

### Task 2: 创建注册表

**Files:**
- Create: `app/providers/registry.py`

- [ ] **Step 1: 创建 registry.py**

```python
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
                "supports_offline": p.supports_offline,
                "supports_fixed_share_link": p.supports_fixed_share_link,
                "supports_strm": p.supports_strm,
                "supports_monitor": p.supports_monitor,
            })
        return result
```

- [ ] **Step 2: 验证语法**

```bash
.venv/bin/python -m compileall app/providers/registry.py
```

- [ ] **Step 3: Commit**

```bash
git add app/providers/registry.py
git commit -m "feat: add CloudProvider registry"
```

---

### Task 3: 重构 Pan115Provider

**Files:**
- Modify: `app/providers/pan115.py`

将现有的模块级函数组织为 `Pan115Provider` 类，同时保留原函数名作为模块级别名。

- [ ] **Step 1: 在 pan115.py 顶部添加 import 和类定义**

在 `pan115.py` 文件开头（现有 import 之后），找到 `from .common import parse_int` 后添加：

```python
from .base import CloudProvider
from .registry import register
```

- [ ] **Step 2: 在文件末尾（最后一行之后）追加 Provider 类**

```python
class Pan115Provider(CloudProvider):
    name = "115"
    label = "115网盘"
    link_type = "115share"
    auth_type = "cookie"
    config_keys = ["cookie_115"]
    supports_offline = True
    supports_fixed_share_link = True
    supports_strm = True
    supports_monitor = True
    rate_limit_seconds = _api_115_runtime_tuning.get("rate_limit_seconds", 0.35)

    def list_entries_payload(self, cookie, cid="0", folders_only=False):
        return list_115_entries_payload(cookie, cid, folders_only)

    def list_entries(self, cookie, cid="0"):
        return list_115_entries(cookie, cid)

    def create_folder(self, cookie, cid="0", folder_name=""):
        return create_115_folder(cookie, cid, folder_name)

    def resolve_folder_id_by_path(self, cookie, relative_path):
        return resolve_115_folder_id_by_path(cookie, relative_path)

    def ensure_folder_id_by_path(self, cookie, relative_path):
        return ensure_115_folder_id_by_path(cookie, relative_path)

    def resolve_share_payload(self, cookie, share_url, raw_text="", receive_code=""):
        return resolve_115_share_payload(cookie, share_url, raw_text, receive_code)

    def list_share_entries(self, cookie, share_payload, cid="0", offset=0, limit=200):
        return list_115_share_entries(cookie, share_payload, cid)

    def prepare_share_receive(self, cookie, share_payload, cid="0"):
        return prepare_115_share_receive(cookie, share_payload, cid)

    def submit_share_receive(self, cookie, receive_payload, files):
        return submit_115_share_receive(cookie, receive_payload, files)

    def submit_offline_task(self, cookie, resource_url, folder_id="0"):
        return submit_115_offline_task(cookie, resource_url, folder_id)

    def probe_connectivity(self, cookie):
        try:
            list_115_entries_payload(cookie, "0", folders_only=True)
            return True
        except Exception:
            return False

    def resolve_download_url(self, cookie, file_id):
        raise NotImplementedError  # STRM 后续实现，115 RSA 逻辑在 routes/strm.py


register(Pan115Provider())
```

- [ ] **Step 3: 验证语法**

```bash
.venv/bin/python -m compileall app/providers/pan115.py
```

- [ ] **Step 4: Commit**

```bash
git add app/providers/pan115.py
git commit -m "feat: wrap pan115 functions into Pan115Provider class"
```

---

### Task 4: 重构 QuarkProvider

**Files:**
- Modify: `app/providers/quark.py`

- [ ] **Step 1: 在 quark.py 顶部添加 import**

在 import block 末尾添加：

```python
from .base import CloudProvider
from .registry import register
```

- [ ] **Step 2: 在文件末尾追加 QuarkProvider 类**

```python
class QuarkProvider(CloudProvider):
    name = "quark"
    label = "夸克网盘"
    link_type = "quark"
    auth_type = "cookie"
    config_keys = ["cookie_quark"]
    supports_offline = False
    supports_fixed_share_link = False
    supports_strm = True
    supports_monitor = False

    def list_entries_payload(self, cookie, cid="0", folders_only=False):
        return list_quark_entries_payload(cookie, cid, folders_only)

    def list_entries(self, cookie, cid="0"):
        return list_quark_entries(cookie, cid)

    def create_folder(self, cookie, cid="0", folder_name=""):
        return create_quark_folder(cookie, cid, folder_name)

    def resolve_folder_id_by_path(self, cookie, relative_path):
        return resolve_quark_folder_id_by_path(cookie, relative_path)

    def ensure_folder_id_by_path(self, cookie, relative_path):
        return ensure_quark_folder_id_by_path(cookie, relative_path)

    def resolve_share_payload(self, cookie, share_url, raw_text="", receive_code=""):
        return resolve_quark_share_payload(cookie, share_url, raw_text, receive_code)

    def list_share_entries(self, cookie, share_payload, cid="0", offset=0, limit=200):
        return list_quark_share_entries(cookie, share_payload, cid, offset, limit)

    def prepare_share_receive(self, cookie, share_payload, cid="0"):
        return prepare_quark_share_save(cookie, share_payload, cid)

    def submit_share_receive(self, cookie, receive_payload, files):
        return submit_quark_share_save(cookie, receive_payload, files)

    def probe_connectivity(self, cookie):
        try:
            result = probe_quark_connectivity(cookie)
            return bool(result.get("ok"))
        except Exception:
            return False


register(QuarkProvider())
```

- [ ] **Step 3: 验证语法**

```bash
.venv/bin/python -m compileall app/providers/quark.py
```

- [ ] **Step 4: Commit**

```bash
git add app/providers/quark.py
git commit -m "feat: wrap quark functions into QuarkProvider class"
```

---

### Task 5: provider 模块延迟导入（避免循环依赖）

**Files:**
- Modify: `app/core.py`

`pan115.py` 和 `quark.py` 开头有 `from ..core import *`，core.py 底部又导入 provider。需要在 core.py 的 provider import 区域确保注册触发。

- [ ] **Step 1: 检查加载顺序**

core.py 底部 provider import 会触发 `pan115.py` / `quark.py` 加载，此时文件末尾的 `register(Pan115Provider())` 自动执行。确认无需额外改动——现有 import 即触发注册。

- [ ] **Step 2: 在 core.py 中导入 registry**

在 core.py 文件末尾、provider import 区域**之前**添加：

```python
from .providers.registry import get_all_capabilities, get_by_link_type, get_or_none as _get_provider_or_none, list_all, list_enabled
```

然后紧接后续的：

```python
from .providers.pan115 import (
    # ... 保持不变
)
from .providers.quark import (
    # ... 保持不变
)
```

- [ ] **Step 3: 验证完整加载链**

```bash
.venv/bin/python -c "from app.core import list_all; print([p.name for p in list_all()])"
```

Expected: `['115', 'quark']`

- [ ] **Step 4: Commit**

```bash
git add app/core.py
git commit -m "feat: import registry into core.py"
```

---

### Task 6: 添加 /api/providers 端点 + provider_enabled 配置

**Files:**
- Modify: `app/routes/settings.py`
- Modify: `app/core.py`

- [ ] **Step 1: 在 settings.py 中添加端点**

```python
# 在 settings.py 的 router 中添加
from ..core import get_all_capabilities, normalize_provider_enabled_config

@router.get("/api/providers")
async def get_providers(request: Request):
    cfg = get_settings_config()
    return JSONResponse(get_all_capabilities(cfg))
```

- [ ] **Step 2: 在 core.py 中添加 provider_enabled 默认值处理**

在 `app/core.py` 中找到默认配置初始化位置（`DEFAULT_SETTINGS` 或类似结构），添加：

```python
# 在 provider_enabled 默认值相关位置
def normalize_provider_enabled_config(cfg: Dict[str, Any]) -> Dict[str, bool]:
    enabled = cfg.get("provider_enabled")
    if not isinstance(enabled, dict):
        enabled = {}
    defaults = {"115": True, "quark": True}
    result = {}
    for name in defaults:
        result[name] = bool(enabled.get(name, defaults[name]))
    for name in enabled:
        if name not in result:
            result[name] = bool(enabled[name])
    return result
```

并在 `build_public_settings_payload` 中确保返回 `provider_enabled`。

- [ ] **Step 3: 验证端点**

启动 dev server 后访问 `http://localhost:18080/api/providers`，预期返回 JSON 包含 115 和 quark。

- [ ] **Step 4: Commit**

```bash
git add app/routes/settings.py app/core.py
git commit -m "feat: add /api/providers endpoint and provider_enabled config"
```

---

### Task 7: 替换 core.py 中的硬编码 provider 常量为动态获取

**Files:**
- Modify: `app/core.py`

将以下硬编码改为从 registry 动态获取：

- [ ] **Step 1: 改造 COOKIE_HEALTH_PROVIDERS**

找到 `COOKIE_HEALTH_PROVIDERS: Tuple[str, ...] = ("115", "quark")`，改为：

```python
def _build_cookie_health_providers() -> Tuple[str, ...]:
    try:
        return tuple(p.name for p in list_all() if p.config_keys)
    except Exception:
        return ("115", "quark")
```

- [ ] **Step 2: 改造 DEFAULT_MOUNT_POINTS**

找到 `DEFAULT_MOUNT_POINTS: List[Dict[str, str]] = [...]`，改为：

```python
def _build_default_mount_points() -> List[Dict[str, str]]:
    try:
        return [{"provider": p.name, "prefix": f"/{p.name}"} for p in list_all()]
    except Exception:
        return [{"provider": "115", "prefix": "/115"}, {"provider": "quark", "prefix": "/quark"}]
```

- [ ] **Step 3: 改造 normalize_subscription_provider**

找到 `normalize_subscription_provider` 函数，改为：

```python
def normalize_subscription_provider(value: Any, fallback: str = "115") -> str:
    normalized = str(value or "").strip().lower()
    # 先精确匹配
    p = _get_provider_or_none(normalized)
    if p:
        return p.name
    # 通过 link_type 匹配
    p = get_by_link_type(normalized)
    if p:
        return p.name
    # 兼容旧别名
    if normalized in ("115share", "magnet", "magnet115"):
        return "115"
    if normalized in ("pan.quark", "quarkshare", "quark_pan"):
        return "quark"
    normalized_fallback = str(fallback or "115").strip().lower()
    return normalized_fallback if _get_provider_or_none(normalized_fallback) else "115"
```

- [ ] **Step 4: 改造 normalize_resource_provider_filter**

```python
def normalize_resource_provider_filter(value: Any) -> str:
    normalized = str(value or "all").strip().lower()
    if normalized == "all":
        return "all"
    if normalized == "magnet":
        return "magnet"
    # 检查是否匹配已注册的 provider name
    p = _get_provider_or_none(normalized)
    if p:
        return p.name
    # 通过 link_type 匹配
    p = get_by_link_type(normalized)
    if p:
        return p.name
    return "all"
```

- [ ] **Step 5: 验证改动**

```bash
.venv/bin/python -c "
from app.core import normalize_subscription_provider, normalize_resource_provider_filter, list_all
print('providers:', [p.name for p in list_all()])
print('norm sub 115:', normalize_subscription_provider('115'))
print('norm sub quark:', normalize_subscription_provider('quark'))
print('norm filter 115:', normalize_resource_provider_filter('115'))
print('norm filter quark:', normalize_resource_provider_filter('quark'))
"
```

- [ ] **Step 6: Commit**

```bash
git add app/core.py
git commit -m "feat: replace hardcoded provider lists with registry lookups"
```

---

### Task 8: 订阅任务 normalize 改为能力驱动

**Files:**
- Modify: `app/core.py` (normalize_subscription 函数)

- [ ] **Step 1: 替换硬编码 provider 守卫**

在 `normalize_subscription` 函数中（约 1600-1650 行），找到所有 `if provider != "115"` 和 `if provider == "115"`：

```python
# 旧:
use_115_fixed_link = provider == "115" and share_link_type == "115share"
if provider != "115":
    share_link_url = ""
# ...
if provider != "115":
    share_link_receive_code = ""
    share_subdir = ""
    share_subdir_cid = ""
    fixed_link_channel_search = False

# 新:
p = _get_provider_or_none(provider)
supports_fixed = p.supports_fixed_share_link if p else False
use_fixed_link = supports_fixed and provider == "115" and share_link_type == "115share"
if not supports_fixed:
    share_link_url = ""
    share_link_receive_code = ""
    share_subdir = ""
    share_subdir_cid = ""
    fixed_link_channel_search = False
```

- [ ] **Step 2: 验证**

```bash
.venv/bin/python -m compileall app/core.py
```

- [ ] **Step 3: Commit**

```bash
git add app/core.py
git commit -m "feat: use capability-driven checks in normalize_subscription"
```

---

## Phase 2: 前端数据驱动改造

### Task 9: boot.js 启动时加载 providerMeta

**Files:**
- Modify: `static/js/modules/app/boot.js`
- Modify: `static/js/modules/resource/core.js`

- [ ] **Step 1: 在 boot.js init() 中添加 provider 加载**

在 `init()` 函数中，`const cfg = await window.MediaHubApi.getJson('/get_settings');` 之后添加：

```javascript
// 加载 provider 清单
try {
    const providerList = await window.MediaHubApi.getJson('/api/providers');
    window.providerMeta = providerList || [];
    if (typeof setProviderMeta === 'function') {
        setProviderMeta(window.providerMeta);
    }
} catch (e) {
    console.warn('加载 provider 清单失败，使用默认值', e);
    window.providerMeta = [];
}
```

- [ ] **Step 2: 在 core.js 中添加 setProviderMeta**

```javascript
// core.js 顶部
let _providerMeta = [];

export function setProviderMeta(list) {
    _providerMeta = list || [];
}

export function getProviderMeta() {
    if (_providerMeta.length === 0 && window.providerMeta) {
        _providerMeta = window.providerMeta;
    }
    return _providerMeta;
}

export function getEnabledProviders() {
    return getProviderMeta().filter(p => p.enabled);
}

export function getProviderByName(name) {
    return getProviderMeta().find(p => p.name === name);
}

export function getProviderByLinkType(linkType) {
    return getProviderMeta().find(p => p.link_type === linkType);
}
```

- [ ] **Step 3: 替换 getResourceProviderByLinkType**

```javascript
// 旧:
export function getResourceProviderByLinkType(linkType) {
    if (linkType === 'quark') return 'quark';
    return '115';
}

// 新:
export function getResourceProviderByLinkType(linkType) {
    const p = getProviderByLinkType(linkType);
    return p ? p.name : '115';
}
```

- [ ] **Step 4: 替换 getResourceProviderLabel**

```javascript
// 旧:
export function getResourceProviderLabel(provider) {
    if (normalizeSubscriptionProvider(provider, '115') === 'quark') return '夸克';
    return '115';
}

// 新:
export function getResourceProviderLabel(provider) {
    const p = getProviderByName(normalizeSubscriptionProvider(provider, '115'));
    return p ? p.label : '115';
}
```

- [ ] **Step 5: 替换 getResourceLinkTypeLabel**

使用 `getProviderByLinkType` 增加动态映射，保留现有静态表作为 fallback：

```javascript
export function getResourceLinkTypeLabel(linkType) {
    const staticLabels = {
        '115share': '115分享', 'quark': '夸克分享', 'magnet': '磁力',
        'ed2k': '电驴', 'url': '直链', 'aliyun': '阿里云盘', 'baidu': '百度网盘',
        'xunlei': '迅雷云盘', 'uc': 'UC网盘', '123pan': '123云盘',
        'tianyi': '天翼云盘', 'pikpak': 'PikPak', 'lanzou': '蓝奏云',
        'google_drive': 'Google Drive', 'onedrive': 'OneDrive', 'mega': 'MEGA',
        'unknown': '未知',
    };
    const p = getProviderByLinkType(linkType);
    if (p) return p.label;
    return staticLabels[linkType] || linkType || '未知';
}
```

- [ ] **Step 6: Commit**

```bash
git add static/js/modules/app/boot.js static/js/modules/resource/core.js
git commit -m "feat: load providerMeta on boot, add dynamic provider helpers"
```

---

### Task 10: 前端 provider filter 动态渲染

**Files:**
- Modify: `templates/partials/pages/resource.html`
- Modify: `static/js/modules/resource/core.js`

- [ ] **Step 1: 替换资源页 provider 过滤按钮**

将 `templates/partials/pages/resource.html` 中的硬编码按钮（约20-23行）：

```html
<button id="resource-provider-filter-all" onclick="setResourceProviderFilter('all')">全部</button>
<button id="resource-provider-filter-115" onclick="setResourceProviderFilter('115')">115</button>
<button id="resource-provider-filter-magnet" onclick="setResourceProviderFilter('magnet')">磁力</button>
<button id="resource-provider-filter-quark" onclick="setResourceProviderFilter('quark')">夸克</button>
```

替换为：

```html
<div id="resource-provider-filters">
  <button id="resource-provider-filter-all" onclick="setResourceProviderFilter('all')">全部</button>
  <button id="resource-provider-filter-magnet" onclick="setResourceProviderFilter('magnet')">磁力</button>
</div>
```

- [ ] **Step 2: 在 core.js 中添加动态渲染逻辑**

```javascript
export function renderProviderFilterButtons() {
    const container = document.getElementById('resource-provider-filters');
    if (!container) return;
    // 移除旧的动态按钮（保留 "全部" 和 "磁力"）
    container.querySelectorAll('.provider-filter-dynamic').forEach(el => el.remove());
    const enabled = getEnabledProviders();
    enabled.forEach(p => {
        const btn = document.createElement('button');
        btn.id = `resource-provider-filter-${p.name}`;
        btn.className = 'provider-filter-dynamic';
        btn.onclick = () => setResourceProviderFilter(p.name);
        btn.textContent = p.label;
        container.appendChild(btn);
    });
}
```

在 boot.js 的 init() 末尾添加调用：

```javascript
if (typeof renderProviderFilterButtons === 'function') {
    renderProviderFilterButtons();
}
```

- [ ] **Step 3: 替换 resourceItemMatchesProviderFilter**

```javascript
// 旧:
export function resourceItemMatchesProviderFilter(item, filter) {
    if (filter === 'all') return true;
    const linkType = detectResourceLinkTypeByUrl(item.link_url);
    if (filter === '115') return linkType === '115share';
    if (filter === 'quark') return linkType === 'quark';
    if (filter === 'magnet') return linkType === 'magnet';
    return false;
}

// 新:
export function resourceItemMatchesProviderFilter(item, filter) {
    if (filter === 'all') return true;
    if (filter === 'magnet') return detectResourceLinkTypeByUrl(item.link_url) === 'magnet';
    const p = getProviderByName(filter);
    if (p) return detectResourceLinkTypeByUrl(item.link_url) === p.link_type;
    return false;
}
```

- [ ] **Step 4: Commit**

```bash
git add templates/partials/pages/resource.html static/js/modules/resource/core.js static/js/modules/app/boot.js
git commit -m "feat: dynamic provider filter buttons and matching logic"
```

---

### Task 11: 设置页动态 provider 配置块

**Files:**
- Modify: `templates/partials/pages/settings.html`
- Modify: `static/js/modules/tabs/settings.js`

- [ ] **Step 1: 替换设置页网盘认证区域**

将 `templates/partials/pages/settings.html` 中「网盘认证」section 内的硬编码 cookie 输入框和健康卡片替换为动态容器：

```html
<!-- 网盘认证 -->
<section style="order: 1;">
    <h3 class="text-sm font-semibold text-slate-400 mb-3 tracking-wide">网盘认证</h3>
    <div id="settings-provider-auth-container">
        <!-- 由 JS 动态渲染每个 provider 的折叠块 -->
    </div>
</section>
```

- [ ] **Step 2: 在 settings.js 中添加动态渲染函数**

```javascript
function renderProviderAuthBlocks(cfg, sensitiveMeta) {
    const container = document.getElementById('settings-provider-auth-container');
    if (!container) return;
    const meta = getProviderMeta();
    if (!meta.length) return;

    container.innerHTML = meta.map(p => {
        const enabled = p.enabled;
        const cookieKey = p.config_keys[0] || `cookie_${p.name}`;
        const cookieVal = cfg[cookieKey] || '';
        const hasCookie = cookieVal.length > 0;
        const healthy = sensitiveMeta && sensitiveMeta['cookie_health'] ?
            sensitiveMeta['cookie_health'][p.name] : null;
        const healthIcon = healthy === true ? '🟢' : healthy === false ? '🔴' : '⚪';
        const authHint = p.auth_type === 'refresh_token'
            ? `<a href="https://aliyuntoken.vercel.app/" target="_blank" class="text-xs text-blue-400">获取 refresh_token</a>`
            : p.auth_type === 'oauth2'
            ? `<span class="text-xs text-slate-500">Cookie + OAuth2 自动续期</span>`
            : `<span class="text-xs text-slate-500">从浏览器复制 Cookie</span>`;

        const tags = [];
        if (p.supports_share_receive) tags.push('分享转存');
        if (p.supports_offline) tags.push('离线下载');
        if (p.supports_strm) tags.push('STRM');

        return `
        <div class="provider-auth-block mb-3 bg-slate-800/50 rounded-xl border border-slate-700/50 overflow-hidden">
            <div class="flex items-center justify-between p-3 cursor-pointer" onclick="toggleProviderBlock('${p.name}')">
                <div class="flex items-center gap-3">
                    <span class="text-sm text-slate-200">${p.label}</span>
                    <span class="text-xs text-slate-500">${healthIcon}</span>
                    <span class="text-xs text-slate-500">${tags.join(' · ')}</span>
                </div>
                <label class="relative inline-flex items-center cursor-pointer" onclick="event.stopPropagation()">
                    <input type="checkbox" id="provider_enabled_${p.name}" ${enabled ? 'checked' : ''}
                        onchange="toggleProviderEnabled('${p.name}', this.checked)"
                        class="sr-only peer">
                    <div class="w-9 h-5 bg-slate-600 rounded-full peer peer-checked:bg-emerald-500/70 peer-focus:ring-2 peer-focus:ring-emerald-400/30 after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full"></div>
                </label>
            </div>
            <div id="provider-block-body-${p.name}" class="p-3 pt-0 border-t border-slate-700/50 ${enabled ? '' : 'hidden'}">
                ${authHint}
                <textarea id="${cookieKey}" class="w-full bg-slate-900 border-slate-700 rounded-xl p-3 text-sm mt-2 font-mono" rows="3" placeholder="粘贴 ${p.label} 的${p.auth_type === 'refresh_token' ? 'refresh_token' : 'Cookie'}">${cookieVal}</textarea>
                <div class="mt-2 flex items-center gap-2">
                    <button type="button" onclick="testProviderCookie('${p.name}')" class="text-xs text-slate-400 hover:text-slate-200 bg-slate-700 hover:bg-slate-600 px-3 py-1 rounded-lg">测试连接</button>
                    <span id="provider-health-${p.name}" class="text-xs text-slate-500"></span>
                </div>
            </div>
        </div>`;
    }).join('');
}

function toggleProviderBlock(name) {
    const body = document.getElementById(`provider-block-body-${name}`);
    if (body) body.classList.toggle('hidden');
}

function toggleProviderEnabled(name, checked) {
    const body = document.getElementById(`provider-block-body-${name}`);
    if (body) {
        if (checked) body.classList.remove('hidden');
        else body.classList.add('hidden');
    }
    // 标记已修改，保存时生效
    window._providerEnabledChanged = true;
}

async function testProviderCookie(name) {
    const p = getProviderByName(name);
    if (!p) return;
    const cookieKey = p.config_keys[0] || `cookie_${name}`;
    const el = document.getElementById(cookieKey);
    const cookie = el ? el.value.trim() : '';
    const statusEl = document.getElementById(`provider-health-${name}`);
    if (statusEl) statusEl.textContent = '检测中...';
    try {
        const resp = await window.MediaHubApi.postJson('/test_provider_cookie', { provider: name, cookie });
        if (statusEl) statusEl.textContent = resp.ok ? '✓ 连接成功' : '✗ ' + (resp.error || '连接失败');
        if (statusEl) statusEl.className = 'text-xs ' + (resp.ok ? 'text-emerald-400' : 'text-red-400');
    } catch (e) {
        if (statusEl) statusEl.textContent = '✗ 请求失败';
        if (statusEl) statusEl.className = 'text-xs text-red-400';
    }
}
```

- [ ] **Step 3: 修改 collectSettingsPayload**

在 `collectSettingsPayload()` 中，provider 字段动态收集：

```javascript
function collectSettingsPayload() {
    const payload = {};
    // ... 其他字段不变

    // 动态收集各 provider 的 cookie
    const meta = getProviderMeta();
    const providerEnabled = {};
    meta.forEach(p => {
        const cookieKey = p.config_keys[0] || `cookie_${p.name}`;
        const el = document.getElementById(cookieKey);
        if (el) payload[cookieKey] = el.value;
        const enabledEl = document.getElementById(`provider_enabled_${p.name}`);
        providerEnabled[p.name] = enabledEl ? enabledEl.checked : p.enabled;
    });
    payload.provider_enabled = providerEnabled;

    // ... 其余字段
    return payload;
}
```

- [ ] **Step 4: 启动时调用 renderProviderAuthBlocks**

在 settings 页面切换时或 boot.js init 完成后调用：

```javascript
// 在 boot.js init 中，加载 cfg 后
if (typeof renderProviderAuthBlocks === 'function') {
    renderProviderAuthBlocks(cfg, sensitiveMeta);
}
```

- [ ] **Step 5: Commit**

```bash
git add templates/partials/pages/settings.html static/js/modules/tabs/settings.js static/js/modules/app/boot.js
git commit -m "feat: dynamic provider auth blocks in settings"
```

---

### Task 12: 订阅弹窗 provider 下拉框动态化

**Files:**
- Modify: `templates/partials/modals/subscription.html`
- Modify: `static/js/modules/subscription/ui.js`

- [ ] **Step 1: 替换下拉框**

将 `templates/partials/modals/subscription.html` 中硬编码的 select option：

```html
<select id="subscription_provider" onchange="syncSubscriptionProviderUI()">
</select>
```

- [ ] **Step 2: 在 ui.js 中添加动态填充**

```javascript
function populateSubscriptionProviderSelect() {
    const select = document.getElementById('subscription_provider');
    if (!select) return;
    const enabled = getEnabledProviders();
    select.innerHTML = enabled.map(p =>
        `<option value="${p.name}">${p.label}</option>`
    ).join('');
}
```

在订阅弹窗打开时调用。

- [ ] **Step 3: 替换 provider guard 为能力驱动**

在 `folders.js` 中：

```javascript
// 旧:
if (getCurrentSubscriptionProvider() !== '115') {
    alert('Quark 模式不支持固定分享链接目录浏览');
    return;
}

// 新:
const p = getProviderByName(getCurrentSubscriptionProvider());
if (!p || !p.supports_fixed_share_link) {
    alert(p ? `${p.label} 不支持固定分享链接目录浏览` : '当前网盘不支持此功能');
    return;
}
```

- [ ] **Step 4: Commit**

```bash
git add templates/partials/modals/subscription.html static/js/modules/subscription/ui.js static/js/modules/subscription/folders.js
git commit -m "feat: dynamic subscription provider dropdown and capability-driven guards"
```

---

### Task 13: 导入弹窗 provider 逻辑能力驱动

**Files:**
- Modify: `static/js/modules/resource/import-modal.js`

- [ ] **Step 1: isProviderCookieConfigured 动态化**

```javascript
// 旧:
function isProviderCookieConfigured(provider) {
    if (provider === 'quark') return resourceState.quark_cookie_configured;
    return resourceState.cookie_configured;
}

// 新:
function isProviderCookieConfigured(provider) {
    const p = getProviderByName(provider);
    if (!p) return false;
    const key = 'cookie_configured_' + p.name;
    if (resourceState[key] !== undefined) return resourceState[key];
    // fallback: 检查主 cookie key
    const cookieKey = p.config_keys[0] || `cookie_${p.name}`;
    return resourceState[cookieKey + '_configured'] || false;
}
```

- [ ] **Step 2: getResourceFolderApiPrefix 统一路由**

```javascript
// 旧:
function getResourceFolderApiPrefix(provider) {
    return normalizeSubscriptionProvider(provider, '115') === 'quark' ? '/resource/quark' : '/resource/115';
}

// 新:
function getResourceFolderApiPrefix(provider) {
    return `/resource/browse?provider=${encodeURIComponent(provider)}`;
}
```

- [ ] **Step 3: Commit**

```bash
git add static/js/modules/resource/import-modal.js static/js/modules/resource/core.js
git commit -m "feat: capability-driven import modal and unified folder API prefix"
```

---

### Task 14: 统一资源浏览路由

**Files:**
- Modify: `app/routes/resource.py`

- [ ] **Step 1: 添加统一路由**

在 `resource.py` 中添加：

```python
@router.get("/resource/browse")
async def unified_browse(request: Request):
    provider_name = str(request.query_params.get("provider", "115")).strip()
    cid = str(request.query_params.get("cid", "0")).strip()
    p = get(provider_name)
    cookie = p.get_cookie(get_settings_config())
    payload = await run_resource_browse_io(p.list_entries_payload, cookie, cid)
    return JSONResponse(payload)

@router.post("/resource/browse/create-folder")
async def unified_create_folder(request: Request):
    body = await request.json()
    provider_name = str(body.get("provider", "")).strip()
    cid = str(body.get("cid", "0")).strip()
    name = str(body.get("name", "")).strip()
    p = get(provider_name)
    cookie = p.get_cookie(get_settings_config())
    folder = await run_resource_browse_io(p.create_folder, cookie, cid, name)
    return JSONResponse(folder)
```

旧路由 `/resource/115/...` 和 `/resource/quark/...` 保留，内部改为调用统一逻辑。

- [ ] **Step 2: Commit**

```bash
git add app/routes/resource.py
git commit -m "feat: add unified /resource/browse endpoint"
```

---

### Task 15: 刮削器 provider 列表动态化

**Files:**
- Modify: `static/js/modules/scraper/core.js`

- [ ] **Step 1: 替换硬编码列表**

找到硬编码的 `{ provider: '115', label: '115' }, { provider: 'quark', label: '夸克' }`，替换为：

```javascript
function getScraperProviderOptions() {
    return getEnabledProviders().map(p => ({
        provider: p.name,
        label: p.label,
    }));
}
```

- [ ] **Step 2: Commit**

```bash
git add static/js/modules/scraper/core.js
git commit -m "feat: dynamic scraper provider list"
```

---

## Phase 3: 新增 3 个网盘

### Task 16: 天翼云盘 Provider

**Files:**
- Create: `app/providers/tianyi.py`

- [ ] **Step 1: 实现 TianyiProvider**

核心 API 端点 `cloud.189.cn` / `api.cloud.189.cn`，认证为 OAuth2 SSO：

```python
import json
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
    supports_offline = False
    supports_fixed_share_link = False

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
                headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0"},
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
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        }

    def list_entries_payload(self, cookie, cid="0", folders_only=False):
        url = "https://api.cloud.189.cn/open/file/listFiles.action"
        params = {
            "folderId": cid or "0",
            "pageNum": 1,
            "pageSize": 200,
            "orderBy": "lastOpTime",
            "descending": "true",
        }
        if folders_only:
            params["mediaType"] = "folder"
        resp = requests.get(url, headers=self._api_headers(cookie), params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        entries = []
        for item in data.get("data", {}).get("items", []):
            entries.append({
                "id": str(item.get("fileId", "")),
                "name": str(item.get("fileName", "")),
                "type": "folder" if item.get("isFolder") else "file",
                "size": int(item.get("fileSize", 0) or 0),
                "parent_id": cid or "0",
            })
        return {"entries": entries, "total": data.get("data", {}).get("total", 0)}

    def list_entries(self, cookie, cid="0"):
        payload = self.list_entries_payload(cookie, cid)
        return payload["entries"]

    def create_folder(self, cookie, cid="0", folder_name=""):
        url = "https://api.cloud.189.cn/open/file/createFolder.action"
        resp = requests.post(url, headers=self._api_headers(cookie), data={
            "parentFileId": cid or "0",
            "fileName": folder_name,
        }, timeout=15)
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
        share_code = share_code_match.group(1)
        return {"share_code": share_code, "receive_code": str(receive_code or "").strip()}

    def list_share_entries(self, cookie, share_payload, cid="0", offset=0, limit=200):
        share_code = share_payload["share_code"]
        receive_code = share_payload.get("receive_code", "")
        url = "https://api.cloud.189.cn/open/share/listShareDir.action"
        resp = requests.post(url, headers=self._api_headers(cookie), data={
            "shareCode": share_code,
            "accessCode": receive_code,
            "fileId": cid or "0",
            "pageNum": 1,
            "pageSize": limit,
        }, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        entries = []
        for item in data.get("data", {}).get("items", []):
            entries.append({
                "id": str(item.get("fileId", "")),
                "name": str(item.get("fileName", "")),
                "type": "folder" if item.get("isFolder") else "file",
                "size": int(item.get("fileSize", 0) or 0),
                "share_id": share_code,
            })
        return {"entries": entries, "total": data.get("data", {}).get("total", 0), "share": share_payload}

    def prepare_share_receive(self, cookie, share_payload, cid="0"):
        return {**share_payload, "target_cid": cid or "0"}

    def submit_share_receive(self, cookie, receive_payload, files):
        share_code = receive_payload["share_code"]
        receive_code = receive_payload.get("receive_code", "")
        target_cid = receive_payload.get("target_cid", "0")
        file_ids = [str(f.get("id", "")).strip() for f in (files or []) if str(f.get("id", "")).strip()]
        if not file_ids:
            raise RuntimeError("未选择要转存的文件")

        url = "https://api.cloud.189.cn/open/share/saveShareFiles.action"
        resp = requests.post(url, headers=self._api_headers(cookie), data={
            "shareCode": share_code,
            "accessCode": receive_code,
            "targetFileId": target_cid,
            "fileIds": ",".join(file_ids),
        }, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if data.get("res_code") != 0:
            raise RuntimeError(f"天翼云盘转存失败: {data.get('res_msg', '')}")
        return {"success": True, "count": len(file_ids)}

    def probe_connectivity(self, cookie):
        try:
            self._ensure_token(cookie)
            return True
        except Exception:
            return False


register(TianyiProvider())
```

- [ ] **Step 2: 验证语法**

```bash
.venv/bin/python -m compileall app/providers/tianyi.py
```

- [ ] **Step 3: 验证注册**

```bash
.venv/bin/python -c "import app.providers.tianyi; from app.providers.registry import list_all; print([p.name for p in list_all()])"
```

Expected: `['115', 'quark', 'tianyi']`

- [ ] **Step 4: Commit**

```bash
git add app/providers/tianyi.py
git commit -m "feat: add TianyiProvider (天翼云盘)"
```

---

### Task 17: 123云盘 Provider

**Files:**
- Create: `app/providers/pan123.py`

- [ ] **Step 1: 实现 Pan123Provider**

```python
import json
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
    supports_offline = True

    def _headers(self, cookie: str) -> dict:
        return {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://www.123pan.com/",
        }

    def _api_call(self, cookie: str, method: str, url: str, **kwargs) -> dict:
        self.throttle()
        headers = self._headers(cookie)
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=kwargs.pop("timeout", 30), **kwargs)
        else:
            resp = requests.post(url, headers=headers, timeout=kwargs.pop("timeout", 30), **kwargs)
        resp.raise_for_status()
        data = resp.json()
        code = int(data.get("code", -1) or -1)
        if code != 0 and code != 200:
            raise RuntimeError(f"123云盘 API 错误: {data.get('message', '未知错误')}")
        return data

    def list_entries_payload(self, cookie, cid="0", folders_only=False):
        data = self._api_call(cookie, "GET",
            f"https://www.123pan.com/api/file/list/new?dirID={cid or '0'}&page=1&size=200")
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
                "size": int(item.get("size", 0) or 0),
                "parent_id": cid or "0",
            })
        return {"entries": entries, "total": len(entries)}

    def list_entries(self, cookie, cid="0"):
        return self.list_entries_payload(cookie, cid)["entries"]

    def create_folder(self, cookie, cid="0", folder_name=""):
        data = self._api_call(cookie, "POST",
            "https://www.123pan.com/api/file/upload/mkdir",
            json={"driveId": 0, "dirId": int(cid or 0), "name": folder_name})
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
        return {"share_code": share_code_match.group(1), "receive_code": str(receive_code or "").strip()}

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
            entries.append({
                "id": str(item.get("fileId", item.get("fileID", ""))),
                "name": str(item.get("fileName", "")),
                "type": "folder" if int(item.get("type", 0) or 0) == 1 else "file",
                "size": int(item.get("size", 0) or 0),
                "share_id": share_code,
            })
        return {"entries": entries, "total": len(entries), "share": share_payload}

    def prepare_share_receive(self, cookie, share_payload, cid="0"):
        return {**share_payload, "target_cid": cid or "0"}

    def submit_share_receive(self, cookie, receive_payload, files):
        share_code = receive_payload["share_code"]
        receive_code = receive_payload.get("receive_code", "")
        target_cid = receive_payload.get("target_cid", "0")
        file_ids = [str(f.get("id", "")).strip() for f in (files or []) if str(f.get("id", "")).strip()]
        if not file_ids:
            raise RuntimeError("未选择要转存的文件")

        url = "https://www.123pan.com/api/share/save"
        data = self._api_call(cookie, "POST", url, json={
            "shareKey": share_code,
            "sharePwd": receive_code,
            "dirId": int(target_cid or 0),
            "fileIdList": [int(fid) for fid in file_ids],
        })
        return {"success": True, "count": len(file_ids)}

    def submit_offline_task(self, cookie, resource_url, folder_id="0"):
        url = "https://www.123pan.com/api/offline/download"
        data = self._api_call(cookie, "POST", url, json={
            "url": resource_url,
            "dirId": int(folder_id or 0),
        })
        return {"task_id": str(data.get("data", {}).get("taskId", ""))}

    def probe_connectivity(self, cookie):
        try:
            self._api_call(cookie, "GET", "https://www.123pan.com/api/user/info")
            return True
        except Exception:
            return False


register(Pan123Provider())
```

- [ ] **Step 2: 验证语法**

```bash
.venv/bin/python -m compileall app/providers/pan123.py
```

- [ ] **Step 3: Commit**

```bash
git add app/providers/pan123.py
git commit -m "feat: add Pan123Provider (123云盘)"
```

---

### Task 18: 阿里云盘 Provider

**Files:**
- Create: `app/providers/aliyun.py`

- [ ] **Step 1: 实现 AliyunProvider**

```python
import json
import logging
import re
import threading
import time

import requests

from .base import CloudProvider
from .registry import register


class AliyunProvider(CloudProvider):
    name = "aliyun"
    label = "阿里云盘"
    link_type = "aliyun"
    auth_type = "refresh_token"
    config_keys = ["aliyun_refresh_token"]
    supports_offline = False
    supports_fixed_share_link = False

    def __init__(self):
        super().__init__()
        self._access_token = None
        self._token_expiry = 0.0
        self._token_lock = threading.Lock()

    def _ensure_access_token(self, refresh_token: str) -> str:
        now = time.time()
        with self._token_lock:
            if self._access_token and now < self._token_expiry - 60:
                return self._access_token
            resp = requests.post(
                "https://auth.aliyundrive.com/v2/account/token",
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("access_token")
            if not token:
                raise RuntimeError("阿里云盘 access_token 获取失败，请检查 refresh_token")
            self._access_token = token
            self._token_expiry = now + int(data.get("expires_in", 7200))
            return token

    def _api_headers(self, refresh_token: str) -> dict:
        token = self._ensure_access_token(refresh_token)
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        }

    def _api_post(self, refresh_token: str, url: str, body: dict, timeout: int = 30) -> dict:
        self.throttle()
        resp = requests.post(url, headers=self._api_headers(refresh_token), json=body, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") and data["code"] not in ("Success", ""):
            raise RuntimeError(f"阿里云盘 API 错误: {data.get('message', '未知错误')}")
        return data

    def list_entries_payload(self, cookie, cid="root", folders_only=False):
        body = {
            "drive_id": self._resolve_drive_id(refresh_token=cookie),
            "parent_file_id": cid or "root",
            "limit": 200,
            "order_by": "updated_at",
            "order_direction": "DESC",
        }
        if folders_only:
            body["type"] = "folder"
        data = self._api_post(cookie, "https://api.aliyundrive.com/v2/file/list", body)
        entries = []
        for item in data.get("items", []):
            entries.append({
                "id": str(item.get("file_id", "")),
                "name": str(item.get("name", "")),
                "type": "folder" if item.get("type") == "folder" else "file",
                "size": int(item.get("size", 0) or 0),
                "parent_id": str(item.get("parent_file_id", cid)),
            })
        return {"entries": entries, "total": len(entries)}

    def list_entries(self, cookie, cid="root"):
        return self.list_entries_payload(cookie, cid)["entries"]

    def _resolve_drive_id(self, refresh_token: str) -> str:
        data = self._api_post(refresh_token, "https://api.aliyundrive.com/v2/user/get", {})
        return data.get("default_drive_id", "")

    def create_folder(self, cookie, cid="root", folder_name=""):
        data = self._api_post(cookie, "https://api.aliyundrive.com/v2/file/create", {
            "drive_id": self._resolve_drive_id(refresh_token=cookie),
            "parent_file_id": cid or "root",
            "name": folder_name,
            "type": "folder",
            "check_name_mode": "refuse",
        })
        return {"cid": str(data.get("file_id", "")), "name": folder_name}

    def resolve_folder_id_by_path(self, cookie, relative_path):
        parts = [p.strip() for p in str(relative_path).split("/") if p.strip()]
        cid = "root"
        for name in parts:
            entries = self.list_entries(cookie, cid)
            found = next((e for e in entries if e.get("name") == name), None)
            if not found:
                return ""
            cid = found["id"]
        return cid

    def ensure_folder_id_by_path(self, cookie, relative_path):
        parts = [p.strip() for p in str(relative_path).split("/") if p.strip()]
        cid = "root"
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
        share_id_match = re.search(r'/s/([A-Za-z0-9]+)', str(share_url))
        if not share_id_match:
            raise RuntimeError("无法识别阿里云盘分享链接")
        return {"share_id": share_id_match.group(1), "receive_code": str(receive_code or "").strip()}

    def list_share_entries(self, cookie, share_payload, cid="root", offset=0, limit=200):
        share_id = share_payload["share_id"]
        receive_code = share_payload.get("receive_code", "")
        data = self._api_post(cookie, "https://api.aliyundrive.com/v2/share_link/get_share_by_anonymous", {
            "share_id": share_id,
        })
        share_token = data.get("share_token", "")
        if receive_code:
            self._api_post(cookie, "https://api.aliyundrive.com/v2/share_link/verify_code", {
                "share_id": share_id,
                "share_pwd": receive_code,
            })
        entries_data = self._api_post(cookie, "https://api.aliyundrive.com/v2/file/list_share", {
            "share_id": share_id,
            "parent_file_id": cid or "root",
            "limit": limit,
            "share_token": share_token,
        })
        entries = []
        for item in entries_data.get("items", []):
            entries.append({
                "id": str(item.get("file_id", "")),
                "name": str(item.get("name", "")),
                "type": "folder" if item.get("type") == "folder" else "file",
                "size": int(item.get("size", 0) or 0),
                "share_id": share_id,
                "share_token": share_token,
            })
        return {"entries": entries, "total": len(entries), "share": {**share_payload, "share_token": share_token}}

    def prepare_share_receive(self, cookie, share_payload, cid="root"):
        return {**share_payload, "target_cid": cid or "root"}

    def submit_share_receive(self, cookie, receive_payload, files):
        share_id = receive_payload["share_id"]
        share_token = receive_payload.get("share_token", "")
        target_cid = receive_payload.get("target_cid", "root")
        drive_id = self._resolve_drive_id(refresh_token=cookie)
        file_ids = [str(f.get("id", "")).strip() for f in (files or []) if str(f.get("id", "")).strip()]
        if not file_ids:
            raise RuntimeError("未选择要转存的文件")

        for fid in file_ids:
            self._api_post(cookie, "https://api.aliyundrive.com/v2/file/copy", {
                "drive_id": drive_id,
                "file_id": fid,
                "to_parent_file_id": target_cid,
                "share_id": share_id,
                "share_token": share_token,
            }, timeout=60)
        return {"success": True, "count": len(file_ids)}

    def probe_connectivity(self, cookie):
        try:
            self._ensure_access_token(cookie)
            return True
        except Exception:
            return False


register(AliyunProvider())
```

- [ ] **Step 2: 验证语法**

```bash
.venv/bin/python -m compileall app/providers/aliyun.py
```

- [ ] **Step 3: Commit**

```bash
git add app/providers/aliyun.py
git commit -m "feat: add AliyunProvider (阿里云盘)"
```

---

### Task 19: 运行完整加载验证 + dev server 测试

- [ ] **Step 1: 完整加载验证**

```bash
.venv/bin/python -c "
import app.providers.tianyi
import app.providers.pan123
import app.providers.aliyun
from app.providers.registry import list_all, get_all_capabilities
providers = list_all()
print('Registered providers:', [p.name for p in providers])
print('Capabilities:', get_all_capabilities())
assert len(providers) == 5, f'Expected 5 providers, got {len(providers)}'
print('OK - all 5 providers registered')
"
```

- [ ] **Step 2: 启动 dev server 测试**

```bash
.venv/bin/python -m compileall app main.py
.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 18080 &
sleep 2
curl -s http://localhost:18080/api/providers | python -m json.tool
```

预期返回 5 个 provider，115/quark enabled=true，其他 enabled=false。

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: verify all 5 providers registered and API working"
```
