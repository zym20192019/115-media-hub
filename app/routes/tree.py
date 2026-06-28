import asyncio

from fastapi import APIRouter, Request

from ..background import submit_background
from ..core import *  # noqa: F401,F403
from ..services.tree import run_sync, export_115_tree, query_115_tree_export_status

router = APIRouter()


@router.post("/start")
async def start_sync(request: Request) -> Dict[str, str]:
    data = await request.json()
    if not task_status["running"]:
        submit_background(
            run_sync,
            use_local=data.get("use_local", False),
            force_full=data.get("force_full", False),
            label="tree-manual-sync",
        )
        return {"status": "started"}
    return {"status": "busy"}


@router.post("/export")
async def export_tree(request: Request) -> Dict[str, Any]:
    """
    调用115官方API异步导出目录树。
    
    Request body:
        folder_path: 115中的相对路径 (e.g., "我的影视/电影")
        layer_limit: 目录深度限制 (default: 25)
    
    Returns:
        export_id: 导出任务ID，用于后续查询状态
    """
    try:
        data = await request.json()
        folder_path = str(data.get("folder_path", "")).strip()
        layer_limit = int(data.get("layer_limit", 25))
        
        # Get cookie from config
        cfg = get_config()
        cookie = str(cfg.get("cookie_115", "")).strip()
        
        if not cookie:
            return {"ok": False, "msg": "请先在参数配置中填写 115 Cookie"}
        
        # Call 115 official export API
        result = await asyncio.to_thread(
            export_115_tree,
            cookie,
            folder_path,
            layer_limit,
        )
        
        return result
    except Exception as exc:
        return {"ok": False, "msg": str(exc)}


@router.get("/export/status")
async def export_tree_status(request: Request) -> Dict[str, Any]:
    """
    查询115目录树导出状态（轮询用）。
    
    Query params:
        export_id: 导出任务ID
    
    Returns:
        status: processing / completed / failed / error
    """
    try:
        export_id = request.query_params.get("export_id")
        if not export_id:
            return {"ok": False, "msg": "缺少 export_id", "status": "error"}
        
        cfg = get_config()
        cookie = str(cfg.get("cookie_115", "")).strip()
        
        if not cookie:
            return {"ok": False, "msg": "请先配置 115 Cookie", "status": "error"}
        
        result = await asyncio.to_thread(
            query_115_tree_export_status,
            cookie,
            int(export_id),
        )
        return result
    except Exception as exc:
        return {"ok": False, "msg": str(exc), "status": "error"}


@router.get("/logs")
async def get_logs(request: Request) -> Dict[str, Any]:
    compact = request.query_params.get("compact") == "1"
    return build_main_status_payload(log_limit=UI_STATUS_STREAM_LOG_TAIL_LIMIT if compact else UI_STATUS_LOG_TAIL_LIMIT)


@router.post("/logs/clear")
async def clear_logs(request: Request) -> Dict[str, Any]:
    line = f"{format_log_time(True)} 系统日志已清空"
    task_status["logs"] = [{"text": line, "level": "info"}]
    await asyncio.to_thread(clear_log_file, MAIN_LOG_PATH, line)
    schedule_ui_state_push(0)
    return {"ok": True}
