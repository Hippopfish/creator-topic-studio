"""对话接口。会话增删查 + SSE 流式对话 + 选题驱动采集的队列查询。"""
from __future__ import annotations

from fastapi import APIRouter, Body
from fastapi.responses import StreamingResponse

from core import chat, collect_queue, store

chat_router = APIRouter(prefix="/api/chat", tags=["chat"])


@chat_router.get("/sessions")
def list_sessions() -> dict:
    rows = chat.list_sessions()
    return {"items": [{"id": s["id"], "title": s.get("title") or "新对话",
                       "updatedAt": s.get("updatedAt"),
                       "count": len(s.get("messages") or [])} for s in rows]}


@chat_router.post("/sessions")
def create_session(payload: dict = Body(default={})) -> dict:
    return chat.new_session(str(payload.get("title") or "新对话"))


@chat_router.get("/sessions/{sid}")
def get_session(sid: str) -> dict:
    s = chat.get_session(sid)
    if not s:
        return {"ok": False, "message": "会话不存在"}
    return s


@chat_router.delete("/sessions/{sid}")
def delete_session(sid: str) -> dict:
    return {"ok": chat.delete_session(sid)}


@chat_router.get("/rules")
def rules() -> dict:
    """自检用：选题规则能不能切出来、切得干不干净。前端不调这个。"""
    r = chat.topic_rules()
    pool = chat.tool_search_material({"keyword": "", "limit": 20})
    items = pool.get("items") or []
    likes = [i["like"] for i in items if isinstance(i.get("like"), int)]
    dates = [i.get("publish") or "" for i in items]
    by_publish = all(dates[i] >= dates[i + 1] for i in range(len(dates) - 1)) if len(dates) > 1 else True

    all_likes = [(x.get("metrics") or {}).get("like") for x in chat._all_contents()]
    all_likes = [x for x in all_likes if isinstance(x, int)]
    return {
        "ok": bool(r),
        "chars": len(r),
        "placeholders": r.count("{{"),
        "hasGate": "硬门槛" in r,
        "hasMemo": "研究小抄" in r,
        "hasTitleRules": "标题硬规则" in r,
        "hasDedupe": "同批去重" in r,
        "tools": [t["function"]["name"] for t in chat.TOOLS],
        "pool": {"total": pool.get("库内总数"),
                 "recentMinLike": min(likes) if likes else None,
                 "minLikeInPool": min(all_likes) if all_likes else None,
                 "lowLikeCount": len([x for x in all_likes if x < 100]),
                 "sortByPublish": by_publish},
    }


@chat_router.post("/stream")
def stream(payload: dict = Body(...)):
    sid = str(payload.get("sessionId") or "").strip()
    text = str(payload.get("text") or "").strip()
    if not text:
        return {"ok": False, "message": "空消息"}
    if not sid:
        sid = chat.new_session(text[:24])["id"]

    # 选题台传来的本轮选区。只收认识的字段，别把整包 payload 塞进上下文。
    raw = payload.get("ctx") or {}

    def _ints(v):
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return 0

    ctx = {
        "accountId": str(raw.get("accountId") or ""),
        "contentKeys": [str(k) for k in (raw.get("contentKeys") or []) if k][:10],
        "seed": str(raw.get("seed") or "").strip()[:200],
        "count": _ints(raw.get("count")),
        "candidateRange": str(raw.get("candidateRange") or ""),
        "seedBrief": {k: str(v).strip()[:200] for k, v in (raw.get("seedBrief") or {}).items()
                      if v and str(v).strip()},
    }

    def gen():
        yield from chat.stream_chat(sid, text, ctx)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------------ 采集队列
# 对话里提交的采集是后台排队跑的，界面靠这几个接口看进度、取消、重试。

@chat_router.get("/collect")
def collect_status(sessionId: str = "") -> dict:
    s = collect_queue.summary(sessionId)
    sess = chat.get_session(sessionId) if sessionId else None
    return {
        **s,
        "notices": (sess or {}).get("notices") or [] if sessionId else [],
        "queueBusy": collect_queue.pending() != [],
    }


@chat_router.post("/notices/seen")
def notices_seen(payload: dict = Body(default={})) -> dict:
    sid = str(payload.get("sessionId") or "").strip()
    return {"ok": chat.mark_notices_seen(sid)}


@chat_router.get("/notices/pending")
def notices_pending() -> dict:
    """导航红点：哪些会话有没看过的采集结果。"""
    items = chat.pending_notices()
    return {"items": items, "count": sum(x.get("count") or 0 for x in items)}


@chat_router.get("/collect/all")
def collect_all() -> dict:
    """采集台/设置里看全局队列用。"""
    rows = collect_queue.all_items()
    live = [r for r in rows if r.get("status") in ("queued", "running")]
    return {"items": rows[-30:], "pending": live,
            "worker": collect_queue.worker_alive()}


@chat_router.post("/collect/{qid}/cancel")
def collect_cancel(qid: str) -> dict:
    return collect_queue.cancel(qid)


@chat_router.post("/collect/{qid}/retry")
def collect_retry(qid: str) -> dict:
    return collect_queue.retry(qid)


@chat_router.delete("/collect/{qid}")
def collect_delete(qid: str) -> dict:
    """删掉一条队列记录（不是取消，是从历史里清掉）。"""
    return {"ok": store.remove_item("collectQueue", qid, note="delete collect record")}


@chat_router.post("/collect/run-now")
def collect_run_now() -> dict:
    """自检/调试用：不等 8 秒，手动催一次调度。"""
    return collect_queue.tick()
