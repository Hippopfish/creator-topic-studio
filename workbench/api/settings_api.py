"""设置、密钥、账号档案接口。"""
from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Body, HTTPException

from core import baseline, store
from core import ai

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ settings

@router.get("/settings")
def get_settings() -> dict:
    s = store.settings()
    key = store.secret()
    s["ai"]["apiKeyMasked"] = store.mask_key(key)
    s["ai"]["apiKeySet"] = bool(key)
    s["ai"]["modelPresets"] = ai.MODEL_PRESETS
    s["ai"]["modelKnown"] = s["ai"].get("model") in ai.MODEL_NAMES
    return s


@router.put("/settings")
def put_settings(patch: dict = Body(...)) -> dict:
    store.save_settings(patch)
    return get_settings()


@router.put("/settings/secret")
def put_secret(payload: dict = Body(...)) -> dict:
    key = str(payload.get("apiKey") or "").strip()
    store.save_secret(key)
    return {"ok": True, "apiKeyMasked": store.mask_key(key), "apiKeySet": bool(key)}


@router.post("/settings/test-ai")
def test_ai() -> dict:
    s = store.settings()["ai"]
    key = store.secret()
    if not key:
        raise HTTPException(409, "NO_API_KEY")

    base = str(s.get("baseUrl") or "").rstrip("/")
    if not base or not str(s.get("model") or "").strip():
        return {"ok": False, "latencyMs": 0, "model": s.get("model") or "",
                "message": "先把 Base URL 和模型填上再测。"}
    probe = {
        "model": s["model"],
        "messages": [{"role": "user", "content": "回复两个字：正常"}],
        "max_tokens": 64,
    }
    # 测连通不测推理：关掉思考模式，否则 16 个 token 会被推理过程吃光
    if "deepseek" in base.lower():
        probe["thinking"] = {"type": "disabled"}
    t0 = time.time()
    try:
        with httpx.Client(timeout=30) as c:
            r = c.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=probe,
            )
        ms = int((time.time() - t0) * 1000)
        if r.status_code >= 400:
            return {
                "ok": False,
                "latencyMs": ms,
                "model": s["model"],
                "message": f"HTTP {r.status_code}：{r.text[:200]}",
            }
        body = r.json()
        reply = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return {
            "ok": True,
            "latencyMs": ms,
            "model": body.get("model") or s["model"],
            "message": f"模型回复：{reply.strip()[:40]}",
        }
    except Exception as e:
        return {
            "ok": False,
            "latencyMs": int((time.time() - t0) * 1000),
            "model": s["model"],
            "message": f"{type(e).__name__}: {e}",
        }


# ------------------------------------------------------------------ accounts

DEFAULT_ACCOUNT = {
    "platform": "xhs",
    "displayName": "",
    "homepageUrl": "",
    "positioning": "",
    "audience": "",
    "contentPillars": [],
    "boundaries": {"forbiddenTopics": [], "bannedTone": [], "mustNotDrift": []},
    "businessGoal": {"type": "涨粉", "detail": ""},
    "cadence": "每周 3 条",
    "enabled": True,
}


@router.get("/accounts")
def list_accounts() -> dict:
    return {"items": store.list_items("account")}


@router.post("/accounts")
def create_account(payload: dict = Body(...)) -> dict:
    item = {**DEFAULT_ACCOUNT, **payload}
    item["id"] = store.new_id("acc")
    item["createdAt"] = store.now_iso()
    item["updatedAt"] = item["createdAt"]
    store.upsert_item("account", item, note="create account")
    return item


@router.put("/accounts/{acc_id}")
def update_account(acc_id: str, payload: dict = Body(...)) -> dict:
    found = next((a for a in store.list_items("account") if a.get("id") == acc_id), None)
    if not found:
        raise HTTPException(404, "ACCOUNT_NOT_FOUND")
    item = {**found, **payload, "id": acc_id, "updatedAt": store.now_iso()}
    store.upsert_item("account", item, note="update account")
    return item


@router.delete("/accounts/{acc_id}")
def delete_account(acc_id: str) -> dict:
    return {"ok": store.remove_item("account", acc_id, note="delete account")}


@router.get("/accounts/{acc_id}/baseline")
def account_baseline(acc_id: str, n: int = 20) -> dict:
    """基线 = 该账号最近约 20 条的同项中位数（原版 content-review:23）。

    统一走 core.baseline，和复盘台用的是同一份算法，不再各算各的。
    """
    b = baseline.compute(store.list_items("reviews"), acc_id, n=n)
    b["computedAt"] = store.now_iso()
    return b
