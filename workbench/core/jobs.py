"""采集任务管理：起子进程、抓日志、SSE 推送、停止、看门狗、孤儿清理。

三条来自代码实测的强制约束：
1. cwd 必须是 MediaCrawler 目录（data/、libs/、browser_data 都是相对路径）。
2. logging 打在 stderr，print 打在 stdout → 必须 stderr=STDOUT 才能看到日志。
3. Windows 上不能用 os.kill(pid, 0) 判活（会真的杀进程），只能用 tasklist。
"""
from __future__ import annotations

import asyncio
import json
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone, timedelta

from . import browser, crawl_guard, importer, paths, store

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
MAX_LOGS = 5000
_TZ = timezone(timedelta(hours=8))

# MediaCrawler 的小红书搜索排序枚举（media_platform/xhs/field.py:SearchSortType）
SORT_TYPES = ("general", "popularity_descending", "time_descending")
SORT_LABELS = {
    "general": "综合",
    "popularity_descending": "热门",
    "time_descending": "最新",
}

_JOBS: dict[str, dict] = {}
_LOCK = threading.RLock()
_WATCHDOG_STARTED = False


# ------------------------------------------------------------------ 工具

def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def is_pid_alive(pid: int) -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, encoding="mbcs", errors="replace",
            creationflags=CREATE_NO_WINDOW, timeout=10,
        ).stdout
        return str(pid) in out and "No tasks" not in out
    except Exception:
        return False


def _level(text: str) -> str:
    low = text.lower()
    if any(k in low for k in ("error", "exception", "traceback", "失败", "错误", "异常")):
        return "error"
    if any(k in low for k in ("warn", "警告", "⚠")):
        return "warn"
    if any(k in low for k in ("success", "完成", "成功")):
        return "ok"
    return "info"


def _push(job: dict, text: str) -> None:
    with _LOCK:
        job["seq"] += 1
        job["logs"].append({"seq": job["seq"], "ts": _now(), "level": _level(text), "text": text})
        if len(job["logs"]) > MAX_LOGS:
            del job["logs"][: len(job["logs"]) - MAX_LOGS]
        _append_log_file(job, text)
    # 风控只在采集器自己的输出里判，不判工作台自己写的回执行（会误命中"验证码"之类的说明文字）
    if not text.startswith("[工作台]") and crawl_guard.looks_like_risk(text):
        job["riskHits"] = int(job.get("riskHits") or 0) + 1
        r = crawl_guard.note_risk(text)
        if r.get("triggered"):
            _push(job, f"[工作台] 连续撞风控，自动刹车 {r.get('cooldownMin')} 分钟，"
                       f"期间不允许起新采集（可在采集台点「解除刹车」）")


def _append_log_file(job: dict, text: str) -> None:
    try:
        with (paths.LOGS_DIR / f"crawl-{job['id']}.log").open("a", encoding="utf-8") as f:
            f.write(f"[{_now()}] {text}\n")
    except Exception:
        pass


# ------------------------------------------------------------------ 查询

def is_running() -> bool:
    with _LOCK:
        return any(j["status"] == "running" for j in _JOBS.values())


def current() -> dict | None:
    with _LOCK:
        for j in _JOBS.values():
            if j["status"] == "running":
                return _brief(j)
    return None


def get(job_id: str) -> dict | None:
    with _LOCK:
        j = _JOBS.get(job_id)
        return _brief(j) if j else None


def list_jobs(limit: int = 20) -> list[dict]:
    with _LOCK:
        jobs = sorted(_JOBS.values(), key=lambda x: x["startedAt"], reverse=True)
        return [_brief(j) for j in jobs[:limit]]


def logs(job_id: str, after: int = 0) -> dict:
    with _LOCK:
        j = _JOBS.get(job_id)
        if not j:
            return {"items": [], "lastSeq": 0}
        items = [x for x in j["logs"] if x["seq"] > after]
        return {"items": items, "lastSeq": j["seq"]}


def _brief(j: dict) -> dict:
    return {
        "id": j["id"],
        "batchId": j["batchId"],
        "platform": j["platform"],
        "keywords": j["keywords"],
        "status": j["status"],
        "pid": j["pid"],
        "startedAt": j["startedAt"],
        "endedAt": j.get("endedAt"),
        "exitCode": j.get("exitCode"),
        "seq": j["seq"],
        "lineCount": len(j["logs"]),
    }


# ------------------------------------------------------------------ 启动

def start(platform: str, keywords: list[str], max_notes: int = 20,
          with_comments: bool = True, with_sub_comments: bool = False,
          sort_type: str = "", crawler_type: str = "search",
          creator_id: str = "", role: str = "",
          note_ids: list[str] | None = None,
          origin: str = "manual", reason: str = "", request_id: str = "") -> dict:
    if platform not in paths.PLATFORMS:
        return {"ok": False, "message": f"不支持的平台：{platform}"}
    # creator 模式按账号采、detail 模式按笔记 ID 采，都不看关键词
    crawler_type = (crawler_type or "search").strip()
    if crawler_type not in ("search", "creator", "detail"):
        return {"ok": False, "message": f"不支持的采集方式：{crawler_type}"}
    # 采自己的帖子用 own 号；爬别人用 crawl 号
    role = (role or "").strip() or ("own" if crawler_type == "creator" else "crawl")
    if role not in ("own", "crawl"):
        return {"ok": False, "message": f"不支持的浏览器角色：{role}"}

    creator_id = (creator_id or "").strip()
    ids = [str(i).strip() for i in (note_ids or []) if str(i).strip()]
    if crawler_type == "creator":
        if not creator_id:
            return {"ok": False, "message": "creator 模式必须给账号主页链接"}
        kws = []
    elif crawler_type == "detail":
        if not ids:
            return {"ok": False, "message": "detail 模式必须给笔记 ID"}
        kws = []
    else:
        kws = [k.strip() for k in keywords if k and k.strip()]
        if not kws:
            return {"ok": False, "message": "至少填一个关键词"}
    if is_running():
        return {"ok": False, "code": "CONFLICT_RUNNING", "message": "已有采集任务在跑"}

    # 采集护栏：组间冷却 / 日限额 / 风控刹车。放在起进程之前，命中就明说原因，
    # 不排队、不静默等待——用户需要知道还要等多久、或者额度还剩多少。
    planned = (len(ids) if crawler_type == "detail"
               else max(1, len(kws)) * max(1, int(max_notes)))
    guard = crawl_guard.check(crawler_type, planned)
    if not guard.get("ok"):
        return {
            "ok": False,
            "code": guard.get("code") or "GUARD_BLOCKED",
            "message": guard.get("message") or "采集护栏拦下了这次请求",
            "retryAfterSec": guard.get("retryAfterSec") or 0,
            "planned": planned,
        }

    # 排序只对小红书生效（抖音/B站搜索接口的排序字段含义不同，暂不接）
    sort_type = (sort_type or "").strip()
    if sort_type and sort_type not in SORT_TYPES:
        return {"ok": False, "message": f"不支持的排序方式：{sort_type}"}
    if platform != "xhs" or crawler_type != "search":
        sort_type = ""

    if not paths.VENV_PY.exists():
        return {"ok": False, "message": "找不到 MediaCrawler 的 Python 环境"}
    if not paths.CRAWLER_MAIN.exists():
        return {"ok": False, "message": "找不到 MediaCrawler/main.py"}

    port = browser.port_for(role)
    if not browser.cdp_alive(port):
        return {
            "ok": False,
            "code": "CDP_NOT_READY",
            "message": f"「{paths.ROLE_NAMES.get(role, role)}」的专用浏览器没就绪"
                       f"（{port} 端口）。请先启动它并确认已登录。",
            "role": role,
        }

    ts = datetime.now(_TZ).strftime("%Y%m%d_%H%M%S")
    short = datetime.now(_TZ).strftime("%Y%m%d_%H%M")
    batch_id = f"b_{short}_{platform}"
    job_id = f"j_{ts}_{platform}"

    raw_root = paths.RAW_DIR / batch_id
    raw_root.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(paths.VENV_PY), "-u", "main.py",
        "--platform", platform,
        "--type", crawler_type,
        *(["--keywords", ",".join(kws)] if kws else []),
        *(["--creator_id", creator_id] if creator_id else []),
        # detail 模式：按笔记 ID 精确抓，用于补回搜索排序变化后沉下去的笔记
        *(["--specified_id", ",".join(ids)] if ids else []),
        "--lt", "qrcode",
        "--save_data_option", "jsonl",
        "--save_data_path", str(raw_root),
        "--get_comment", "true" if with_comments else "false",
        "--get_sub_comment", "true" if with_sub_comments else "false",
        # 新闻图笔记的核心信息多半在图里，图必须当场落盘：
        # xhscdn 的链接带签名时间戳，实测 1–2 天后一律 403，事后再补就补不回来了。
        # 视频不下（MP4 动辄几十 MB），只留封面——封面上的字才是选题要用的。
        "--get_media", "true",
        "--crawler_max_notes_count", str(int(max_notes)),
        "--headless", "false",
    ]

    batch = {
        "id": batch_id,
        "platform": platform,
        "keywords": kws,
        "crawlerType": crawler_type,
        "creatorId": creator_id or None,
        "browserRole": role,
        "loginType": "qrcode",
        "sortType": sort_type or None,
        "startedAt": _now(),
        "status": "running",
        "rawDir": str(raw_root),
        "rawFiles": [],
        "importedAt": None,
        "importStats": None,
        "jobId": job_id,
        "note": "",
        # 谁的活：manual = 采集台点的；chat = 选题搭子提交的（采完自动入库）
        "origin": origin,
        "reason": reason or "",
        "requestId": request_id or "",
        "autoImport": origin == "chat",
    }
    store.upsert_item("batches", batch, note="batch start")

    env = {
        **__import__("os").environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        # MediaCrawler 的 CDP 端口写死在 base_config，靠这个环境变量指到对应角色
        "MC_CDP_PORT": str(port),
        # 只存图片，不拉视频文件（封面仍下）。见 config/base_config.py 的 ENABLE_GET_VIDEO
        "MC_GET_VIDEO": "0",
        # 实况照片附带的短视频同样不下：选题要的是图上的字，不是那段 2 秒动态
        "MC_GET_LIVE_PHOTO": "0",
    }
    if sort_type:
        env["MC_XHS_SORT"] = sort_type
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(paths.CRAWLER_DIR),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=env,
            creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
        )
    except Exception as e:
        store.upsert_item("batches", {**batch, "status": "failed", "note": str(e)})
        return {"ok": False, "message": f"启动失败：{e}"}

    job = {
        "id": job_id, "batchId": batch_id, "platform": platform,
        "keywords": kws, "sortType": sort_type or None,
        "crawlerType": crawler_type, "creatorId": creator_id or None,
        "browserRole": role, "port": port,
        "status": "running", "pid": proc.pid,
        "startedAt": _now(), "endedAt": None, "exitCode": None,
        "seq": 0, "logs": [], "proc": proc, "stopRequested": False,
        "riskHits": 0, "planned": planned,
    }
    with _LOCK:
        _JOBS[job_id] = job

    # 进程真起来了才记账（排队阶段不占额度）
    crawl_guard.record_start(job_id, crawler_type, planned, kws or ids)


    _push(job, f"$ cd {paths.CRAWLER_DIR}")
    _push(job, "$ " + " ".join(cmd))
    _push(job, f"浏览器：{paths.ROLE_NAMES.get(role, role)}（CDP {port}）")
    if sort_type:
        _push(job, f"排序：{SORT_LABELS.get(sort_type, sort_type)}（{sort_type}）")
    else:
        _push(job, "排序：平台默认")
    threading.Thread(target=_reader, args=(job,), daemon=True).start()
    _start_watchdog()
    _save_job_snapshot(job)

    return {"ok": True, "jobId": job_id, "batchId": batch_id}


def _reader(job: dict) -> None:
    proc: subprocess.Popen = job["proc"]
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            _push(job, line.rstrip("\n"))
    except Exception as e:
        _push(job, f"[工作台] 读日志中断：{e}")
    code = proc.wait()
    with _LOCK:
        job["exitCode"] = code
        job["endedAt"] = _now()
        if job["stopRequested"]:
            job["status"] = "stopped"
        elif code == 0:
            job["status"] = "done"
        else:
            job["status"] = "failed"
        status = job["status"]

    dur = _duration_sec(job["startedAt"], job["endedAt"])
    _push(job, f"[工作台] 采集结束，退出码 {code}，耗时 {dur} 秒")
    _save_job_snapshot(job)

    b = next((x for x in store.list_items("batches") if x.get("id") == job["batchId"]), None)
    files = importer.scan_raw({"rawDir": b.get("rawDir")}) if b else {"contents": [], "comments": []}
    got = _jsonl_lines(files["contents"])
    if b:
        note = ""
        # 退出码 0 不等于采到东西：MediaCrawler 遇到链接解析失败、风控、无权限都是
        # 打一条 ERROR 然后正常退出。这种必须标成 empty，不能显示"完成"骗人。
        if status == "done" and got == 0:
            note = _last_error(job) or "跑完了，但一条都没采到"
            with _LOCK:
                job["status"] = status = "empty"
                job["note"] = note
            _push(job, f"[工作台] 没采到任何帖子：{note}")
            _save_job_snapshot(job)
        store.upsert_item("batches", {
            **b, "status": status, "endedAt": job["endedAt"],
            "exitCode": code, "durationSec": dur, "note": note,
            "rawFiles": [f.name for f in files["contents"] + files["comments"]],
        }, note=f"batch {status}")

    # 护栏收尾：回填实际条数、从此刻起算组间冷却、没撞风控就清连续计数。
    # 放在 status 定稿之后：empty / failed 要如实记进去，否则日限额会虚高。
    crawl_guard.record_end(job["id"], got, status)


def _jsonl_lines(paths_: list) -> int:
    """数 jsonl 里的有效行数。空行不算。"""
    n = 0
    for f in paths_ or []:
        try:
            with open(f, encoding="utf-8") as fh:
                n += sum(1 for line in fh if line.strip())
        except Exception:
            continue
    return n


def _last_error(job: dict) -> str:
    """从日志里捞最后一条 ERROR，用来告诉用户到底为什么没采到。"""
    for line in reversed(job.get("logs") or []):
        s = line.get("text") if isinstance(line, dict) else str(line)
        if not s:
            continue
        if " ERROR " in s or "Error" in s or "解析" in s:
            i = s.find("] ", s.find("ERROR"))
            body = s[i + 2:] if i > 0 else s
            # 去掉 "[类名.方法]" 前缀
            import re as _re
            body = _re.sub(r"^\[[^\]]+\]\s*", "", body).strip()
            return _humanize_crawl_error(body)[:180]
    return ""


# MediaCrawler 的报错是英文的，直接甩给用户看不懂。只翻最常见的几种。
_ERROR_HINTS = (
    ("banned by xhs", "这个主页打不开：可能不是公开账号（私密/被封），或者链接不是主页链接"),
    ("Unable to parse creator info", "链接格式认不出来，用主页链接或主页地址里那串 id"),
    ("Login state", "登录状态失效了，去采集台重新扫码登录"),
    ("IPBlock", "IP 被限流了，过一会儿再试"),
    ("rate limit", "请求太频繁被限流，过一会儿再试"),
)


def _humanize_crawl_error(msg: str) -> str:
    low = msg.lower()
    for key, cn in _ERROR_HINTS:
        if key.lower() in low:
            return f"{cn}（原文：{msg[:90]}）"
    return msg


def _duration_sec(a: str, b: str) -> int:
    try:
        fa = datetime.fromisoformat(a)
        fb = datetime.fromisoformat(b)
        return max(0, int((fb - fa).total_seconds()))
    except Exception:
        return 0


# ------------------------------------------------------------------ 停止

def stop(job_id: str) -> dict:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return {"ok": False, "message": "任务不存在"}
        if job["status"] != "running":
            return {"ok": False, "message": "任务已结束"}
        job["stopRequested"] = True
        proc: subprocess.Popen = job["proc"]
        pid = proc.pid

    _push(job, "[工作台] 收到停止请求，先尝试优雅退出…")
    try:
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    except Exception:
        pass

    for _ in range(16):
        if proc.poll() is not None:
            return {"ok": True, "mode": "graceful"}
        time.sleep(0.5)

    _push(job, "[工作台] 优雅退出超时，强制结束进程树")
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=15)
    except Exception as e:
        return {"ok": False, "message": str(e)}
    return {"ok": True, "mode": "forced"}


def stop_current() -> dict:
    cur = current()
    return stop(cur["id"]) if cur else {"ok": False, "message": "没有在跑的任务"}


# ------------------------------------------------------------------ 看门狗 / 孤儿

def _start_watchdog() -> None:
    global _WATCHDOG_STARTED
    with _LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True
    threading.Thread(target=_watchdog_loop, daemon=True).start()


def _watchdog_loop() -> None:
    while True:
        time.sleep(30)
        limit = int(store.settings()["crawler"].get("maxJobMinutes", 30)) * 60
        with _LOCK:
            running = [j for j in _JOBS.values() if j["status"] == "running"]
        for j in running:
            if _duration_sec(j["startedAt"], _now()) > limit:
                _push(j, f"[工作台] 超过 {limit // 60} 分钟，自动停止")
                stop(j["id"])
                with _LOCK:
                    j["status"] = "timeout"


def cleanup_orphans() -> dict:
    """服务启动时调用：把上次遗留的 running 快照标成 interrupted。"""
    fixed = 0
    for f in paths.JOBS_DIR.glob("*.json"):
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if snap.get("status") == "running":
            pid = int(snap.get("pid") or 0)
            if pid and is_pid_alive(pid):
                try:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=15)
                except Exception:
                    pass
            snap["status"] = "interrupted"
            try:
                f.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            b = next((x for x in store.list_items("batches")
                      if x.get("id") == snap.get("batchId")), None)
            if b and b.get("status") == "running":
                store.upsert_item("batches", {**b, "status": "interrupted"},
                                  note="orphan cleanup")
            fixed += 1
    return {"ok": True, "fixed": fixed}


def _save_job_snapshot(job: dict) -> None:
    try:
        p = paths.JOBS_DIR / f"{job['id']}.json"
        p.write_text(json.dumps({
            "id": job["id"], "batchId": job["batchId"], "platform": job["platform"],
            "status": job["status"], "pid": job["pid"],
            "startedAt": job["startedAt"], "endedAt": job.get("endedAt"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ------------------------------------------------------------------ SSE

async def stream(job_id: str, after: int = 0):
    """按 seq 增量推送日志。用轮询而非队列，避免跨线程传 asyncio 对象。"""
    sent = after
    idle = 0
    while True:
        with _LOCK:
            job = _JOBS.get(job_id)
            if not job:
                yield _sse("error", {"message": "任务不存在"})
                return
            items = [x for x in job["logs"] if x["seq"] > sent]
            status = job["status"]
            if items:
                sent = items[-1]["seq"]

        for it in items:
            yield _sse("log", it, it["seq"])

        if status != "running":
            yield _sse("done", {"status": status,
                                "exitCode": job.get("exitCode"),
                                "endedAt": job.get("endedAt")})
            return

        idle += 1
        if not items and idle % 25 == 0:
            yield ": ping\n\n"
        await asyncio.sleep(0.3)


def _sse(event: str, payload: dict, seq: int | None = None) -> str:
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
