"""后台数据接口：同步创作者中心、读账号级日序列与逐篇快照。"""
from __future__ import annotations

from fastapi import APIRouter, Body

from core import datacenter

stats_router = APIRouter(prefix="/api/stats")


@stats_router.get("/status")
def stats_status() -> dict:
    return datacenter.status()


@stats_router.post("/sync")
def stats_sync(payload: dict = Body(default={})) -> dict:
    return datacenter.sync(reason=str(payload.get("reason") or "manual"))


@stats_router.get("/daily")
def stats_daily(days: int = 30) -> dict:
    return {"items": datacenter.daily(days)}


@stats_router.get("/summary")
def stats_summary() -> dict:
    return datacenter.summary()


@stats_router.get("/posts")
def stats_posts(key: str = "") -> dict:
    return {"items": datacenter.snapshots(key)}
