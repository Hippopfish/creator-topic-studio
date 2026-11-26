"""档案台接口：采自己的账号、拆解、读写三份产物。"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from core import profile

profile_router = APIRouter(prefix="/api/profile")


@profile_router.get("/status")
def get_status() -> dict:
    return profile.status()


@profile_router.get("")
def get_product() -> dict:
    return profile.product()


@profile_router.post("/crawl")
def crawl(payload: dict = Body(...)) -> dict:
    res = profile.crawl_own(
        homepage_url=str(payload.get("homepageUrl") or ""),
        max_notes=int(payload.get("maxNotes") or 20),
    )
    if not res.get("ok"):
        code = 409 if res.get("code") == "CONFLICT_RUNNING" else 400
        raise HTTPException(code, res.get("message") or "启动失败")
    return res


@profile_router.post("/build")
def build(payload: dict = Body(default={})) -> dict:
    res = profile.start_build(
        homepage_url=str(payload.get("homepageUrl") or ""),
        limit=int(payload.get("limit") or 12),
        mode=str(payload.get("mode") or "init"),
    )
    if not res.get("ok"):
        code = 409 if res.get("code") in ("BUSY",) else 400
        raise HTTPException(code, res.get("message") or "启动失败")
    return res


@profile_router.post("/learn")
def learn(payload: dict = Body(default={})) -> dict:
    res = profile.start_build(
        homepage_url=str(payload.get("homepageUrl") or ""),
        limit=int(payload.get("limit") or 12),
        mode="relearn",
    )
    if not res.get("ok"):
        code = 409 if res.get("code") in ("BUSY",) else 400
        raise HTTPException(code, res.get("message") or "启动失败")
    return res


@profile_router.post("/correct")
def correct(payload: dict = Body(...)) -> dict:
    res = profile.correct_sample(
        type_id=str(payload.get("typeId") or ""),
        sample_index=int(payload.get("sampleIndex") or 0),
        note=str(payload.get("note") or ""),
    )
    if not res.get("ok"):
        raise HTTPException(400, res.get("message") or "记录失败")
    return res


@profile_router.put("/creator")
def put_creator(payload: dict = Body(...)) -> dict:
    return profile.update_creator(
        markdown=str(payload.get("markdown") or ""),
        fields=payload.get("fields") if isinstance(payload.get("fields"), dict) else None,
    )


@profile_router.put("/memory")
def put_memory(payload: dict = Body(...)) -> dict:
    res = profile.update_memory(
        item_id=str(payload.get("id") or ""),
        action=str(payload.get("action") or "confirm"),
        content=str(payload.get("content") or ""),
    )
    if not res.get("ok"):
        raise HTTPException(404, res.get("message") or "没有这条记忆")
    return res


@profile_router.post("/forget")
def forget(payload: dict = Body(default={})) -> dict:
    return profile.forget_own(str(payload.get("platform") or "xhs"))
