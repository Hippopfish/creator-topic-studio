"""选题驱动采集：对话里定下的关键词，排队 → 起采集 → 采完自动入库 → 回报给对话。

为什么不直接在对话里跑采集：

1. 一次采集 5–25 分钟，HTTP 请求早超时了，对话等不起。
2. MediaCrawler 全局只能跑一个（同一套浏览器 profile、同一套接口），必须串行。
3. 用户也可能在采集台手动点一次，那对话提交的就得让路，不能抢。

所以这里只做三件事：入队、串行调度、收尾入库。判断「该不该采」不在这一层，
在对话里由模型提、由用户点头。

状态机：
    queued ──→ running ──→ done / empty
      │            └─────→ failed
      ├────→ cancelled（用户手动取消）
      └────→ stalled（排队太久还没轮上，多半是浏览器一直没就绪）
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from . import browser, crawl_guard, importer, jobs, paths, store

TICK_SEC = 8            # 调度间隔。太密会跟手动采集抢，太疏用户等得急
MAX_KEYWORDS = 3        # 一次最多 3 个词，多了就是半天
MAX_QUEUE = 6           # 未完成任务上限，防止一口气排十来个
MAX_WAIT_MIN = 180      # 排队超过 3 小时没轮到 → stalled，不再自动起
MIN_PER_KEYWORD = 6     # 估算用：每词约 6 分钟（20 条 + 评论）
LAUNCH_RETRY_MIN = 10   # 自动拉浏览器失败后，隔 10 分钟再试，别每 8 秒敲一次

_TZ = timezone(timedelta(hours=8))
_LOCK = threading.RLock()
_WORKER: dict = {"started": False, "ticking": False}


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


# ================================================================== 读

def all_items() -> list[dict]:
    rows = store.list_items("collectQueue")
    return sorted(rows, key=lambda r: r.get("createdAt") or "")


LIVE = ("queued", "running", "stopping")


def pending() -> list[dict]:
    return [r for r in all_items() if r.get("status") in LIVE]


def for_session(sid: str, limit: int = 6) -> list[dict]:
    if not sid:
        return []
    rows = [r for r in all_items() if r.get("sessionId") == sid]
    return rows[-limit:]


def summary(session_id: str = "") -> dict:
    rows = for_session(session_id) if session_id else all_items()
    queued = [r for r in rows if r.get("status") == "queued"]
    running = [r for r in rows if r.get("status") in ("running", "stopping")]
    return {
        "queued": len(queued),
        "running": len(running),
        "items": rows,
    }


def position_of(qid: str) -> int:
    """排队第几位（1 = 下一个开跑）。已在跑或已结束返回 0。"""
    rows = [r for r in all_items() if r.get("status") in ("queued", "running")]
    for i, r in enumerate(rows):
        if r.get("id") == qid:
            return i + 1
    return 0


def eta_text(ahead_keywords: int, own_keywords: int) -> str:
    lo = max(1, ahead_keywords + own_keywords) * (MIN_PER_KEYWORD - 2)
    hi = max(1, ahead_keywords + own_keywords) * (MIN_PER_KEYWORD + 3)
    return f"约 {lo}–{hi} 分钟"


# ================================================================== 写

def enqueue(keywords: list[str], reason: str = "", session_id: str = "",
            account_id: str = "", platform: str = "xhs",
            max_notes: int = 20, topic_id: str = "") -> dict:
    kws: list[str] = []
    for k in keywords or []:
        s = str(k).strip().strip("，,、")
        if s and s not in kws and len(s) <= 24:
            kws.append(s)
    if not kws:
        return {"ok": False, "message": "没有有效关键词"}
    if len(kws) > MAX_KEYWORDS:
        return {"ok": False, "message": f"一次最多 {MAX_KEYWORDS} 个词，先挑最关键的"}

    live = pending()
    if len(live) >= MAX_QUEUE:
        return {"ok": False,
                "message": f"已经排了 {len(live)} 个采集任务在等。等这批跑完再提新的。"}

    # 已经在排队/在跑的同一个词，不重复提（采两遍是浪费额度）
    busy_words = {w for r in live for w in (r.get("keywords") or [])}
    dup = [k for k in kws if k in busy_words]
    new_kws = [k for k in kws if k not in busy_words]
    if not new_kws:
        return {"ok": False, "message": f"这几个词已经在队列里了：{'、'.join(dup)}"}

    item = {
        "id": store.new_id("q"),
        "platform": platform,
        "keywords": new_kws,
        "skipped": dup,
        "reason": str(reason or "").strip()[:200],
        "sessionId": session_id or "",
        "accountId": account_id or "",
        "topicId": topic_id or "",
        "maxNotes": max(5, min(int(max_notes or 20), 50)),
        "status": "queued",
        "jobId": None,
        "batchId": None,
        "createdAt": _now(),
        "startedAt": None,
        "endedAt": None,
        "importStats": None,
        "note": "",
        "launchAt": "",          # 上次尝试自动拉浏览器的时刻
        "noticeSent": False,
    }
    store.upsert_item("collectQueue", item, note="collect enqueue")

    port = browser.port_for("crawl")
    ready = browser.cdp_alive(port)
    ahead = sum(len(r.get("keywords") or []) for r in live)
    return {
        "ok": True,
        "id": item["id"],
        "keywords": new_kws,
        "skipped": dup,
        "position": position_of(item["id"]) or 1,
        "queue": {"queued": len([r for r in live if r.get("status") == "queued"]) + 1,
                  "running": len([r for r in live if r.get("status") == "running"])},
        "etaMinutes": eta_text(ahead, len(new_kws)),
        "browser": {"ready": ready, "port": port,
                    "role": paths.ROLE_NAMES.get("crawl", "crawl")},
        "note": ("采集浏览器已就绪，轮到了就开跑。" if ready
                 else f"采集专用浏览器还没起（{port} 端口），轮到时会自动拉起来；"
                      f"如果提示需要扫码，去采集台登录一次即可。"),
    }


def cancel(qid: str) -> dict:
    hit = {"ok": False, "status": ""}

    def _fn(data: dict) -> dict:
        for r in data.get("items") or []:
            if r.get("id") != qid:
                continue
            hit["status"] = r.get("status") or ""
            if r.get("status") == "queued":
                r["status"] = "cancelled"
                r["endedAt"] = _now()
                hit["ok"] = True
            elif r.get("status") == "running":
                # 先标 stopping 再停进程：优雅退出要几秒，不能让用户点了没反应。
                # 真正的 jobs.stop 扔线程里跑，HTTP 立刻返回。
                r["status"] = "stopping"
                hit["jobId"] = r.get("jobId")
                hit["ok"] = True
            break
        return data

    store.update("collectQueue", _fn, note=f"cancel collect {qid}")
    if hit.get("jobId"):
        threading.Thread(target=jobs.stop, args=(hit["jobId"],), daemon=True).start()
        return {"ok": True, "message": "正在停止，几秒后收尾（已采到的部分照常入库）"}
    if not hit["ok"]:
        return {"ok": False,
                "message": f"这个任务{hit['status'] or '不存在'}，取消失败"}
    return {"ok": True, "message": "已从队列移除"}


def retry(qid: str) -> dict:
    """失败/中断的重新排队。浏览器问题修好后常用。"""
    def _fn(data: dict) -> dict:
        for r in data.get("items") or []:
            if r.get("id") == qid and r.get("status") in ("failed", "empty", "stalled",
                                                          "cancelled", "interrupted"):
                r.update({"status": "queued", "note": "", "jobId": None, "batchId": None,
                          "startedAt": None, "endedAt": None, "launchAt": "",
                          "createdAt": _now(), "noticeSent": False})
            return data
        return data

    store.update("collectQueue", _fn, note=f"retry collect {qid}")
    it = next((r for r in all_items() if r.get("id") == qid), None)
    return {"ok": bool(it and it.get("status") == "queued"),
            "message": "已重新排队" if it and it.get("status") == "queued" else "这条不能重排"}


# ================================================================== 调度

def start_worker() -> None:
    with _LOCK:
        if _WORKER["started"]:
            return
        _WORKER["started"] = True
    threading.Thread(target=_loop, daemon=True, name="collect-queue").start()


def worker_alive() -> bool:
    return any(t.name == "collect-queue" and t.is_alive() for t in threading.enumerate())


def _loop() -> None:
    time.sleep(3)          # 等 app 起完，别跟启动时清理孤儿抢
    while True:
        try:
            tick()
        except Exception as e:                      # 调度线程不能死
            print(f"[collect-queue] tick 出错：{type(e).__name__}: {e}", flush=True)
        time.sleep(TICK_SEC)


def tick() -> dict:
    """一次调度：先给在跑的收尾，再起下一个。重入保护，同一时刻只跑一个 tick。"""
    with _LOCK:
        if _WORKER["ticking"]:
            return {"skipped": "busy"}
        _WORKER["ticking"] = True
    try:
        return _tick_inner()
    finally:
        with _LOCK:
            _WORKER["ticking"] = False


def _tick_inner() -> dict:
    rows = all_items()

    # 1) 在跑的：看批次结没结束，结束就导入 + 回报
    live_status = ("running", "stopping")
    finished = 0
    for r in [x for x in rows if x.get("status") in live_status]:
        if _settle(r):
            finished += 1

    rows = all_items()
    if any(r.get("status") in live_status for r in rows):
        return {"running": 1, "finished": finished}

    # 2) 队列里最老的 queued
    queued = sorted([r for r in rows if r.get("status") == "queued"],
                    key=lambda x: x.get("createdAt") or "")
    if not queued:
        return {"running": 0, "finished": finished}

    # 3) 手动采集在跑：让路，下个 tick 再看
    if jobs.is_running():
        return {"waiting": "manual job running", "finished": finished}

    nxt = queued[0]
    if _waited_too_long(nxt):
        _update(nxt["id"], {"status": "stalled", "endedAt": _now(),
                            "note": "排队太久没轮上，已停止。检查采集浏览器后再重试。"})
        _notify(nxt, "collect_stalled",
                f"采集「{'、'.join(nxt.get('keywords') or [])}」等了 3 小时还没轮到，"
                f"已停下。多半是采集专用浏览器一直没登录。")
        return {"stalled": nxt["id"], "finished": finished}

    _launch(nxt)
    return {"started": nxt["id"], "finished": finished}


def _waited_too_long(r: dict) -> bool:
    try:
        t0 = datetime.fromisoformat(r.get("createdAt") or "")
    except ValueError:
        return False
    return (datetime.now(_TZ) - t0).total_seconds() > MAX_WAIT_MIN * 60


def _launch(r: dict) -> None:
    """起采集。浏览器没就绪就先自己拉起来——用户不该为了这句话去采集台点一次。"""
    kws = r.get("keywords") or []
    planned = max(1, len(kws)) * max(1, int(r.get("maxNotes") or 20))

    # 护栏在拉浏览器之前查：冷却 / 刹车期间去拉 Chrome 是白费功夫，也会让用户以为在跑
    g = crawl_guard.check("search", planned)
    if not g.get("ok"):
        code = g.get("code") or ""
        note = str(g.get("message") or "")[:160]
        if code == "GUARD_DAILY":
            # 额度用完是当天终局，继续挂在队列里只会每小时撞一次，标 stalled 说清原因
            _update(r["id"], {"status": "stalled", "endedAt": _now(), "note": note})
            _notify(r, "collect_blocked",
                    f"采集「{'、'.join(kws)}」今天排不上了：{note}")
            return
        if r.get("note") != note:
            _update(r["id"], {"note": note})
        return

    port = browser.port_for("crawl")
    if not browser.cdp_alive(port):
        try:
            last = datetime.fromisoformat(r["launchAt"]) if r.get("launchAt") else None
        except ValueError:
            last = None
        recent = last and (datetime.now(_TZ) - last).total_seconds() < LAUNCH_RETRY_MIN * 60
        if not recent:
            # 记下尝试时刻，避免每次 tick 都去拉一遍 Chrome
            _update(r["id"], {"launchAt": _now()})
            res = browser.launch("xhs", "crawl", open_url=False, wait=25.0)
            if not res.get("ok"):
                msg = str(res.get("message") or "")
                _update(r["id"], {"note": f"等浏览器：{msg}"})
                _notify(r, "collect_blocked",
                        f"采集「{'、'.join(r.get('keywords') or [])}」在等采集专用浏览器。"
                        f"{msg}")
                return
        if not browser.cdp_alive(port):
            return

    res = jobs.start(
        platform=r.get("platform") or "xhs",
        keywords=r.get("keywords") or [],
        max_notes=int(r.get("maxNotes") or 20),
        with_comments=True,
        with_sub_comments=False,
        sort_type="",
        crawler_type="search",
        role="crawl",
        origin="chat",
        reason=r.get("reason") or "",
        request_id=r.get("id") or "",
    )
    if res.get("ok"):
        _update(r["id"], {"status": "running", "jobId": res.get("jobId"),
                          "batchId": res.get("batchId"), "startedAt": _now(), "note": ""})
        return

    if res.get("code") in ("CONFLICT_RUNNING", "CDP_NOT_READY", "GUARD_COOLDOWN", "GUARD_RISK"):
        # 都不是终态错误：让路 / 等浏览器 / 等护栏，下个 tick 再试。
        # note 变化才写盘，否则每 8 秒一次写会把 journal 灌满。
        note = str(res.get("message") or "")[:160]
        if r.get("note") != note:
            _update(r["id"], {"note": note})
        return

    if res.get("code") == "GUARD_DAILY":
        note = str(res.get("message") or "今日搜索额度已用完")
        _update(r["id"], {"status": "stalled", "endedAt": _now(), "note": note})
        _notify(r, "collect_blocked",
                f"采集「{'、'.join(kws)}」今天排不上了：{note}")
        return

    _update(r["id"], {"status": "failed", "endedAt": _now(),
                      "note": str(res.get("message") or "启动失败")[:160]})
    _notify(r, "collect_failed",
            f"采集「{'、'.join(r.get('keywords') or [])}」没起来：{res.get('message')}")


def _settle(r: dict) -> bool:
    """批次结束了就导入 + 标状态。返回是否这次处理完了。"""
    was_stopping = r.get("status") == "stopping"
    batch = _find_batch(r.get("batchId") or "")
    if not batch:
        # 批次记录被人删了：当作失败，别让它永远挂在 running
        job = jobs.get(r.get("jobId") or "")
        if job and job.get("status") == "running":
            return False
        _update(r["id"], {"status": "cancelled" if was_stopping else "failed",
                          "endedAt": _now(),
                          "note": "批次记录不见了（可能被手动删了）"})
        return True

    st = str(batch.get("status") or "")
    if st in ("running", ""):
        return False

    # ---------------- 采到了：导入
    stats = None
    note = ""
    if st in ("done", "empty", "stopped"):
        if batch.get("importedAt") and batch.get("importStats"):
            stats = batch.get("importStats")
        else:
            try:
                res = importer.import_batch(batch["id"])
                if res.get("ok"):
                    stats = res
                else:
                    note = f"自动导入失败：{res.get('message')}"
            except Exception as e:
                note = f"自动导入出错：{type(e).__name__}: {e}"

    if stats:
        added = int(stats.get("added") or 0)
        _update(r["id"], {
            "status": "cancelled" if was_stopping else ("done" if added else "empty"),
            "endedAt": _now(), "importStats": stats,
            "note": note or f"新增 {added} 条",
        })
        if was_stopping:
            _notify(r, "collect_cancelled",
                    f"「{'、'.join(r.get('keywords') or [])}」已取消；"
                    f"停之前采到的 {added} 条已入库。")
        else:
            _notify(r, "collect_done",
                    f"「{'、'.join(r.get('keywords') or [])}」采完了：新增 {added} 条，"
                    f"更新 {int(stats.get('updated') or 0)} 条，"
                    f"重复 {int(stats.get('duplicated') or 0)} 条，已自动入库。"
                    + ("可以接着分析了。" if added else "这批没新料，换个词试试。"))
        return True

    _update(r["id"], {"status": "cancelled" if was_stopping else "failed",
                      "endedAt": _now(),
                      "note": note or batch.get("note") or f"采集{st}"})
    if was_stopping:
        return True
    _notify(r, "collect_failed",
            f"「{'、'.join(r.get('keywords') or [])}」没采成："
            f"{note or batch.get('note') or st}")
    return True


def _find_batch(bid: str) -> dict | None:
    if not bid:
        return None
    return next((b for b in store.list_items("batches") if b.get("id") == bid), None)


def _update(qid: str, patch: dict) -> None:
    def _fn(data: dict) -> dict:
        for r in data.get("items") or []:
            if r.get("id") == qid:
                r.update(patch)
                break
        return data

    store.update("collectQueue", _fn, note=f"collect {qid}")


def _notify(r: dict, kind: str, text: str) -> None:
    """把结果写回对话会话（只给界面看，不进模型上下文）。"""
    sid = r.get("sessionId") or ""
    if not sid:
        return
    try:
        from . import chat

        chat.add_notice(sid, {
            "kind": kind,
            "requestId": r.get("id"),
            "keywords": r.get("keywords") or [],
            "text": text,
        })
    except Exception:
        pass
