# Scraper Multi-Provider Dynamic Adaptation

## Goal

Make the scraper file manager read available providers dynamically from the provider registry, with each provider exposing operations based on its actual capabilities. Remove all hardcoded 115/quark assumptions.

## Changes

### 1. `app/providers/base.py` — Add optional file-operation methods to ABC

Add 4 optional methods (default `NotImplementedError`) and 4 capability flags (default `False`):

- `rename_entry(cookie, entry_id, new_name, parent_id="") -> dict`
- `move_entries(cookie, entry_ids, target_id, source_id="") -> dict`
- `copy_entries(cookie, entry_ids, target_id, source_id="") -> dict`
- `delete_entries(cookie, entry_ids, parent_id="") -> dict`

Corresponding capability flags: `supports_rename`, `supports_move`, `supports_copy`, `supports_delete`.

### 2. `app/providers/pan115.py` and `quark.py` — Wire methods on provider classes

Add the 4 methods on `Pan115Provider` and `QuarkProvider`, each delegating to the existing standalone functions (e.g. `rename_115_entry`, `move_quark_entries`). Set the 4 capability flags to `True`.

### 3. `app/services/scraper.py` — Remove hardcoded provider knowledge

- Delete `SCRAPER_PROVIDER_ALIASES` dict (alias mapping table).
- Delete `SCRAPER_LEGACY_FILE_OPERATION_PROVIDERS` set (`{"115", "quark"}`).
- `normalize_scraper_provider()` — look up directly in registry by name, return empty string on miss.
- `_supports_scraper_file_operations()` — read provider capability flags from registry.
- 6 dispatch functions (`_list_provider_entries_payload`, `_create_provider_folder`, `_rename_provider_entry`, `_move_provider_entries`, `_copy_provider_entries`, `_delete_provider_entries`) — remove `if provider == "115"/"quark"` branches, always get provider object from registry and call its method.

### 4. `static/js/modules/scraper/core.js` — Dynamic operation buttons

- `normalizeProvider()` — remove hardcoded client-side alias map, look up only from server-returned provider list and `window.providerMeta`.
- Provider tabs — already rendered from server response, no change needed.
- Operation buttons (rename, copy, move, delete, scrape, rollback) — read `operations` object from server response. Supported: normal button. Unsupported: rendered but grayed out with tooltip "当前网盘不支持此操作".

## Result

- **115/quark**: no functional change, all operations work as before.
- **Tianyi/123pan/Aliyun**: appear as provider tabs with browse + create folder; rename/move/copy/delete buttons grayed out.
- **Future providers**: implement the optional methods + set capability flags on the provider class, scraper automatically supports them with no scraper code changes.
