"""MBTI 自媒体工作台 · 服务入口。

用法：
    pythonw.exe app.py --open     # 无窗口启动并打开浏览器（桌面快捷方式走这条）
    python.exe  app.py            # 调试：可见控制台
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# 允许 `python app.py` 直接运行（把 workbench 加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core import paths, store

APP_NAME = "mbti-workbench"
VERSION = "0.1.0"
PORT_RANGE = range(8737, 8748)

paths.ensure_dirs()

app = FastAPI(title=APP_NAME, version=VERSION, docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(127\.0\.0\.1|localhost)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)

_SERVER: dict = {"inst": None}
_STARTED_AT = store.now_iso()

# ----------------------------------------------------------------- 路由注册

from api.settings_api import router as settings_router  # noqa: E402

app.include_router(settings_router)

for _mod, _name in (("crawl_api", "crawl_router"), ("content_api", "content_router"),
                    ("studio_api", "studio_router"), ("ai_api", "ai_router"),
                    ("profile_api", "profile_router"), ("stats_api", "stats_router"),
                    ("chat_api", "chat_router")):
    try:
        _m = __import__(f"api.{_mod}", fromlist=[_name])
        app.include_router(getattr(_m, _name))
    except ModuleNotFoundError:
        pass  # 分阶段落地，模块没写好时不影响启动


# ----------------------------------------------------------------- 系统接口

@app.get("/api/health")
def health() -> dict:
    from core import jobs

    return {
        "ok": True,
        "app": APP_NAME,
        "version": VERSION,
        "pid": os.getpid(),
        "port": _current_port(),
        "startedAt": _STARTED_AT,
        "crawlerRunning": jobs.is_running(),
    }


@app.get("/api/paths")
def get_paths() -> dict:
    return {
        "base": str(paths.BASE),
        "data": str(paths.DATA_DIR),
        "raw": str(paths.RAW_DIR),
        "logs": str(paths.LOGS_DIR),
        "prompts": str(paths.PROMPTS_DIR),
        "crawler": str(paths.CRAWLER_DIR),
        "profiles": {k: str(v) for k, v in paths.PROFILE_DIRS.items()},
    }


@app.post("/api/open-folder")
def open_folder(payload: dict | None = None) -> dict:
    which = (payload or {}).get("which", "data")
    target = {
        "data": paths.DATA_DIR,
        "raw": paths.RAW_DIR,
        "logs": paths.LOGS_DIR,
        "prompts": paths.PROMPTS_DIR,
        "base": paths.BASE,
    }.get(which)
    if not target:
        return JSONResponse({"ok": False, "message": "未知目录"}, status_code=400)
    try:
        os.startfile(str(target))  # type: ignore[attr-defined]
        return {"ok": True, "path": str(target)}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@app.post("/api/shutdown")
def shutdown() -> dict:
    threading.Timer(0.4, _do_shutdown).start()
    return {"ok": True}


def _do_shutdown() -> None:
    inst = _SERVER.get("inst")
    if inst is not None:
        inst.should_exit = True
    else:
        os._exit(0)


# ----------------------------------------------------------------- 静态资源

app.mount("/static", StaticFiles(directory=str(paths.WEB_DIR)), name="static")

_ASSET_RE = re.compile(r'(/static/[^"\'?\s]+)(["\'])')


def _static_version() -> str:
    """按 web 目录里最新的文件修改时间生成版本号，改动代码后自动破缓存。"""
    latest = 0.0
    for p in paths.WEB_DIR.rglob("*"):
        if p.is_file():
            latest = max(latest, p.stat().st_mtime)
    return str(int(latest))


@app.get("/")
def index() -> HTMLResponse:
    """首页：给静态资源打上版本号，并禁止缓存 HTML 本身。

    FastAPI 默认不给 Cache-Control，浏览器会按启发式缓存 JS/CSS，
    导致改完代码刷新后仍是旧界面。版本号 + no-store 一起用可彻底避免。
    """
    html = (paths.WEB_DIR / "index.html").read_text(encoding="utf-8")
    ver = _static_version()
    html = _ASSET_RE.sub(lambda m: f"{m.group(1)}?v={ver}{m.group(2)}", html)
    return HTMLResponse(html, headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/favicon.ico")
def favicon():
    ico = paths.WEB_DIR / "icon.ico"
    if ico.exists():
        return FileResponse(str(ico))
    return JSONResponse({}, status_code=204)


# ----------------------------------------------------------------- 启动逻辑

def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _probe(port: int) -> bool:
    """判断该端口上跑的是不是本工作台（避免误认别的程序）。"""
    try:
        import httpx

        r = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=0.8)
        return r.status_code == 200 and r.json().get("app") == APP_NAME
    except Exception:
        return False


def _current_port() -> int:
    try:
        return int(json.loads(paths.SERVER_FILE.read_text(encoding="utf-8")).get("port") or 0)
    except Exception:
        return 0


def _write_server(port: int) -> None:
    """写到 workbench/runtime/server.json（不是 data/ 下），关闭脚本按这个路径读。"""
    try:
        paths.SERVER_FILE.parent.mkdir(parents=True, exist_ok=True)
        paths.SERVER_FILE.write_text(json.dumps({
            "schemaVersion": 1, "pid": os.getpid(),
            "port": port, "startedAt": _STARTED_AT,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()

    # 1) 已有实例在跑：只开浏览器，不起第二个进程
    for p in (args.port, *PORT_RANGE):
        if p and not _port_free(p) and _probe(p):
            _write_server(p)
            if args.open:
                webbrowser.open(f"http://127.0.0.1:{p}/")
            return

    # 2) 找一个空闲端口
    port = next((p for p in PORT_RANGE if _port_free(p)), 8737)
    _write_server(port)

    if args.open:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()

    print(f"[{APP_NAME}] http://127.0.0.1:{port}/  (pid={os.getpid()})", flush=True)

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port,
        log_level="warning", access_log=False, log_config=None,
    )
    server = uvicorn.Server(config)
    _SERVER["inst"] = server

    # 启动时清理上次遗留的采集进程
    try:
        from core import jobs

        jobs.cleanup_orphans()
    except Exception:
        pass

    # 选题驱动采集的调度线程：对话里提交的关键词靠它排队起任务、采完自动入库
    try:
        from core import collect_queue

        collect_queue.start_worker()
    except Exception as e:
        print(f"[collect-queue] worker 没起来：{e}", flush=True)

    server.run()


if __name__ == "__main__":
    main()
