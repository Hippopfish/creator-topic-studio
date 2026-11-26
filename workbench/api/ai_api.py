"""AI 双模接口：prepared / run / ingest。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Body

from core import ai, paths, prompts, store

ai_router = APIRouter(prefix="/api/ai")


@ai_router.post("/prepare")
def prepare(payload: dict = Body(...)) -> dict:
    return ai.prepare(payload.get("task") or "", payload.get("ctx") or {})


@ai_router.post("/run")
def run(payload: dict = Body(...)) -> dict:
    return ai.run(payload.get("task") or "", payload.get("ctx") or {})


@ai_router.post("/ingest")
def ingest(payload: dict = Body(...)) -> dict:
    return ai.ingest(
        task=payload.get("task") or "",
        raw_text=payload.get("rawText") or "",
        target_id=payload.get("targetId") or "",
        account_id=payload.get("accountId") or "",
    )


@ai_router.get("/tasks")
def tasks() -> dict:
    return {
        "items": [
            {"task": k, "name": v, "template": f"{k}.md",
             "exists": (paths.PROMPTS_DIR / f"{k}.md").exists()}
            for k, v in ai.TASK_NAMES.items()
        ],
        "available": prompts.available(),
    }


@ai_router.get("/usage")
def usage() -> dict:
    p = paths.LOGS_DIR / "ai-usage.ndjson"
    if not p.exists():
        return {"calls": 0, "tokens": 0, "failures": 0}
    calls = tokens = failures = 0
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            calls += 1
            tokens += int(r.get("tokens") or 0)
            if not r.get("ok"):
                failures += 1
    except Exception:
        pass
    return {"calls": calls, "tokens": tokens, "failures": failures}
