import asyncio
import os
import re
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from ..core import *  # noqa: F401,F403

router = APIRouter()

_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_LOCKOUT_SECONDS = 300
_login_attempts: Dict[str, List[float]] = {}
_login_attempts_lock = threading.Lock()
_TEMPLATE_INCLUDE_RE = re.compile(r"{%\s*include\s+[\"']([^\"']+)[\"']\s*%}")
_ASSET_VERSION_CACHE_SECONDS = max(1, int(os.environ.get("ASSET_VERSION_CACHE_SECONDS", 30) or 30))
_TEMPLATE_CACHE_SECONDS = max(0, int(os.environ.get("TEMPLATE_CACHE_SECONDS", 30) or 30))
_CACHE_LOCK = threading.RLock()
_ASSET_VERSION_CACHE: Tuple[float, str] = (0.0, "")
_TEMPLATE_CACHE: Dict[str, Tuple[float, str]] = {}


def prune_page_runtime_caches() -> Dict[str, int]:
    now = time.time()
    removed_login_attempts = 0
    with _login_attempts_lock:
        for client_ip, attempts in list(_login_attempts.items()):
            fresh_attempts = [ts for ts in attempts if now - float(ts or 0.0) < _LOGIN_LOCKOUT_SECONDS]
            if fresh_attempts:
                if len(fresh_attempts) != len(attempts):
                    _login_attempts[client_ip] = fresh_attempts
                    removed_login_attempts += len(attempts) - len(fresh_attempts)
                continue
            _login_attempts.pop(client_ip, None)
            removed_login_attempts += len(attempts)

    removed_templates = 0
    if _TEMPLATE_CACHE_SECONDS > 0:
        now_mono = time.monotonic()
        with _CACHE_LOCK:
            for name, (cached_at, _content) in list(_TEMPLATE_CACHE.items()):
                if now_mono - float(cached_at or 0.0) < _TEMPLATE_CACHE_SECONDS:
                    continue
                _TEMPLATE_CACHE.pop(name, None)
                removed_templates += 1
    return {"login_attempts": removed_login_attempts, "templates": removed_templates}


def _get_asset_version() -> str:
    global _ASSET_VERSION_CACHE
    now = time.monotonic()
    with _CACHE_LOCK:
        cached_at, cached_value = _ASSET_VERSION_CACHE
        if cached_value and (now - cached_at) < _ASSET_VERSION_CACHE_SECONDS:
            return cached_value
    try:
        version_info = load_local_version()
        newest_mtime = 0
        for asset_dir in ("js", "css"):
            for root, _, files in os.walk(os.path.join(STATIC_DIR, asset_dir)):
                for file_name in files:
                    if not file_name.endswith((".js", ".css")):
                        continue
                    try:
                        newest_mtime = max(newest_mtime, int(os.path.getmtime(os.path.join(root, file_name))))
                    except OSError:
                        pass
        raw_asset_version = (
            f"{version_info.get('version', 'dev')}-"
            f"{version_info.get('buildDate', '')}-"
            f"{newest_mtime}"
        )
        asset_version = re.sub(r"[^0-9A-Za-z_.-]+", "-", raw_asset_version).strip("-") or "dev"
    except Exception:
        asset_version = "dev"
    with _CACHE_LOCK:
        _ASSET_VERSION_CACHE = (now, asset_version)
    return asset_version


def _read_template(name: str, seen: Optional[Set[str]] = None) -> str:
    normalized_name = os.path.normpath(str(name or "").strip())
    if not normalized_name or normalized_name.startswith("..") or os.path.isabs(normalized_name):
        raise RuntimeError("模板路径不合法")
    if seen is None and _TEMPLATE_CACHE_SECONDS > 0:
        now = time.monotonic()
        with _CACHE_LOCK:
            cached = _TEMPLATE_CACHE.get(normalized_name)
            if cached and (now - cached[0]) < _TEMPLATE_CACHE_SECONDS:
                return cached[1]

    active_seen = set(seen or set())
    if normalized_name in active_seen:
        raise RuntimeError(f"模板 include 循环：{normalized_name}")
    active_seen.add(normalized_name)
    with open(os.path.join(TEMPLATE_DIR, normalized_name), "r", encoding="utf-8") as f:
        content = f.read()

    def replace_include(match: re.Match[str]) -> str:
        return _read_template(match.group(1), active_seen)

    rendered = _TEMPLATE_INCLUDE_RE.sub(replace_include, content)
    if "{{ asset_version }}" in rendered:
        rendered = rendered.replace("{{ asset_version }}", _get_asset_version())
    if seen is None and _TEMPLATE_CACHE_SECONDS > 0:
        with _CACHE_LOCK:
            _TEMPLATE_CACHE[normalized_name] = (time.monotonic(), rendered)
    return rendered


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> str:
    return await asyncio.to_thread(_read_template, "login.html")


@router.post("/login")
async def do_login(request: Request) -> JSONResponse:
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    with _login_attempts_lock:
        attempts = _login_attempts.get(client_ip, [])
        attempts = [t for t in attempts if now - t < _LOGIN_LOCKOUT_SECONDS]
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            return JSONResponse(status_code=429, content={"ok": False, "msg": "登录失败次数过多，请5分钟后重试"})

    data = await request.json()
    cfg = get_config()
    username_ok = hmac.compare_digest(str(data.get("username", "")), str(cfg.get("username", "")))
    password_ok = verify_password(str(data.get("password", "")), str(cfg.get("password", "")))
    if username_ok and password_ok:
        with _login_attempts_lock:
            _login_attempts.pop(client_ip, None)
        request.session["logged_in"] = True
        return JSONResponse(content={"ok": True})

    with _login_attempts_lock:
        _login_attempts.setdefault(client_ip, []).append(now)
    return JSONResponse(status_code=401, content={"ok": False, "msg": "密码错误"})


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    if not request.session.get("logged_in"):
        return RedirectResponse(url="/login")
    return HTMLResponse(await asyncio.to_thread(_read_template, "index.html"))


@router.get("/favicon.ico", include_in_schema=False)
async def favicon_ico() -> FileResponse:
    return FileResponse(FAVICON_PATH, media_type="image/svg+xml")


@router.api_route("/download/userscript/magnet-helper.user.js", methods=["GET", "HEAD"], include_in_schema=False)
async def download_magnet_userscript(request: Request):
    return RedirectResponse(url="/userscript/magnet-helper.user.js", status_code=307)


@router.api_route("/userscript/magnet-helper.user.js", methods=["GET", "HEAD"], include_in_schema=False)
async def install_magnet_userscript(request: Request):
    if not os.path.exists(USERSCRIPT_MAGNET_HELPER_PATH):
        return JSONResponse(status_code=404, content={"ok": False, "msg": "脚本文件不存在"})
    headers = {
        "Cache-Control": "no-store",
    }
    if request.method == "HEAD":
        return Response(status_code=200, media_type="application/javascript; charset=utf-8", headers=headers)

    def _read_userscript() -> str:
        with open(USERSCRIPT_MAGNET_HELPER_PATH, "r", encoding="utf-8") as f:
            return f.read()

    script_text = await asyncio.to_thread(_read_userscript)
    return Response(
        content=script_text,
        media_type="application/javascript; charset=utf-8",
        headers=headers,
    )


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    request.session.clear()
    return JSONResponse(content={"ok": True, "redirect": "/login"})


@router.get("/logout")
async def logout_compat(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login")
