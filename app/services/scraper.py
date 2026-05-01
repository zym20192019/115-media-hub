import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from ..core import *  # noqa: F401,F403
from ..providers.pan115 import (
    create_115_folder,
    delete_115_entries,
    invalidate_115_entries_cache,
    list_115_entries_payload,
    move_115_entries,
    rename_115_entry,
)
from ..providers.quark import (
    create_quark_folder,
    delete_quark_entries,
    list_quark_entries_payload,
    move_quark_entries,
    rename_quark_entry,
)
from ..services.subscription_episode import _extract_task_episodes_from_file_entry
from ..subscription_scoring import parse_resource_episode_meta


SCRAPER_JOB_LIMIT_DEFAULT = 20
SCRAPER_SCAN_MAX_DIRS = 80
SCRAPER_SCAN_MAX_ENTRIES = 1200
SCRAPER_TAG_PATTERNS: Dict[str, List[Tuple[str, str]]] = {
    "resolution": [
        (r"\b(?:4320p|8k)\b", "8K"),
        (r"\b(?:2160p|4k|uhd)\b", "2160p"),
        (r"\b1080p\b", "1080p"),
        (r"\b720p\b", "720p"),
        (r"\b480p\b", "480p"),
    ],
    "source": [
        (r"\bremux\b", "REMUX"),
        (r"\b(?:blu[-_. ]?ray|bdrip|bdremux)\b", "BluRay"),
        (r"\bweb[-_. ]?dl\b", "WEB-DL"),
        (r"\bwebrip\b", "WEBRip"),
        (r"\bhdtv\b", "HDTV"),
    ],
    "dynamic_range": [
        (r"\bdolby[ ._-]?vision\b|\bdv\b", "DV"),
        (r"\bhdr10\+?\b", "HDR10"),
        (r"\bhdr\b", "HDR"),
    ],
    "video": [
        (r"\bhevc\b|\bh\.?265\b|\bx265\b", "HEVC"),
        (r"\bh\.?264\b|\bx264\b", "H.264"),
        (r"\bav1\b", "AV1"),
        (r"\b10[-_. ]?bit\b", "10bit"),
    ],
    "audio": [
        (r"\batmos\b", "Atmos"),
        (r"\btruehd\b", "TrueHD"),
        (r"\bdts[-_. ]?hd\b", "DTS-HD"),
        (r"\bdts\b", "DTS"),
        (r"\bddp\d?(?:[._ ]?\d)?\b|\bdd\+?\b", "DDP"),
        (r"\baac\b", "AAC"),
        (r"\bflac\b", "FLAC"),
    ],
}


def normalize_scraper_provider(value: Any) -> str:
    provider = str(value or "").strip().lower()
    if provider in ("115", "pan115", "115pan"):
        return "115"
    if provider in ("quark", "夸克"):
        return "quark"
    return ""


def get_scraper_provider_label(provider: str) -> str:
    return "夸克" if provider == "quark" else "115"


def _get_provider_cookie(provider: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    active_cfg = cfg or get_config()
    return str(active_cfg.get("cookie_quark" if provider == "quark" else "cookie_115", "") or "").strip()


def build_scraper_providers_payload(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    active_cfg = cfg or get_config()
    providers = []
    for provider in ("115", "quark"):
        cookie = _get_provider_cookie(provider, active_cfg)
        providers.append(
            {
                "provider": provider,
                "label": get_scraper_provider_label(provider),
                "configured": bool(cookie),
                "operations": {
                    "browse": True,
                    "create_folder": True,
                    "rename": True,
                    "move": True,
                    "delete": True,
                    "scrape": True,
                    "rollback": True,
                },
            }
        )
    return {"ok": True, "providers": providers}


def _require_provider_cookie(provider: str) -> str:
    normalized = normalize_scraper_provider(provider)
    if not normalized:
        raise RuntimeError("网盘类型无效")
    cookie = _get_provider_cookie(normalized)
    if not cookie:
        raise RuntimeError(f"请先配置 {get_scraper_provider_label(normalized)} Cookie")
    return cookie


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
    return list_115_entries_payload(cookie, target_id, force_refresh=force_refresh, folders_only=folders_only)


def _create_provider_folder(provider: str, cookie: str, cid: str, name: str) -> Dict[str, Any]:
    if provider == "quark":
        return create_quark_folder(cookie, cid, name)
    return create_115_folder(cookie, cid, name)


def _rename_provider_entry(provider: str, cookie: str, entry_id: str, new_name: str, parent_id: str = "") -> Dict[str, Any]:
    if provider == "quark":
        return rename_quark_entry(cookie, entry_id, new_name, parent_id)
    return rename_115_entry(cookie, entry_id, new_name, parent_id)


def _move_provider_entries(provider: str, cookie: str, entry_ids: List[str], target_id: str, source_id: str = "") -> Dict[str, Any]:
    if provider == "quark":
        return move_quark_entries(cookie, entry_ids, target_id, source_id)
    return move_115_entries(cookie, entry_ids, target_id, source_id)


def _delete_provider_entries(provider: str, cookie: str, entry_ids: List[str], parent_id: str = "") -> Dict[str, Any]:
    if provider == "quark":
        return delete_quark_entries(cookie, entry_ids, parent_id)
    return delete_115_entries(cookie, entry_ids, parent_id)


def _invalidate_provider_parent(provider: str, parent_id: str = "") -> None:
    if provider == "115":
        invalidate_115_entries_cache(parent_id)


def _compact_scraper_entry(entry: Dict[str, Any], parent_id: str = "", parent_path: str = "") -> Dict[str, Any]:
    item = entry if isinstance(entry, dict) else {}
    is_dir = bool(item.get("is_dir"))
    entry_id = str(item.get("id", "") or "").strip()
    name = str(item.get("name", "") or "").strip()
    if not entry_id or not name:
        return {}
    effective_parent = str(item.get("parent_id", "") or parent_id or "0").strip() or "0"
    effective_parent_path = normalize_relative_path(str(item.get("parent_path", "") or parent_path or "").strip())
    path = normalize_relative_path(str(item.get("path", "") or "").strip()) or normalize_relative_path(join_relative_path(effective_parent_path, name))
    payload: Dict[str, Any] = {
        "id": entry_id,
        "name": name,
        "is_dir": is_dir,
        "size": parse_int(item.get("size") or 0),
        "parent_id": effective_parent,
        "parent_path": effective_parent_path,
        "path": path,
        "modified_at": str(item.get("modified_at", "") or "").strip(),
    }
    if is_dir:
        payload["cid"] = str(item.get("cid", "") or entry_id).strip() or entry_id
    else:
        payload["fid"] = str(item.get("fid", "") or entry_id).strip() or entry_id
    return payload


def _scraper_entry_path(entry: Dict[str, Any]) -> str:
    item = entry if isinstance(entry, dict) else {}
    path = normalize_relative_path(str(item.get("path", "") or "").strip())
    if path:
        return path
    parent_path = normalize_relative_path(str(item.get("parent_path", "") or "").strip())
    name = str(item.get("name", "") or "").strip()
    return normalize_relative_path(join_relative_path(parent_path, name))


def _scraper_path_depth(path: str) -> int:
    normalized = normalize_relative_path(str(path or "").strip())
    return len([part for part in normalized.split("/") if part])


def _is_scraper_path_descendant(path: str, ancestor_path: str) -> bool:
    normalized_path = normalize_relative_path(str(path or "").strip())
    normalized_ancestor = normalize_relative_path(str(ancestor_path or "").strip())
    if not normalized_path or not normalized_ancestor:
        return False
    return normalized_path == normalized_ancestor or normalized_path.startswith(f"{normalized_ancestor}/")


def _normalize_scraper_selected_entries(selected: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for raw in selected or []:
        item = raw if isinstance(raw, dict) else {}
        entry = _compact_scraper_entry(
            item,
            str(item.get("parent_id", "") or "0"),
            normalize_relative_path(str(item.get("parent_path", "") or "")),
        )
        if not entry:
            continue
        entry["path"] = _scraper_entry_path(entry)
        candidates.append(entry)

    if not candidates:
        return []

    candidates.sort(
        key=lambda item: (
            _scraper_path_depth(str(item.get("path", "") or "")),
            0 if item.get("is_dir") else 1,
            str(item.get("path", "") or "").lower(),
            str(item.get("id", "") or ""),
        )
    )
    normalized: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    seen_paths: Set[str] = set()
    for entry in candidates:
        entry_id = str(entry.get("id", "") or "").strip()
        entry_path = _scraper_entry_path(entry)
        if not entry_id or not entry_path:
            continue
        if entry_id in seen_ids or entry_path in seen_paths:
            continue
        if any(existing.get("is_dir") and _is_scraper_path_descendant(entry_path, str(existing.get("path", "") or "")) for existing in normalized):
            continue
        normalized.append(entry)
        seen_ids.add(entry_id)
        seen_paths.add(entry_path)
    return normalized


def list_scraper_entries(provider: str, cid: str = "0", force_refresh: bool = False, search: str = "") -> Dict[str, Any]:
    normalized = normalize_scraper_provider(provider)
    cookie = _require_provider_cookie(normalized)
    target_id = str(cid or "0").strip() or "0"
    payload = _list_provider_entries_payload(normalized, cookie, target_id, force_refresh=force_refresh, folders_only=False)
    entries = [
        compact
        for compact in (_compact_scraper_entry(item, target_id) for item in (payload.get("entries", []) if isinstance(payload, dict) else []))
        if compact
    ]
    keyword = str(search or "").strip().lower()
    if keyword:
        entries = [item for item in entries if keyword in str(item.get("name", "")).lower()]
    summary = payload.get("summary", {}) if isinstance(payload, dict) and isinstance(payload.get("summary"), dict) else {}
    return {
        "ok": True,
        "provider": normalized,
        "cid": target_id,
        "entries": entries,
        "summary": {
            "folder_count": max(0, parse_int(summary.get("folder_count", 0), 0)),
            "file_count": max(0, parse_int(summary.get("file_count", 0), 0)),
        },
    }


def create_scraper_folder(provider: str, cid: str, name: str) -> Dict[str, Any]:
    normalized = normalize_scraper_provider(provider)
    cookie = _require_provider_cookie(normalized)
    parent_id = str(cid or "0").strip() or "0"
    folder = _create_provider_folder(normalized, cookie, parent_id, str(name or "").strip())
    _invalidate_provider_parent(normalized, parent_id)
    return {"ok": True, "provider": normalized, "cid": parent_id, "folder": folder}


def rename_scraper_entry(provider: str, entry_id: str, parent_id: str, name: str) -> Dict[str, Any]:
    normalized = normalize_scraper_provider(provider)
    cookie = _require_provider_cookie(normalized)
    result = _rename_provider_entry(normalized, cookie, entry_id, name, parent_id)
    _invalidate_provider_parent(normalized, parent_id)
    return {"ok": True, "provider": normalized, "entry": result}


def check_scraper_folder_rename_warning(provider: str, old_path: str, new_path: str) -> Dict[str, Any]:
    normalized = normalize_scraper_provider(provider) or "115"
    normalized_old_path = normalize_relative_path(str(old_path or "").strip())
    normalized_new_path = normalize_relative_path(str(new_path or "").strip())
    if not normalized_old_path or not normalized_new_path:
        raise RuntimeError("文件夹路径无效")
    warning = _collect_scraper_subscription_rename_warning(normalized, normalized_old_path, normalized_new_path)
    return {
        "ok": True,
        "provider": normalized,
        "old_path": normalized_old_path,
        "new_path": normalized_new_path,
        "warning": warning,
    }


def move_scraper_entries(provider: str, entry_ids: List[str], target_cid: str, source_cid: str = "") -> Dict[str, Any]:
    normalized = normalize_scraper_provider(provider)
    cookie = _require_provider_cookie(normalized)
    result = _move_provider_entries(normalized, cookie, entry_ids, target_cid, source_cid)
    _invalidate_provider_parent(normalized, source_cid)
    _invalidate_provider_parent(normalized, target_cid)
    return {"ok": True, "provider": normalized, "result": result}


def delete_scraper_entries(provider: str, entry_ids: List[str], parent_id: str = "") -> Dict[str, Any]:
    normalized = normalize_scraper_provider(provider)
    cookie = _require_provider_cookie(normalized)
    result = _delete_provider_entries(normalized, cookie, entry_ids, parent_id)
    _invalidate_provider_parent(normalized, parent_id)
    return {"ok": True, "provider": normalized, "result": result}


def _strip_extension(name: str) -> str:
    stem, _ = os.path.splitext(str(name or "").strip())
    return stem or str(name or "").strip()


def _is_scraper_excluded_archive(name: str) -> bool:
    return os.path.splitext(str(name or "").strip())[1].lower() in {".zip", ".rar"}


def _clean_search_title(value: str) -> str:
    text = _strip_extension(value)
    text = re.sub(r"[\[\(（【].{0,90}?(?:2160p|1080p|720p|4k|uhd|hdr|web[-_. ]?dl|bluray|remux|x26[45]|hevc|aac|dts|atmos|第.+?季|s\d{1,2}e\d{1,4}).{0,90}?[\]\)）】]", " ", text, flags=re.I)
    text = re.sub(r"\b(19|20)\d{2}\b", " ", text)
    text = re.sub(r"\bS\d{1,2}\s*E\d{1,4}\b|\bEP?\s*\d{1,4}\b|\bE\d{1,4}\b", " ", text, flags=re.I)
    text = re.sub(r"\bS\d{1,2}\b|\bSeason\s*\d{1,2}\b", " ", text, flags=re.I)
    text = re.sub(r"第\s*[零〇一二三四五六七八九十两兩0-9]{1,4}\s*(?:季|集|话|話)", " ", text)
    text = re.sub(r"(?:全|共)\s*\d{1,4}\s*(?:集|话|話)", " ", text)
    text = re.sub(
        r"\b(?:2160p|1080p|720p|480p|4k|8k|uhd|web[-_. ]?dl|webrip|blu[-_. ]?ray|bdrip|"
        r"bdremux|remux|hdtv|hdr10\+?|hdr|dolby[ ._-]?vision|dv|hevc|h\.?265|x265|h\.?264|x264|"
        r"av1|aac|flac|ddp\d?(?:[._ ]?\d)?|dd\+?|dd\d(?:[._ ]?\d)?|dts[-_. ]?hd|dts|truehd|atmos|10[-_. ]?bit)\b",
        " ",
        text,
        flags=re.I,
    )
    text = re.sub(r"\b(?:complete|proper|repack|extended|uncut|internal|multi|chs|cht|gb|big5|简繁|简中|繁中|中字|字幕)\b", " ", text, flags=re.I)
    text = re.sub(r"[\._\-]+", " ", text)
    text = re.sub(r"\s*(?:\||/|／|·|•)\s*", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_.")
    return text or _strip_extension(value)


def _scraper_keyword_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").lower())


def _split_scraper_title_parts(value: str) -> List[str]:
    text = _strip_extension(value)
    text = re.sub(r"^[\[\(（【][^\]\)）】]{1,40}[\]\)）】]\s*", " ", text)
    text = re.sub(r"[\[\(（【][0-9a-f]{8}[\]\)）】]", " ", text, flags=re.I)
    parts = []
    for segment in re.split(r"[\\/]+", text):
        segment = segment.strip()
        if not segment:
            continue
        parts.append(segment)
        for part in re.split(r"\s+(?:-|–|—|\||/|／|·|•)\s+", segment):
            part = part.strip()
            if part and part != segment:
                parts.append(part)
    return parts


def _common_scraper_prefix(names: List[str]) -> str:
    cleaned = [_clean_search_title(name) for name in names if str(name or "").strip()]
    cleaned = [item for item in cleaned if len(_scraper_keyword_key(item)) >= 2]
    if len(cleaned) < 2:
        return ""
    prefix = os.path.commonprefix(cleaned).strip(" -_.")
    return _clean_search_title(prefix) if len(_scraper_keyword_key(prefix)) >= 2 else ""


def build_scraper_keyword_suggestions(selected: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    weighted: Dict[str, Dict[str, Any]] = {}

    def add_candidate(raw: str, score: int, source: str = "") -> None:
        cleaned = _clean_search_title(raw)
        key = _scraper_keyword_key(cleaned)
        if len(key) < 2:
            return
        if len(cleaned) > 80:
            cleaned = cleaned[:80].strip()
        item = weighted.get(key)
        if not item:
            weighted[key] = {"keyword": cleaned, "score": 0, "sources": set()}
            item = weighted[key]
        item["score"] += score
        if source:
            item["sources"].add(source)

    selected_names: List[str] = []
    parent_names: List[str] = []
    for raw in selected:
        item = raw if isinstance(raw, dict) else {}
        name = str(item.get("name", "") or "").strip()
        path = normalize_relative_path(str(item.get("path", "") or ""))
        parent_path = normalize_relative_path(str(item.get("parent_path", "") or ""))
        if name:
            selected_names.append(name)
            add_candidate(name, 32 if item.get("is_dir") else 18, "选中项")
        if path:
            path_parts = [part for part in path.split("/") if part]
            if len(path_parts) > 1:
                parent_names.extend(path_parts[:-1])
            for part in path_parts[-3:]:
                add_candidate(part, 22 if part != name else 8, "路径")
        if parent_path:
            parts = [part for part in parent_path.split("/") if part]
            parent_names.extend(parts[-2:])
            for part in parts[-2:]:
                add_candidate(part, 26, "父文件夹")
        for part in _split_scraper_title_parts(name or path):
            add_candidate(part, 10, "拆分")

    common_prefix = _common_scraper_prefix(selected_names)
    if common_prefix:
        add_candidate(common_prefix, 34, "公共前缀")

    year = _extract_year_from_names(selected_names + parent_names)
    enriched: List[Dict[str, Any]] = []
    for item in weighted.values():
        keyword = str(item.get("keyword", "") or "").strip()
        if not keyword:
            continue
        score = int(item.get("score", 0) or 0)
        key = _scraper_keyword_key(keyword)
        sources = item.get("sources", set()) if isinstance(item.get("sources"), set) else set()
        if re.fullmatch(r"(?:s\d{1,2}|season\d{1,2}|\d{1,4})", key, re.I):
            score -= 35
        if _contains_cjk(keyword):
            score += 25
        if "父文件夹" in sources:
            score += 18
        if re.search(r"\b(?:ddp|aac|dts|hevc|webdl|bluray|remux|hdr|2160p|1080p)\b", keyword, re.I):
            score -= 18
        if year and year not in keyword:
            score += 4
        enriched.append(
            {
                "keyword": keyword,
                "score": max(0, score),
                "source": "、".join(sorted(item.get("sources", set()))) if isinstance(item.get("sources"), set) else "",
            }
        )
        if year and keyword and year not in keyword:
            enriched.append({"keyword": f"{keyword} {year}", "score": max(0, score - 3), "source": "标题+年份"})

    seen: Set[str] = set()
    suggestions: List[Dict[str, Any]] = []
    for item in sorted(enriched, key=lambda payload: int(payload.get("score", 0) or 0), reverse=True):
        key = _scraper_keyword_key(str(item.get("keyword", "") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        suggestions.append(item)
        if len(suggestions) >= 5:
            break
    return suggestions


def _extract_year_from_names(names: List[str]) -> str:
    for name in names:
        matched = re.search(r"\b(19|20)\d{2}\b", str(name or ""))
        if matched:
            return matched.group(0)
    return ""


def _looks_like_tv(names: List[str]) -> bool:
    text = " ".join(str(name or "") for name in names)
    if re.search(r"\bS\d{1,2}\s*E\d{1,4}\b|\bEP?\s*\d{1,4}\b", text, re.I):
        return True
    if re.search(r"\bS\d{1,2}\b|\bSeason\s*\d{1,2}\b", text, re.I):
        return True
    if re.search(r"第\s*[零〇一二三四五六七八九十两兩0-9]{1,4}\s*(?:季|集|话|話)|(?:全|共)\s*\d{1,4}\s*(?:集|话|話)|完结|完結", text):
        return True
    return False


def _build_task_from_tmdb(tmdb: Dict[str, Any], options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = tmdb if isinstance(tmdb, dict) else {}
    opts = options if isinstance(options, dict) else {}
    media_type = normalize_tmdb_media_type(payload.get("tmdb_media_type") or payload.get("media_type"), "movie")
    season = max(1, parse_int(opts.get("season") or payload.get("season") or 1, 1))
    episode_mode = normalize_tmdb_episode_mode(payload.get("tmdb_episode_mode") or payload.get("episode_mode") or "seasonal")
    return {
        "media_type": media_type,
        "season": season,
        "multi_season_mode": media_type == "tv" and episode_mode == "absolute",
        "anime_mode": media_type == "tv" and episode_mode == "absolute",
        "tmdb_id": max(0, parse_int(payload.get("tmdb_id") or payload.get("id") or 0, 0)),
        "tmdb_media_type": media_type,
        "tmdb_total_episodes": max(0, parse_int(payload.get("tmdb_total_episodes") or payload.get("total_episodes") or 0, 0)),
        "tmdb_total_seasons": max(0, parse_int(payload.get("tmdb_total_seasons") or payload.get("total_seasons") or 0, 0)),
        "tmdb_season_episode_map": normalize_tmdb_season_episode_map(payload.get("tmdb_season_episode_map") or payload.get("season_episode_map") or {}),
        "tmdb_episode_mode": episode_mode,
    }


def _score_tmdb_candidate(query: str, year: str, item: Dict[str, Any]) -> int:
    query_key = re.sub(r"\W+", "", str(query or "").lower())
    title_key = re.sub(r"\W+", "", str(item.get("title", "") or "").lower())
    original_key = re.sub(r"\W+", "", str(item.get("original_title", "") or "").lower())
    score = 35
    if query_key and query_key in {title_key, original_key}:
        score += 35
    elif query_key and (query_key in title_key or title_key in query_key or query_key in original_key or original_key in query_key):
        score += 20
    if year and str(item.get("year", "")) == year:
        score += 20
    if float(item.get("popularity", 0) or 0) > 10:
        score += 5
    return min(100, score)


def identify_scraper_media(payload: Dict[str, Any]) -> Dict[str, Any]:
    provider = normalize_scraper_provider(payload.get("provider", "115")) or "115"
    selected = _normalize_scraper_selected_entries(payload.get("entries", []) if isinstance(payload.get("entries"), list) else [])
    names = [str(item.get("path") or item.get("name") or "").strip() for item in selected if isinstance(item, dict)]
    if not names:
        return {"ok": True, "provider": provider, "query": "", "media_type": "movie", "year": "", "keywords": [], "items": [], "candidates": []}
    keywords = build_scraper_keyword_suggestions([item for item in selected if isinstance(item, dict)])
    query = str(keywords[0].get("keyword", "") if keywords else _clean_search_title(names[0])).strip()
    media_type = "tv" if _looks_like_tv(names) else "movie"
    year = _extract_year_from_names(names)
    binding = {}
    return {
        "ok": True,
        "provider": provider,
        "tmdb_configured": not bool(validate_tmdb_runtime_config(get_config())),
        "query": query,
        "media_type": media_type,
        "year": year,
        "keywords": keywords,
        "items": [],
        "candidates": [],
        "binding": binding,
    }


def _selected_option_enabled(options: Any, key: str) -> bool:
    if isinstance(options, dict):
        return bool(options.get(key, False))
    if isinstance(options, list):
        return key in {str(item or "").strip() for item in options}
    return False


def extract_scraper_tags(name: str, preserve_options: Any) -> List[str]:
    tags: List[str] = []
    text = str(name or "")
    enabled_groups = {
        "resolution": _selected_option_enabled(preserve_options, "resolution"),
        "source": _selected_option_enabled(preserve_options, "source"),
        "dynamic_range": _selected_option_enabled(preserve_options, "dynamic_range"),
        "video": _selected_option_enabled(preserve_options, "video"),
        "audio": _selected_option_enabled(preserve_options, "audio"),
    }
    seen: Set[str] = set()
    for group, patterns in SCRAPER_TAG_PATTERNS.items():
        if not enabled_groups.get(group):
            continue
        for pattern, label in patterns:
            if re.search(pattern, text, re.I) and label not in seen:
                seen.add(label)
                tags.append(label)
    return tags


def sanitize_scraper_name(value: str, fallback: str = "Untitled") -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", " ", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text).strip(" .")
    return (text or fallback)[:180]


def _contains_cjk(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(value or "")))


def choose_scraper_title(tmdb: Dict[str, Any], language: str = "zh", fallback: str = "") -> str:
    payload = tmdb if isinstance(tmdb, dict) else {}
    normalized_language = str(language or "zh").strip().lower()
    if normalized_language in ("", "auto", "default", "config"):
        cfg_language = str((get_config() or {}).get("tmdb_language", "zh-CN") or "zh-CN").strip().lower()
        normalized_language = "en" if cfg_language.startswith("en") else "zh"
    localized = str(payload.get("tmdb_localized_title") or payload.get("tmdb_title") or payload.get("title") or "").strip()
    english = str(payload.get("tmdb_english_title") or "").strip()
    original = str(payload.get("tmdb_original_title") or payload.get("original_title") or "").strip()
    aliases = payload.get("tmdb_aliases") or payload.get("aliases") or []
    alias_values = [str(item or "").strip() for item in aliases if str(item or "").strip()] if isinstance(aliases, list) else []
    if normalized_language in ("en", "english"):
        return sanitize_scraper_name(english or (original if original and not _contains_cjk(original) else "") or localized or fallback)
    if localized and _contains_cjk(localized):
        return sanitize_scraper_name(localized)
    cjk_alias = next((item for item in alias_values if _contains_cjk(item)), "")
    return sanitize_scraper_name(cjk_alias or localized or fallback)


def _build_tag_suffix(tags: List[str]) -> str:
    cleaned = [sanitize_scraper_name(tag, "") for tag in tags if sanitize_scraper_name(tag, "")]
    return f" [{' '.join(cleaned)}]" if cleaned else ""


def _build_scraper_folder_title(title: str, year: str, tmdb: Dict[str, Any], options: Dict[str, Any]) -> str:
    year_suffix = f" ({year})" if year else ""
    folder_title = sanitize_scraper_name(f"{title}{year_suffix}")
    if bool(options.get("include_tmdb_id", False)):
        tmdb_id = max(0, parse_int(tmdb.get("tmdb_id") or tmdb.get("id") or 0, 0))
        if tmdb_id > 0:
            folder_title = sanitize_scraper_name(f"{folder_title} [tmdbid-{tmdb_id}]")
    return folder_title


def _build_scraper_media_titles(tmdb: Dict[str, Any], options: Dict[str, Any], fallback: str = "") -> Tuple[str, str, str]:
    language = str(options.get("title_language", "auto") or "auto")
    title = choose_scraper_title(tmdb, language, fallback=_clean_search_title(fallback))
    year = normalize_tmdb_year(tmdb.get("tmdb_year") or tmdb.get("year") or "") or _extract_year_from_names([fallback])
    file_title = sanitize_scraper_name(f"{title}{f' ({year})' if year else ''}")
    folder_title = _build_scraper_folder_title(title, year, tmdb, options)
    return title, file_title, folder_title


def _resolve_scraper_selection_mode(selected: List[Dict[str, Any]], options: Dict[str, Any]) -> str:
    items = _normalize_scraper_selected_entries(selected)
    single_folder_selection = len(items) == 1 and bool(items[0].get("is_dir"))
    requested = str((options if isinstance(options, dict) else {}).get("selection_mode", "") or "").strip().lower()
    if requested == "folder" and single_folder_selection:
        return "folder"
    if requested == "contents":
        return "contents"
    return "folder" if single_folder_selection else "contents"


def _relative_parent_path_from_base(parent_path: str, base_path: str) -> str:
    source = normalize_relative_path(str(parent_path or "").strip())
    base = normalize_relative_path(str(base_path or "").strip())
    if not source:
        return ""
    if not base:
        return source
    if source == base:
        return ""
    prefix = f"{base}/"
    if source.startswith(prefix):
        return source[len(prefix):]
    return source


def _format_tv_episode_code(task: Dict[str, Any], episodes: Set[int], default_season: int) -> Tuple[str, str]:
    normalized_values = sorted({max(0, int(value or 0)) for value in episodes if max(0, int(value or 0)) > 0})
    if not normalized_values:
        return "", "无法识别集数"
    season_map = normalize_tmdb_season_episode_map(task.get("tmdb_season_episode_map", {}))
    if is_subscription_multi_season_mode(task) and season_map:
        mapped = [convert_subscription_absolute_to_season_episode(task, value) for value in normalized_values]
        mapped = [(season, episode) for season, episode in mapped if season > 0 and episode > 0]
        if not mapped:
            return "", "连续编号无法映射到 TMDB 季集"
        seasons = {season for season, _ in mapped}
        if len(seasons) > 1:
            return "", "单个文件跨季，暂不自动命名"
        season_no = next(iter(seasons))
        episode_values = sorted({episode for _, episode in mapped})
    else:
        season_no = max(1, int(default_season or task.get("season", 1) or 1))
        episode_values = normalized_values
    if len(episode_values) == 1:
        return f"S{season_no:02d}E{episode_values[0]:02d}", ""
    return f"S{season_no:02d}E{episode_values[0]:02d}-E{episode_values[-1]:02d}", ""


def _build_scraper_target_path(entry: Dict[str, Any], tmdb: Dict[str, Any], options: Dict[str, Any]) -> Tuple[str, str]:
    media_type = normalize_tmdb_media_type(tmdb.get("tmdb_media_type") or tmdb.get("media_type"), "movie")
    organize_into_media_folder = bool(options.get("organize_into_media_folder", True))
    use_season_subfolder = bool(options.get("use_season_subfolder", True))
    preserve_source_parent_path = bool(options.get("preserve_source_parent_path", False))
    source_relative_parent_path = _relative_parent_path_from_base(
        str(entry.get("parent_path", "") or ""),
        str(options.get("base_path", "") or ""),
    )
    _, ext = os.path.splitext(str(entry.get("name", "") or ""))
    tags = extract_scraper_tags(str(entry.get("name", "") or ""), options.get("preserve_tags", {})) if bool(options.get("preserve_file_info", False)) else []
    tag_suffix = _build_tag_suffix(tags)
    _, file_title, folder_title = _build_scraper_media_titles(tmdb, options, str(entry.get("name", "") or ""))
    if media_type == "tv":
        task = _build_task_from_tmdb(tmdb, options)
        episodes = _extract_task_episodes_from_file_entry(
            task,
            str(entry.get("path") or entry.get("name") or ""),
            parent_path=normalize_relative_path(str(entry.get("parent_path", "") or "")),
        )
        episode_code, issue = _format_tv_episode_code(task, episodes, max(1, parse_int(options.get("season") or task.get("season") or 1, 1)))
        if issue:
            return "", issue
        season_no = max(1, int(episode_code[1:3] or options.get("season") or 1))
        file_name = sanitize_scraper_name(f"{file_title} - {episode_code}{tag_suffix}") + ext
        if preserve_source_parent_path:
            return normalize_relative_path(join_relative_path(source_relative_parent_path, file_name)), ""
        if not organize_into_media_folder:
            return file_name, ""
        if not use_season_subfolder:
            return normalize_relative_path(join_relative_path(folder_title, file_name)), ""
        return normalize_relative_path(join_relative_path(folder_title, f"Season {season_no:02d}", file_name)), ""
    file_name = sanitize_scraper_name(f"{file_title}{tag_suffix}") + ext
    if preserve_source_parent_path:
        return normalize_relative_path(join_relative_path(source_relative_parent_path, file_name)), ""
    if not organize_into_media_folder:
        return file_name, ""
    return normalize_relative_path(join_relative_path(folder_title, file_name)), ""


def _walk_existing_folder(provider: str, cookie: str, base_cid: str, folder_path: str) -> Tuple[str, bool]:
    current = str(base_cid or "0").strip() or "0"
    parts = [part for part in normalize_relative_path(folder_path).split("/") if part]
    for part in parts:
        payload = _list_provider_entries_payload(provider, cookie, current, folders_only=True)
        entries = payload.get("entries", []) if isinstance(payload, dict) and isinstance(payload.get("entries"), list) else []
        matched = next((item for item in entries if item.get("is_dir") and str(item.get("name", "") or "").strip() == part), None)
        if not matched:
            return "", False
        current = str(matched.get("id") or matched.get("cid") or "").strip() or "0"
    return current, True


def _ensure_folder_from_base(provider: str, cookie: str, base_cid: str, folder_path: str) -> str:
    current = str(base_cid or "0").strip() or "0"
    for part in [part for part in normalize_relative_path(folder_path).split("/") if part]:
        payload = _list_provider_entries_payload(provider, cookie, current, folders_only=True)
        entries = payload.get("entries", []) if isinstance(payload, dict) and isinstance(payload.get("entries"), list) else []
        matched = next((item for item in entries if item.get("is_dir") and str(item.get("name", "") or "").strip() == part), None)
        if matched:
            current = str(matched.get("id") or matched.get("cid") or "").strip() or current
            continue
        created = _create_provider_folder(provider, cookie, current, part)
        current = str(created.get("id", "") or "").strip() or current
    return current


def _target_name_exists(provider: str, cookie: str, parent_id: str, target_name: str, same_entry_id: str = "") -> bool:
    if not parent_id:
        return False
    payload = _list_provider_entries_payload(provider, cookie, parent_id, folders_only=False)
    entries = payload.get("entries", []) if isinstance(payload, dict) and isinstance(payload.get("entries"), list) else []
    for item in entries:
        if str(item.get("name", "") or "").strip() != target_name:
            continue
        if same_entry_id and str(item.get("id", "") or "").strip() == same_entry_id:
            continue
        return True
    return False


def _is_scraper_path_related(left_path: str, right_path: str) -> bool:
    normalized_left = normalize_relative_path(str(left_path or "").strip())
    normalized_right = normalize_relative_path(str(right_path or "").strip())
    if not normalized_left or not normalized_right:
        return False
    if normalized_left == normalized_right:
        return True
    return normalized_left.startswith(f"{normalized_right}/") or normalized_right.startswith(f"{normalized_left}/")


def _collect_scraper_subscription_path_warning(
    provider: str,
    candidate_paths: List[str],
    *,
    kind: str = "generic",
) -> str:
    normalized_provider = normalize_scraper_provider(provider) or "115"
    normalized_paths = unique_preserve_order(
        [normalize_relative_path(str(item or "").strip()) for item in (candidate_paths or []) if normalize_relative_path(str(item or "").strip())]
    )
    if not normalized_paths:
        return ""

    cfg = get_config()
    tasks = cfg.get("subscription_tasks", []) if isinstance(cfg.get("subscription_tasks"), list) else []
    for raw_task in tasks:
        task = normalize_subscription_task(raw_task or {})
        if not task.get("name"):
            continue
        if normalize_subscription_provider(task.get("provider", "115"), fallback="115") != normalized_provider:
            continue
        task_savepath = normalize_relative_path(str(task.get("savepath", "") or "").strip())
        if not task_savepath:
            continue
        label = str(task.get("title", "") or task.get("name", "") or "").strip() or "未命名任务"
        matched_path = ""
        for candidate_path in normalized_paths:
            if _is_scraper_path_related(candidate_path, task_savepath):
                matched_path = candidate_path
                break
        if not matched_path:
            continue
        return f"文件夹【{matched_path}】用于订阅任务【{label}】；更改可能导致保存路径失效或已保存内容重新识别。"

    return ""


def _collect_scraper_subscription_rename_warning(provider: str, old_path: str, new_path: str) -> str:
    return _collect_scraper_subscription_path_warning(provider, [old_path], kind="folder_rename")


def _collect_scraper_action_warning(provider: str, action: Dict[str, Any]) -> str:
    if bool(action.get("is_dir")):
        folder_path = str(action.get("old_path", "") or "").strip()
    else:
        folder_path = str(action.get("target_parent_path", "") or "").strip()
        if not folder_path:
            new_path = normalize_relative_path(str(action.get("new_path", "") or "").strip())
            folder_path = os.path.dirname(new_path).replace("\\", "/") if new_path else ""
    if not folder_path:
        old_path = normalize_relative_path(str(action.get("old_path", "") or "").strip())
        folder_path = os.path.dirname(old_path).replace("\\", "/") if old_path else ""
    return _collect_scraper_subscription_path_warning(provider, [folder_path], kind="folder_rename")


def _expand_selected_scraper_entries(provider: str, cookie: str, selected: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    files: List[Dict[str, Any]] = []
    issues: List[str] = []
    dirs_seen = 0
    for raw in selected:
        item = raw if isinstance(raw, dict) else {}
        entry = _compact_scraper_entry(item, str(item.get("parent_id", "") or "0"), normalize_relative_path(str(item.get("parent_path", "") or "")))
        if not entry:
            continue
        if not entry.get("is_dir"):
            if _is_scraper_excluded_archive(str(entry.get("name", "") or "")):
                continue
            files.append(entry)
            continue
        queue: List[Tuple[str, str, int]] = [(str(entry.get("id", "") or entry.get("cid", "") or "0"), normalize_relative_path(str(entry.get("path", "") or entry.get("name", ""))), 0)]
        while queue and len(files) < SCRAPER_SCAN_MAX_ENTRIES and dirs_seen < SCRAPER_SCAN_MAX_DIRS:
            dir_id, dir_path, depth = queue.pop(0)
            dirs_seen += 1
            try:
                payload = _list_provider_entries_payload(provider, cookie, dir_id, folders_only=False)
            except Exception as exc:
                issues.append(f"读取目录 {dir_path or dir_id} 失败：{exc}")
                continue
            for child in payload.get("entries", []) if isinstance(payload, dict) else []:
                child_entry = _compact_scraper_entry(child, dir_id, dir_path)
                if not child_entry:
                    continue
                if child_entry.get("is_dir"):
                    if depth < 6:
                        queue.append((str(child_entry.get("id") or child_entry.get("cid") or "0"), normalize_relative_path(str(child_entry.get("path", ""))), depth + 1))
                else:
                    if _is_scraper_excluded_archive(str(child_entry.get("name", "") or "")):
                        continue
                    child_entry["parent_path"] = dir_path
                    files.append(child_entry)
                    if len(files) >= SCRAPER_SCAN_MAX_ENTRIES:
                        issues.append(f"已达到首版扫描上限 {SCRAPER_SCAN_MAX_ENTRIES} 个文件，超出部分未纳入计划")
                        break
    return files, issues


def build_scraper_rename_plan(payload: Dict[str, Any]) -> Dict[str, Any]:
    provider = normalize_scraper_provider(payload.get("provider", "115")) or "115"
    cookie = _require_provider_cookie(provider)
    tmdb = payload.get("tmdb") if isinstance(payload.get("tmdb"), dict) else {}
    if max(0, parse_int(tmdb.get("tmdb_id") or tmdb.get("id") or 0, 0)) <= 0:
        raise RuntimeError("请先选择 TMDB 条目")
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    base_cid = str(payload.get("base_cid", "0") or "0").strip() or "0"
    base_path = normalize_relative_path(str(payload.get("base_path", "") or ""))
    selected = _normalize_scraper_selected_entries(payload.get("entries", []) if isinstance(payload.get("entries"), list) else [])
    if not base_path and selected:
        selected_parent_paths = {
            normalize_relative_path(str(item.get("parent_path", "") or "").strip())
            for item in selected
            if isinstance(item, dict) and str(item.get("parent_path", "") or "").strip()
        }
        if len(selected_parent_paths) == 1:
            base_path = next(iter(selected_parent_paths))
    plan_options = dict(options)
    selection_mode = _resolve_scraper_selection_mode(selected, plan_options)
    folder_mode = selection_mode == "folder"
    plan_options["selection_mode"] = selection_mode
    plan_options["base_path"] = base_path
    plan_options["organize_into_media_folder"] = folder_mode
    plan_options["preserve_source_parent_path"] = not folder_mode
    if not folder_mode:
        plan_options["include_tmdb_id"] = False
        plan_options["use_season_subfolder"] = False
        plan_options["rename_selected_folders"] = False
    expanded_files, scan_issues = _expand_selected_scraper_entries(provider, cookie, selected)
    actions: List[Dict[str, Any]] = []
    issues: List[str] = list(scan_issues)
    warnings: List[str] = []
    target_paths: Set[str] = set()
    target_folder_names: Set[str] = set()
    action_index = 1
    if folder_mode and bool(plan_options.get("rename_selected_folders", True)):
        _, _, target_folder_name = _build_scraper_media_titles(tmdb, plan_options, "")
        for raw in selected:
            item = raw if isinstance(raw, dict) else {}
            if not item.get("is_dir"):
                continue
            entry = _compact_scraper_entry(item, str(item.get("parent_id", "") or base_cid), normalize_relative_path(str(item.get("parent_path", "") or "")))
            if not entry:
                continue
            old_parent_id = str(entry.get("parent_id", "") or base_cid).strip() or "0"
            old_name = str(entry.get("name", "") or "")
            old_path = normalize_relative_path(str(entry.get("path", "") or old_name))
            new_name = target_folder_name
            if not new_name or new_name == old_name:
                continue
            action_issue = ""
            if new_name in target_folder_names:
                action_issue = "本批次内目标文件夹重复"
            target_folder_names.add(new_name)
            if _target_name_exists(provider, cookie, old_parent_id, new_name, same_entry_id=str(entry.get("id", "") or "")):
                action_issue = "当前目录中已有同名文件夹"
            action = {
                "action_index": action_index,
                "entry_id": str(entry.get("id", "") or ""),
                "is_dir": True,
                "old_parent_id": old_parent_id,
                "old_name": old_name,
                "old_path": old_path,
                "new_parent_id": old_parent_id,
                "new_name": new_name,
                "new_path": normalize_relative_path(join_relative_path(normalize_relative_path(str(item.get("parent_path", "") or "")), new_name)),
                "target_parent_path": "",
                "issue": action_issue,
                "warning": "",
                "ready": bool(new_name and not action_issue),
            }
            if not action_issue:
                action_warning = _collect_scraper_action_warning(provider, action)
                if action_warning:
                    action["warning"] = action_warning
                    warnings.append(action_warning)
            if action_issue:
                issues.append(f"{old_name or '--'}：{action_issue}")
            actions.append(action)
            action_index += 1
    for entry in expanded_files:
        target_path, issue = _build_scraper_target_path(entry, tmdb, plan_options)
        old_parent_id = str(entry.get("parent_id", "") or base_cid).strip() or "0"
        old_path = normalize_relative_path(str(entry.get("path", "") or entry.get("name", "")))
        action_issue = issue
        target_parent_path = normalize_relative_path(os.path.dirname(target_path).replace("\\", "/")) if target_path else ""
        new_name = os.path.basename(target_path) if target_path else ""
        existing_parent_id = ""
        if target_path:
            if target_path in target_paths:
                action_issue = action_issue or "本批次内目标路径重复"
            target_paths.add(target_path)
            existing_parent_id, exists = _walk_existing_folder(provider, cookie, base_cid, target_parent_path)
            if exists and _target_name_exists(provider, cookie, existing_parent_id, new_name, same_entry_id=str(entry.get("id", "") or "")):
                action_issue = action_issue or "目标目录中已有同名文件"
        action = {
            "action_index": action_index,
            "entry_id": str(entry.get("id", "") or ""),
            "is_dir": False,
            "old_parent_id": old_parent_id,
            "old_name": str(entry.get("name", "") or ""),
            "old_path": old_path,
            "new_parent_id": existing_parent_id,
            "new_name": new_name,
            "new_path": target_path,
            "target_parent_path": target_parent_path,
            "issue": action_issue,
            "warning": "",
            "ready": bool(target_path and not action_issue),
        }
        action_warning = _collect_scraper_action_warning(provider, action)
        if action_warning:
            action["warning"] = action_warning
            warnings.append(action_warning)
        if action_issue:
            issues.append(f"{entry.get('name', '--')}：{action_issue}")
        actions.append(action)
        action_index += 1
    ready_count = sum(1 for item in actions if item.get("ready"))
    return {
        "ok": True,
        "provider": provider,
        "base_cid": base_cid,
        "actions": actions,
        "issues": issues,
        "warnings": unique_preserve_order(warnings),
        "ready": bool(actions) and ready_count == len(actions) and not issues,
        "ready_count": ready_count,
        "total_count": len(actions),
        "tmdb": tmdb,
        "options": plan_options,
    }


def _insert_scraper_job(provider: str, plan: Dict[str, Any], options: Dict[str, Any], tmdb: Dict[str, Any]) -> int:
    ensure_db()
    now = now_text()
    actions = [item for item in plan.get("actions", []) if isinstance(item, dict)]
    conn = open_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO scraper_jobs(
            provider, status, status_detail, total_actions, created_at, updated_at,
            options_json, tmdb_json, plan_json
        ) VALUES (?, 'pending', '等待执行', ?, ?, ?, ?, ?, ?)
        """,
        (
            provider,
            len(actions),
            now,
            now,
            safe_json_dumps(options),
            safe_json_dumps(tmdb),
            safe_json_dumps({"base_cid": plan.get("base_cid", "0"), "actions": actions}),
        ),
    )
    job_id = int(cursor.lastrowid or 0)
    for action in actions:
        cursor.execute(
            """
            INSERT INTO scraper_job_actions(
                job_id, action_index, provider, entry_id, is_dir, old_parent_id, old_name, old_path,
                new_parent_id, new_name, new_path, target_parent_path, status, status_detail,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', ?, ?)
            """,
            (
                job_id,
                max(0, parse_int(action.get("action_index"), 0)),
                provider,
                str(action.get("entry_id", "") or ""),
                1 if action.get("is_dir") else 0,
                str(action.get("old_parent_id", "") or "0"),
                str(action.get("old_name", "") or ""),
                str(action.get("old_path", "") or ""),
                str(action.get("new_parent_id", "") or ""),
                str(action.get("new_name", "") or ""),
                str(action.get("new_path", "") or ""),
                str(action.get("target_parent_path", "") or ""),
                now,
                now,
            ),
        )
    conn.commit()
    conn.close()
    return job_id


def create_scraper_job_from_plan(payload: Dict[str, Any]) -> Dict[str, Any]:
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    provider = normalize_scraper_provider(plan.get("provider") or payload.get("provider", "115")) or "115"
    actions = [item for item in plan.get("actions", []) if isinstance(item, dict)]
    if not actions:
        raise RuntimeError("没有可执行的改名计划")
    blocked = [item for item in actions if item.get("issue") or not item.get("ready")]
    if blocked:
        raise RuntimeError("改名计划仍存在冲突或未识别项，请先处理后再执行")
    options = plan.get("options") if isinstance(plan.get("options"), dict) else {}
    tmdb = plan.get("tmdb") if isinstance(plan.get("tmdb"), dict) else {}
    job_id = _insert_scraper_job(provider, plan, options, tmdb)
    return {"ok": True, "job_id": job_id}


def _serialize_scraper_action_row(row: Any) -> Dict[str, Any]:
    item = sqlite_row_to_dict(row)
    if not item:
        return {}
    return {
        "id": int(item.get("id", 0) or 0),
        "job_id": int(item.get("job_id", 0) or 0),
        "action_index": int(item.get("action_index", 0) or 0),
        "provider": str(item.get("provider", "") or ""),
        "entry_id": str(item.get("entry_id", "") or ""),
        "is_dir": bool(item.get("is_dir", 0)),
        "old_parent_id": str(item.get("old_parent_id", "") or ""),
        "old_name": str(item.get("old_name", "") or ""),
        "old_path": str(item.get("old_path", "") or ""),
        "new_parent_id": str(item.get("new_parent_id", "") or ""),
        "new_name": str(item.get("new_name", "") or ""),
        "new_path": str(item.get("new_path", "") or ""),
        "target_parent_path": str(item.get("target_parent_path", "") or ""),
        "status": str(item.get("status", "") or ""),
        "status_detail": str(item.get("status_detail", "") or ""),
        "rollback_status": str(item.get("rollback_status", "") or ""),
        "rollback_detail": str(item.get("rollback_detail", "") or ""),
        "updated_at": str(item.get("updated_at", "") or ""),
    }


def _serialize_scraper_job_row(row: Any, actions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    item = sqlite_row_to_dict(row)
    if not item:
        return {}
    return {
        "id": int(item.get("id", 0) or 0),
        "provider": str(item.get("provider", "") or ""),
        "status": str(item.get("status", "") or ""),
        "status_detail": str(item.get("status_detail", "") or ""),
        "total_actions": int(item.get("total_actions", 0) or 0),
        "succeeded_actions": int(item.get("succeeded_actions", 0) or 0),
        "failed_actions": int(item.get("failed_actions", 0) or 0),
        "rollback_succeeded_actions": int(item.get("rollback_succeeded_actions", 0) or 0),
        "rollback_failed_actions": int(item.get("rollback_failed_actions", 0) or 0),
        "created_at": str(item.get("created_at", "") or ""),
        "updated_at": str(item.get("updated_at", "") or ""),
        "started_at": str(item.get("started_at", "") or ""),
        "finished_at": str(item.get("finished_at", "") or ""),
        "options": safe_json_loads(item.get("options_json", "{}"), {}),
        "tmdb": safe_json_loads(item.get("tmdb_json", "{}"), {}),
        "can_rollback": int(item.get("succeeded_actions", 0) or 0) > 0 and str(item.get("status", "") or "") in {"completed", "partial", "rollback_failed"},
        "actions": actions or [],
    }


def get_scraper_jobs_state(limit: int = SCRAPER_JOB_LIMIT_DEFAULT, job_id: int = 0) -> Dict[str, Any]:
    ensure_db()
    conn = open_db()
    cursor = conn.cursor()
    if job_id > 0:
        cursor.execute("SELECT * FROM scraper_jobs WHERE id = ?", (int(job_id),))
        rows = cursor.fetchall()
    else:
        cursor.execute(
            "SELECT * FROM scraper_jobs ORDER BY id DESC LIMIT ?",
            (max(1, min(100, int(limit or SCRAPER_JOB_LIMIT_DEFAULT))),),
        )
        rows = cursor.fetchall()
    jobs: List[Dict[str, Any]] = []
    for row in rows:
        row_id = int(row["id"] or 0)
        cursor.execute("SELECT * FROM scraper_job_actions WHERE job_id = ? ORDER BY action_index ASC", (row_id,))
        actions = [_serialize_scraper_action_row(action_row) for action_row in cursor.fetchall()]
        jobs.append(_serialize_scraper_job_row(row, actions))
    cursor.execute("SELECT status, COUNT(1) AS count FROM scraper_jobs GROUP BY status")
    status_counts = {str(row["status"] or ""): int(row["count"] or 0) for row in cursor.fetchall()}
    conn.close()
    counts = {
        "total": sum(status_counts.values()),
        "active": sum(status_counts.get(status, 0) for status in ("pending", "running", "rollback_running")),
        "completed": sum(status_counts.get(status, 0) for status in ("completed", "rolled_back")),
        "failed": sum(status_counts.get(status, 0) for status in ("failed", "partial", "rollback_failed")),
        "rollback": sum(count for status, count in status_counts.items() if status.startswith("rollback") or status == "rolled_back"),
    }
    return {
        "ok": True,
        "jobs": jobs,
        "active_jobs": [item for item in jobs if str(item.get("status", "") or "") in {"pending", "running", "rollback_running"}],
        "job_counts": counts,
    }


def _load_scraper_job(job_id: int) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    ensure_db()
    conn = open_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM scraper_jobs WHERE id = ?", (int(job_id),))
    job = sqlite_row_to_dict(cursor.fetchone())
    if not job:
        conn.close()
        raise RuntimeError("刮削任务不存在")
    cursor.execute("SELECT * FROM scraper_job_actions WHERE job_id = ? ORDER BY action_index ASC", (int(job_id),))
    actions = [sqlite_row_to_dict(row) for row in cursor.fetchall()]
    conn.close()
    return job, actions


def _update_scraper_job(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    ensure_db()
    allowed = {
        "status",
        "status_detail",
        "succeeded_actions",
        "failed_actions",
        "rollback_succeeded_actions",
        "rollback_failed_actions",
        "started_at",
        "finished_at",
    }
    payload = {key: value for key, value in fields.items() if key in allowed}
    if not payload:
        return
    payload["updated_at"] = now_text()
    sets = ", ".join(f"{key} = ?" for key in payload.keys())
    values = list(payload.values()) + [int(job_id)]
    conn = open_db()
    conn.execute(f"UPDATE scraper_jobs SET {sets} WHERE id = ?", values)
    conn.commit()
    conn.close()


def _update_scraper_action(action_id: int, **fields: Any) -> None:
    if not fields:
        return
    allowed = {"new_parent_id", "status", "status_detail", "rollback_status", "rollback_detail", "response_json"}
    payload = {key: value for key, value in fields.items() if key in allowed}
    if not payload:
        return
    payload["updated_at"] = now_text()
    sets = ", ".join(f"{key} = ?" for key in payload.keys())
    values = list(payload.values()) + [int(action_id)]
    conn = open_db()
    conn.execute(f"UPDATE scraper_job_actions SET {sets} WHERE id = ?", values)
    conn.commit()
    conn.close()


def _build_temp_name(action_id: int, entry_id: str, original_name: str) -> str:
    _, ext = os.path.splitext(str(original_name or ""))
    token = re.sub(r"[^A-Za-z0-9]+", "", str(entry_id or ""))[:12] or str(action_id)
    return f".mediahub-tmp-{int(action_id)}-{token}{ext}"


def _execute_move_rename(
    provider: str,
    cookie: str,
    action: Dict[str, Any],
    target_parent_id: str,
    *,
    reverse: bool = False,
) -> Dict[str, Any]:
    entry_id = str(action.get("entry_id", "") or "").strip()
    if not entry_id:
        raise RuntimeError("文件 ID 不能为空")
    if reverse:
        source_parent = str(action.get("new_parent_id", "") or "").strip() or "0"
        source_name = str(action.get("new_name", "") or "")
        target_parent = str(action.get("old_parent_id", "") or "0").strip() or "0"
        target_name = str(action.get("old_name", "") or "")
    else:
        source_parent = str(action.get("old_parent_id", "") or "0").strip() or "0"
        source_name = str(action.get("old_name", "") or "")
        target_parent = target_parent_id
        target_name = str(action.get("new_name", "") or "")
    if not target_name:
        raise RuntimeError("目标文件名为空")
    if _target_name_exists(provider, cookie, target_parent, target_name, same_entry_id=entry_id):
        raise RuntimeError("目标目录中已有同名文件")
    need_move = source_parent != target_parent
    need_rename = source_name != target_name
    responses: List[Dict[str, Any]] = []
    if not need_move and not need_rename:
        return {"skipped": True, "detail": "文件名和目录未变化"}
    if need_move and need_rename:
        temp_name = _build_temp_name(int(action.get("id", 0) or 0), entry_id, source_name)
        responses.append(_rename_provider_entry(provider, cookie, entry_id, temp_name, source_parent))
        responses.append(_move_provider_entries(provider, cookie, [entry_id], target_parent, source_parent))
        responses.append(_rename_provider_entry(provider, cookie, entry_id, target_name, target_parent))
    elif need_rename:
        responses.append(_rename_provider_entry(provider, cookie, entry_id, target_name, source_parent))
    elif need_move:
        responses.append(_move_provider_entries(provider, cookie, [entry_id], target_parent, source_parent))
    _invalidate_provider_parent(provider, source_parent)
    _invalidate_provider_parent(provider, target_parent)
    return {"skipped": False, "responses": responses, "target_parent_id": target_parent}


def run_scraper_job(job_id: int) -> None:
    try:
        job, actions = _load_scraper_job(job_id)
        provider = normalize_scraper_provider(job.get("provider", "115")) or "115"
        cookie = _require_provider_cookie(provider)
        plan = safe_json_loads(job.get("plan_json", "{}"), {})
        base_cid = str(plan.get("base_cid", "0") or "0").strip() or "0"
    except Exception as exc:
        _update_scraper_job(job_id, status="failed", status_detail=str(exc), failed_actions=1, finished_at=now_text())
        return
    _update_scraper_job(job_id, status="running", status_detail="正在执行刮削改名", started_at=now_text(), finished_at="")
    succeeded = 0
    failed = 0
    for action in actions:
        action_id = int(action.get("id", 0) or 0)
        _update_scraper_action(action_id, status="running", status_detail="正在处理")
        try:
            target_parent_path = str(action.get("target_parent_path", "") or "")
            target_parent_id = str(action.get("new_parent_id", "") or "").strip()
            if not target_parent_id:
                target_parent_id = _ensure_folder_from_base(provider, cookie, base_cid, target_parent_path)
                _update_scraper_action(action_id, new_parent_id=target_parent_id)
                action["new_parent_id"] = target_parent_id
            result = _execute_move_rename(provider, cookie, action, target_parent_id)
            status = "skipped" if result.get("skipped") else "completed"
            detail = str(result.get("detail") or "已完成")
            _update_scraper_action(action_id, status=status, status_detail=detail, response_json=safe_json_dumps(result))
            succeeded += 1
            _update_scraper_job(
                job_id,
                status_detail=f"正在执行刮削改名：成功 {succeeded}，失败 {failed}",
                succeeded_actions=succeeded,
                failed_actions=failed,
            )
        except Exception as exc:
            failed += 1
            _update_scraper_action(action_id, status="failed", status_detail=str(exc))
            _update_scraper_job(
                job_id,
                status_detail=f"正在执行刮削改名：成功 {succeeded}，失败 {failed}",
                succeeded_actions=succeeded,
                failed_actions=failed,
            )
    if failed > 0 and succeeded > 0:
        status = "partial"
        detail = f"部分完成：成功 {succeeded}，失败 {failed}"
    elif failed > 0:
        status = "failed"
        detail = f"执行失败：失败 {failed}"
    else:
        status = "completed"
        detail = f"执行完成：{succeeded} 项"
    _update_scraper_job(
        job_id,
        status=status,
        status_detail=detail,
        succeeded_actions=succeeded,
        failed_actions=failed,
        finished_at=now_text(),
    )


def rollback_scraper_job(job_id: int) -> None:
    try:
        job, actions = _load_scraper_job(job_id)
        provider = normalize_scraper_provider(job.get("provider", "115")) or "115"
        cookie = _require_provider_cookie(provider)
    except Exception as exc:
        _update_scraper_job(job_id, status="rollback_failed", status_detail=str(exc), rollback_failed_actions=1, finished_at=now_text())
        return
    successful_actions = [item for item in actions if str(item.get("status", "") or "") in {"completed", "skipped"}]
    _update_scraper_job(job_id, status="rollback_running", status_detail="正在回退刮削任务", finished_at="")
    succeeded = 0
    failed = 0
    for action in reversed(successful_actions):
        action_id = int(action.get("id", 0) or 0)
        try:
            if str(action.get("status", "") or "") == "skipped":
                _update_scraper_action(action_id, rollback_status="skipped", rollback_detail="原动作未产生变化")
                succeeded += 1
                _update_scraper_job(
                    job_id,
                    status_detail=f"正在回退刮削任务：成功 {succeeded}，失败 {failed}",
                    rollback_succeeded_actions=succeeded,
                    rollback_failed_actions=failed,
                )
                continue
            result = _execute_move_rename(
                provider,
                cookie,
                action,
                str(action.get("old_parent_id", "") or "0"),
                reverse=True,
            )
            _update_scraper_action(action_id, rollback_status="completed", rollback_detail="已回退", response_json=safe_json_dumps(result))
            succeeded += 1
            _update_scraper_job(
                job_id,
                status_detail=f"正在回退刮削任务：成功 {succeeded}，失败 {failed}",
                rollback_succeeded_actions=succeeded,
                rollback_failed_actions=failed,
            )
        except Exception as exc:
            failed += 1
            _update_scraper_action(action_id, rollback_status="failed", rollback_detail=str(exc))
            _update_scraper_job(
                job_id,
                status_detail=f"正在回退刮削任务：成功 {succeeded}，失败 {failed}",
                rollback_succeeded_actions=succeeded,
                rollback_failed_actions=failed,
            )
    status = "rolled_back" if failed <= 0 else "rollback_failed"
    detail = f"回退完成：成功 {succeeded}" if failed <= 0 else f"回退部分失败：成功 {succeeded}，失败 {failed}"
    _update_scraper_job(
        job_id,
        status=status,
        status_detail=detail,
        rollback_succeeded_actions=succeeded,
        rollback_failed_actions=failed,
        finished_at=now_text(),
    )
