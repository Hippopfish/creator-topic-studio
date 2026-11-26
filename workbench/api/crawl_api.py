"""采集台接口：浏览器管理、任务启动/停止/日志流、批次导入。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse

from core import browser, crawl_guard, importer, jobs, paths, store

crawl_router = APIRouter(prefix="/api/crawl")


# ------------------------------------------------------------------ 浏览器

@crawl_router.get("/browser/status")
def browser_status(platform: str = "xhs", role: str = "crawl") -> dict:
    return browser.status(platform, role)


@crawl_router.post("/browser/launch")
def browser_launch(payload: dict = Body(default={})) -> dict:
    return browser.launch(payload.get("platform", "xhs"),
                          role=str(payload.get("role") or "crawl"))


@crawl_router.post("/browser/close")
def browser_close(payload: dict = Body(default={})) -> dict:
    return browser.close(str(payload.get("role") or "crawl"))


@crawl_router.get("/preflight")
def preflight(platform: str = "xhs", role: str = "crawl") -> dict:
    s = store.settings()["crawler"]
    chrome_ok = bool(s.get("chromePath")) and Path(str(s["chromePath"])).exists()
    port = browser.port_for(role)
    cdp_ok = browser.cdp_alive(port)
    venv_ok = paths.VENV_PY.exists()
    main_ok = paths.CRAWLER_MAIN.exists()

    warnings = []
    if not chrome_ok:
        warnings.append("找不到 Chrome，请在设置里填正确路径")
    if not venv_ok:
        warnings.append("MediaCrawler 的 Python 环境缺失")
    if not main_ok:
        warnings.append("找不到 MediaCrawler/main.py")
    if chrome_ok and not cdp_ok:
        warnings.append(f"「{paths.ROLE_NAMES.get(role, role)}」的专用浏览器未启动"
                        f"（{port} 端口，采集前必须先启动并确认已登录）")

    return {
        "platform": platform,
        "role": role,
        "roleName": paths.ROLE_NAMES.get(role, role),
        "port": port,
        "profileDir": str(paths.profile_dir(platform, role)),
        "chromeOk": chrome_ok,
        "cdpOk": cdp_ok,
        "venvOk": venv_ok,
        "mainOk": main_ok,
        "missingChromium": True,
        "warnings": warnings,
        "canStart": chrome_ok and cdp_ok and venv_ok and main_ok,
    }


# ------------------------------------------------------------------ 任务

@crawl_router.post("/jobs")
def create_job(payload: dict = Body(...)) -> dict:
    platform = payload.get("platform", "xhs")
    keywords = payload.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k for k in keywords.replace("，", ",").split(",")]
    res = jobs.start(
        platform=platform,
        keywords=keywords,
        max_notes=int(payload.get("maxNotes") or 20),
        with_comments=bool(payload.get("withComments", True)),
        with_sub_comments=bool(payload.get("withSubComments", False)),
        sort_type=str(payload.get("sortType") or ""),
        crawler_type=str(payload.get("crawlerType") or "search"),
        creator_id=str(payload.get("creatorId") or ""),
        note_ids=payload.get("noteIds") or [],
    )
    if not res.get("ok"):
        code = 409 if res.get("code") == "CONFLICT_RUNNING" else (
            429 if str(res.get("code") or "").startswith("GUARD_") else 400)
        raise HTTPException(code, res.get("message") or "启动失败")
    return res


# ------------------------------------------------------------------ 护栏
# 组间冷却 / 日限额 / 风控刹车。状态见 core/crawl_guard.py 顶部注释。

@crawl_router.get("/guard")
def guard_status() -> dict:
    return crawl_guard.snapshot()


@crawl_router.post("/guard/reset")
def guard_reset(payload: dict = Body(default={})) -> dict:
    return crawl_guard.reset(str(payload.get("what") or "risk"))


@crawl_router.get("/jobs")
def list_jobs(limit: int = 20) -> dict:
    return {"items": jobs.list_jobs(limit), "current": jobs.current()}


@crawl_router.get("/jobs/{job_id}")
def job_detail(job_id: str) -> dict:
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "任务不存在")
    return j


@crawl_router.post("/jobs/{job_id}/stop")
def stop_job(job_id: str) -> dict:
    return jobs.stop(job_id)


@crawl_router.post("/jobs/stop-current")
def stop_current() -> dict:
    return jobs.stop_current()


@crawl_router.get("/jobs/{job_id}/logs")
def job_logs(job_id: str, after: int = 0) -> dict:
    return jobs.logs(job_id, after)


@crawl_router.get("/jobs/{job_id}/stream")
async def job_stream(job_id: str, after: int = 0):
    return StreamingResponse(
        jobs.stream(job_id, after),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ------------------------------------------------------------------ 批次

@crawl_router.get("/batches")
def list_batches() -> dict:
    items = sorted(store.list_items("batches"),
                   key=lambda b: b.get("startedAt") or "", reverse=True)
    for b in items:
        files = importer.scan_raw(b)
        b["rawFileCount"] = len(files["contents"]) + len(files["comments"])
    return {"items": items}


@crawl_router.post("/batches/{batch_id}/import")
def import_batch(batch_id: str, payload: dict = Body(default={})) -> dict:
    res = importer.import_batch(batch_id, replace=bool(payload.get("replace")))
    if not res.get("ok"):
        raise HTTPException(400, res.get("message") or "导入失败")
    return res


@crawl_router.delete("/batches/{batch_id}")
def delete_batch(batch_id: str) -> dict:
    return {"ok": store.remove_item("batches", batch_id, note="delete batch")}


@crawl_router.post("/cleanup-orphans")
def cleanup_orphans() -> dict:
    return jobs.cleanup_orphans()
