import asyncio

from fastapi import APIRouter, Request

from ..background import submit_background
from ..core import *  # noqa: F401,F403
from ..services.tree import run_sync, generate_115_tree_txt

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


@router.post("/generate")
async def generate_tree(request: Request) -> Dict[str, Any]:
    """
    Generate a new 115 directory tree TXT file.
    
    Request body:
        folder_path: Relative path in 115 (e.g., "我的影视/电影")
        output_path: Local path where tree.txt will be saved
        max_depth: Maximum directory depth (default: 25)
    
    Returns:
        Statistics about the generated tree
    """
    try:
        data = await request.json()
        folder_path = str(data.get("folder_path", "")).strip()
        output_path = str(data.get("output_path", "")).strip()
        max_depth = int(data.get("max_depth", 25))
        
        if not output_path:
            return {"ok": False, "msg": "output_path 不能为空"}
        
        # Get cookie from config
        cfg = get_config()
        cookie = str(cfg.get("cookie_115", "")).strip()
        
        if not cookie:
            return {"ok": False, "msg": "请先在参数配置中填写 115 Cookie"}
        
        # Run tree generation in thread pool
        stats = await asyncio.to_thread(
            generate_115_tree_txt,
            cookie,
            folder_path,
            output_path,
            max_depth,
        )
        
        return {
            "ok": True,
            "msg": "目录树生成成功",
            "output_path": output_path,
            "stats": stats,
        }
    except Exception as exc:
        return {"ok": False, "msg": str(exc)}


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
