# Scraper Multi-Provider Dynamic Adaptation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make scraper file manager read providers dynamically from registry, each exposing only its actual capabilities.

**Architecture:** Bottom-up refactor — ABC declares optional methods + capability flags → Pan115/Quark classes implement them → Scraper service removes hardcoded branches → Frontend removes alias map and legacy op fallback.

**Tech Stack:** Python (FastAPI), vanilla JS (no framework)

---

### Task 1: Add file-operation methods and capability flags to CloudProvider ABC

**Files:**
- Modify: `app/providers/base.py:17-24` (add capabilities), `app/providers/base.py:75-76` (add methods)

- [ ] **Step 1: Add capability flags to ABC**

```python
# In app/providers/base.py, after line 23:
    supports_strm: bool = False
    supports_monitor: bool = False
    # NEW — file operations
    supports_rename: bool = False
    supports_move: bool = False
    supports_copy: bool = False
    supports_delete: bool = False
```

- [ ] **Step 2: Add optional file-operation methods to ABC**

```python
# In app/providers/base.py, after the resolve_download_url method (line 75), add:

    # === 文件操作（可选，子类按能力覆写） ===
    def rename_entry(self, cookie: str, entry_id: str, new_name: str, parent_id: str = "") -> Dict[str, Any]:
        raise NotImplementedError(f"{self.label} 不支持重命名")

    def move_entries(self, cookie: str, entry_ids: List[str], target_id: str, source_id: str = "") -> Dict[str, Any]:
        raise NotImplementedError(f"{self.label} 不支持移动")

    def copy_entries(self, cookie: str, entry_ids: List[str], target_id: str, source_id: str = "") -> Dict[str, Any]:
        raise NotImplementedError(f"{self.label} 不支持复制")

    def delete_entries(self, cookie: str, entry_ids: List[str], parent_id: str = "") -> Dict[str, Any]:
        raise NotImplementedError(f"{self.label} 不支持删除")
```

- [ ] **Step 3: Verify syntax**

```bash
PYTHONPYCACHEPREFIX=/tmp/115-media-hub-pycache .venv/bin/python -m compileall app/providers/base.py
```

- [ ] **Step 4: Commit**

```bash
git add app/providers/base.py
git commit -m "feat: add file-operation methods and capability flags to CloudProvider ABC"
```

---

### Task 2: Wire file-operation methods on Pan115Provider

**Files:**
- Modify: `app/providers/pan115.py:1387-1391` (add capabilities), `app/providers/pan115.py:1472` (add methods before register)

- [ ] **Step 1: Set capability flags to True on Pan115Provider**

Replace lines 1387-1391:
```python
    supports_subscription = True
    supports_offline = True
    supports_fixed_share_link = True
    supports_strm = True
    supports_monitor = True
```
With:
```python
    supports_subscription = True
    supports_offline = True
    supports_fixed_share_link = True
    supports_strm = True
    supports_monitor = True
    supports_rename = True
    supports_move = True
    supports_copy = True
    supports_delete = True
```

- [ ] **Step 2: Add 4 file-operation methods on Pan115Provider**

After the `resolve_download_url` method (line 1473), before `register(Pan115Provider())` (line 1476), add:

```python
    def rename_entry(self, cookie, entry_id, new_name, parent_id=""):
        return rename_115_entry(cookie, entry_id, new_name, parent_id)

    def move_entries(self, cookie, entry_ids, target_id, source_id=""):
        return move_115_entries(cookie, entry_ids, target_id, source_id)

    def copy_entries(self, cookie, entry_ids, target_id, source_id=""):
        return copy_115_entries(cookie, entry_ids, target_id, source_id)

    def delete_entries(self, cookie, entry_ids, parent_id=""):
        return delete_115_entries(cookie, entry_ids, parent_id)
```

- [ ] **Step 3: Verify syntax**

```bash
PYTHONPYCACHEPREFIX=/tmp/115-media-hub-pycache .venv/bin/python -m compileall app/providers/pan115.py
```

- [ ] **Step 4: Commit**

```bash
git add app/providers/pan115.py
git commit -m "feat: wire file-operation methods on Pan115Provider"
```

---

### Task 3: Wire file-operation methods on QuarkProvider

**Files:**
- Modify: `app/providers/quark.py:1802-1806` (add capabilities), `app/providers/quark.py:1892` (add methods before register)

- [ ] **Step 1: Set capability flags to True on QuarkProvider**

Replace lines 1802-1806:
```python
    supports_subscription = True
    supports_offline = False
    supports_fixed_share_link = True
    supports_strm = False
    supports_monitor = False
```
With:
```python
    supports_subscription = True
    supports_offline = False
    supports_fixed_share_link = True
    supports_strm = False
    supports_monitor = False
    supports_rename = True
    supports_move = True
    supports_copy = True
    supports_delete = True
```

- [ ] **Step 2: Add 4 file-operation methods on QuarkProvider**

After the `resolve_download_url` method (line 1892), before `register(QuarkProvider())` (line 1895), add:

```python
    def rename_entry(self, cookie, entry_id, new_name, parent_id=""):
        return rename_quark_entry(cookie, entry_id, new_name, parent_id)

    def move_entries(self, cookie, entry_ids, target_id, source_id=""):
        return move_quark_entries(cookie, entry_ids, target_id, source_id)

    def copy_entries(self, cookie, entry_ids, target_id, source_id=""):
        return copy_quark_entries(cookie, entry_ids, target_id, source_id)

    def delete_entries(self, cookie, entry_ids, parent_id=""):
        return delete_quark_entries(cookie, entry_ids, parent_id)
```

- [ ] **Step 3: Verify syntax**

```bash
PYTHONPYCACHEPREFIX=/tmp/115-media-hub-pycache .venv/bin/python -m compileall app/providers/quark.py
```

- [ ] **Step 4: Commit**

```bash
git add app/providers/quark.py
git commit -m "feat: wire file-operation methods on QuarkProvider"
```

---

### Task 4: Remove hardcoded provider logic from scraper service

**Files:**
- Modify: `app/services/scraper.py:7-23` (imports), `:121-141` (constants), `:144-152` (normalize), `:178-186` (supports check), `:246-313` (dispatch functions)

- [ ] **Step 1: Remove standalone function imports**

Replace lines 7-23:
```python
from ..providers.pan115 import (
    copy_115_entries,
    create_115_folder,
    delete_115_entries,
    invalidate_115_entries_cache,
    list_115_entries_payload,
    move_115_entries,
    rename_115_entry,
)
from ..providers.quark import (
    copy_quark_entries,
    create_quark_folder,
    delete_quark_entries,
    list_quark_entries_payload,
    move_quark_entries,
    rename_quark_entry,
)
```
With:
```python
from ..providers.pan115 import invalidate_115_entries_cache
```

Keep the registry import:
```python
from ..providers.registry import get_or_none as get_provider_or_none, list_enabled as list_enabled_providers
```

- [ ] **Step 2: Delete SCRAPER_LEGACY_FILE_OPERATION_PROVIDERS and SCRAPER_PROVIDER_ALIASES**

Delete line 121 and lines 122-141 entirely:
```python
# DELETE: SCRAPER_LEGACY_FILE_OPERATION_PROVIDERS = {"115", "quark"}
# DELETE: SCRAPER_PROVIDER_ALIASES = { ... }
```

- [ ] **Step 3: Simplify normalize_scraper_provider**

Replace lines 144-152:
```python
def normalize_scraper_provider(value: Any) -> str:
    provider = str(value or "").strip().lower()
    if not provider:
        return ""
    aliased = SCRAPER_PROVIDER_ALIASES.get(provider, provider)
    p = get_provider_or_none(aliased)
    if p and p.supports_folder_browse:
        return p.name
    return ""
```
With:
```python
def normalize_scraper_provider(value: Any) -> str:
    name = str(value or "").strip().lower()
    if not name:
        return ""
    p = get_provider_or_none(name)
    if p and p.supports_folder_browse:
        return p.name
    return ""
```

- [ ] **Step 4: Simplify _supports_scraper_file_operations**

Replace lines 178-186:
```python
def _supports_scraper_file_operations(provider: str) -> bool:
    normalized = normalize_scraper_provider(provider)
    if normalized in SCRAPER_LEGACY_FILE_OPERATION_PROVIDERS:
        return True
    p = get_provider_or_none(normalized)
    return all(
        callable(getattr(p, method_name, None))
        for method_name in ("rename_entry", "move_entries", "copy_entries", "delete_entries")
    ) if p else False
```
With:
```python
def _supports_scraper_file_operations(provider: str) -> bool:
    normalized = normalize_scraper_provider(provider)
    p = get_provider_or_none(normalized) if normalized else None
    if not p:
        return False
    return bool(p.supports_rename and p.supports_move and p.supports_copy and p.supports_delete)
```

- [ ] **Step 5: Rewrite _list_provider_entries_payload — unified dispatch**

Replace lines 246-262:
```python
def _list_provider_entries_payload(
    provider: str,
    cookie: str,
    cid: str = "0",
    *,
    force_refresh: bool = False,
    folders_only: bool = False,
) -> Dict[str, Any]:
    target_id = str(cid or "0").strip() or "0"
    if provider == "quark":
        return list_quark_entries_payload(cookie, target_id, folders_only=folders_only)
    if provider == "115":
        return list_115_entries_payload(cookie, target_id, force_refresh=force_refresh, folders_only=folders_only)
    p = get_provider_or_none(provider)
    if not p:
        raise RuntimeError("网盘类型无效")
    return p.list_entries_payload(cookie, target_id, folders_only=folders_only)
```
With:
```python
def _list_provider_entries_payload(
    provider: str,
    cookie: str,
    cid: str = "0",
    *,
    folders_only: bool = False,
) -> Dict[str, Any]:
    target_id = str(cid or "0").strip() or "0"
    p = get_provider_or_none(provider)
    if not p:
        raise RuntimeError("网盘类型无效")
    return p.list_entries_payload(cookie, target_id, folders_only=folders_only)
```

- [ ] **Step 5b: Move force_refresh cache invalidation to list_scraper_entries**

In `list_scraper_entries` (line 414), replace:
```python
    payload = _list_provider_entries_payload(normalized, cookie, target_id, force_refresh=force_refresh, folders_only=False)
```
With:
```python
    if force_refresh:
        _invalidate_provider_parent(normalized, target_id)
    payload = _list_provider_entries_payload(normalized, cookie, target_id, folders_only=False)
```

- [ ] **Step 6: Rewrite _create_provider_folder — unified dispatch**

Replace lines 265-273:
```python
def _create_provider_folder(provider: str, cookie: str, cid: str, name: str) -> Dict[str, Any]:
    if provider == "quark":
        return create_quark_folder(cookie, cid, name)
    if provider == "115":
        return create_115_folder(cookie, cid, name)
    p = get_provider_or_none(provider)
    if not p:
        raise RuntimeError("网盘类型无效")
    return p.create_folder(cookie, cid, name)
```
With:
```python
def _create_provider_folder(provider: str, cookie: str, cid: str, name: str) -> Dict[str, Any]:
    p = get_provider_or_none(provider)
    if not p:
        raise RuntimeError("网盘类型无效")
    return p.create_folder(cookie, cid, name)
```

- [ ] **Step 7: Rewrite _rename_provider_entry — unified dispatch**

Replace lines 276-283:
```python
def _rename_provider_entry(provider: str, cookie: str, entry_id: str, new_name: str, parent_id: str = "") -> Dict[str, Any]:
    if provider == "quark":
        return rename_quark_entry(cookie, entry_id, new_name, parent_id)
    if provider == "115":
        return rename_115_entry(cookie, entry_id, new_name, parent_id)
    _require_scraper_operation(provider, "rename", "重命名")
    p = get_provider_or_none(provider)
    return p.rename_entry(cookie, entry_id, new_name, parent_id)
```
With:
```python
def _rename_provider_entry(provider: str, cookie: str, entry_id: str, new_name: str, parent_id: str = "") -> Dict[str, Any]:
    _require_scraper_operation(provider, "rename", "重命名")
    p = get_provider_or_none(provider)
    return p.rename_entry(cookie, entry_id, new_name, parent_id)
```

- [ ] **Step 8: Rewrite _move_provider_entries — unified dispatch**

Replace lines 286-293:
```python
def _move_provider_entries(provider: str, cookie: str, entry_ids: List[str], target_id: str, source_id: str = "") -> Dict[str, Any]:
    if provider == "quark":
        return move_quark_entries(cookie, entry_ids, target_id, source_id)
    if provider == "115":
        return move_115_entries(cookie, entry_ids, target_id, source_id)
    _require_scraper_operation(provider, "move", "移动")
    p = get_provider_or_none(provider)
    return p.move_entries(cookie, entry_ids, target_id, source_id)
```
With:
```python
def _move_provider_entries(provider: str, cookie: str, entry_ids: List[str], target_id: str, source_id: str = "") -> Dict[str, Any]:
    _require_scraper_operation(provider, "move", "移动")
    p = get_provider_or_none(provider)
    return p.move_entries(cookie, entry_ids, target_id, source_id)
```

- [ ] **Step 9: Rewrite _copy_provider_entries — unified dispatch**

Replace lines 296-303:
```python
def _copy_provider_entries(provider: str, cookie: str, entry_ids: List[str], target_id: str, source_id: str = "") -> Dict[str, Any]:
    if provider == "quark":
        return copy_quark_entries(cookie, entry_ids, target_id, source_id)
    if provider == "115":
        return copy_115_entries(cookie, entry_ids, target_id, source_id)
    _require_scraper_operation(provider, "copy", "复制")
    p = get_provider_or_none(provider)
    return p.copy_entries(cookie, entry_ids, target_id, source_id)
```
With:
```python
def _copy_provider_entries(provider: str, cookie: str, entry_ids: List[str], target_id: str, source_id: str = "") -> Dict[str, Any]:
    _require_scraper_operation(provider, "copy", "复制")
    p = get_provider_or_none(provider)
    return p.copy_entries(cookie, entry_ids, target_id, source_id)
```

- [ ] **Step 10: Rewrite _delete_provider_entries — unified dispatch**

Replace lines 306-313:
```python
def _delete_provider_entries(provider: str, cookie: str, entry_ids: List[str], parent_id: str = "") -> Dict[str, Any]:
    if provider == "quark":
        return delete_quark_entries(cookie, entry_ids, parent_id)
    if provider == "115":
        return delete_115_entries(cookie, entry_ids, parent_id)
    _require_scraper_operation(provider, "delete", "删除")
    p = get_provider_or_none(provider)
    return p.delete_entries(cookie, entry_ids, parent_id)
```
With:
```python
def _delete_provider_entries(provider: str, cookie: str, entry_ids: List[str], parent_id: str = "") -> Dict[str, Any]:
    _require_scraper_operation(provider, "delete", "删除")
    p = get_provider_or_none(provider)
    return p.delete_entries(cookie, entry_ids, parent_id)
```

- [ ] **Step 11: Verify syntax (validates all changes above)**

```bash
PYTHONPYCACHEPREFIX=/tmp/115-media-hub-pycache .venv/bin/python -m compileall app/services/scraper.py
```

- [ ] **Step 12: Commit**

```bash
git add app/services/scraper.py
git commit -m "refactor: remove hardcoded provider logic from scraper service, use registry-driven dispatch"
```

---

### Task 5: Clean up frontend hardcoded provider aliases and legacy op fallback

**Files:**
- Modify: `static/js/modules/scraper/core.js:76-106` (normalizeProvider), `:115-130` (getProviderOperations)

- [ ] **Step 1: Remove hardcoded aliases from normalizeProvider**

Replace lines 76-106:
```javascript
function normalizeProvider(value) {
    const rawInput = String(value || '').trim();
    const raw = rawInput.toLowerCase();
    if (!raw) return '115';
    const aliases = {
        pan115: '115',
        '115pan': '115',
        '夸克': 'quark',
        '夸克网盘': 'quark',
        '天翼': 'tianyi',
        '天翼云盘': 'tianyi',
        '189': 'tianyi',
        cloud189: 'tianyi',
        '123': '123pan',
        '123云盘': '123pan',
        alipan: 'aliyun',
        '阿里': 'aliyun',
        '阿里云盘': 'aliyun',
    };
    const normalized = aliases[raw] || raw;
    const known = [
        ...(Array.isArray(state?.providers) ? state.providers : []),
        ...(Array.isArray(window.providerMeta) ? window.providerMeta : []),
    ];
    const matched = known.find(item => {
        const providerName = String(item?.provider || item?.name || '').trim().toLowerCase();
        const linkType = String(item?.link_type || '').trim().toLowerCase();
        return providerName === normalized || linkType === normalized;
    });
    return String(matched?.provider || matched?.name || normalized || '115').trim() || '115';
}
```
With:
```javascript
function normalizeProvider(value) {
    const raw = String(value || '').trim().toLowerCase();
    if (!raw) return '115';
    const known = [
        ...(Array.isArray(state?.providers) ? state.providers : []),
        ...(Array.isArray(window.providerMeta) ? window.providerMeta : []),
    ];
    const matched = known.find(item => {
        const providerName = String(item?.provider || item?.name || '').trim().toLowerCase();
        return providerName === raw;
    });
    return String(matched?.provider || matched?.name || raw).trim();
}
```

- [ ] **Step 2: Remove legacy op fallback from getProviderOperations**

Replace lines 115-130:
```javascript
function getProviderOperations(provider = state.provider) {
    const normalized = normalizeProvider(provider);
    const info = getProviderInfo(normalized);
    if (info?.operations && typeof info.operations === 'object') return info.operations;
    const legacyFullOps = normalized === '115' || normalized === 'quark';
    return {
        browse: true,
        create_folder: true,
        rename: legacyFullOps,
        copy: legacyFullOps,
        move: legacyFullOps,
        delete: legacyFullOps,
        scrape: legacyFullOps,
        rollback: legacyFullOps,
    };
}
```
With:
```javascript
function getProviderOperations(provider = state.provider) {
    const normalized = normalizeProvider(provider);
    const info = getProviderInfo(normalized);
    if (info?.operations && typeof info.operations === 'object') return info.operations;
    return { browse: true, create_folder: true, rename: false, copy: false, move: false, delete: false, scrape: false, rollback: false };
}
```

- [ ] **Step 3: Verify dev server starts**

```bash
.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 18080 &
# Wait 3 seconds, then curl the providers endpoint
sleep 3
curl -s http://localhost:18080/scraper/providers | python -m json.tool
# Expected: providers array with 115, quark (enabled) having full operations; tianyi/123pan/aliyun having browse + create_folder only
kill %1 2>/dev/null
```

- [ ] **Step 4: Commit**

```bash
git add static/js/modules/scraper/core.js
git commit -m "refactor: remove hardcoded provider aliases and legacy op fallback in scraper frontend"
```
