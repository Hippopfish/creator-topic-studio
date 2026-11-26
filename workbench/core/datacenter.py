"""创作者后台数据同步（只读）。

为什么必须走 CDP 而不是直接请求：
    后台接口带 x-s / x-t 签名，手写 httpx 会被直接拒。
    唯一稳的办法是打开页面、让页面自己去请求，我们只读响应。

数据模型（照原版 metricsRef）：
    (postId, observedAt, metrics) 快照序列。增长由两条快照相减得出，不推算。
    账号级另有逐日序列（曝光/观看/赞藏评转/涨粉），平台直接给的是按天的，原样存。

隐私：
    后台的 /api/galaxy/user/info 会返回手机号、实名状态。
    本模块用白名单硬约束，只让 ACCOUNT_WHITELIST 里的字段进库，其余一律丢弃。
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from . import browser, paths, store

_TZ = timezone(timedelta(hours=8))
_LOCK = threading.RLock()
_THREAD: dict = {"t": None}

HOME_URL = "https://creator.xiaohongshu.com/new/home"
NOTES_URL = "https://creator.xiaohongshu.com/new/note-manager"

API_PERSONAL = "/api/galaxy/creator/home/personal_info"
API_ACCOUNT = "/api/galaxy/v2/creator/datacenter/account/base"
API_POSTED = "/api/galaxy/v2/creator/note/user/posted"

# ---- 白名单：只有这些字段允许进库 ----
ACCOUNT_WHITELIST = ("name", "red_num", "fans_count", "faved_count",
                     "follow_count", "personal_desc")
POST_WHITELIST = ("id", "display_title", "time", "likes", "collected_count",
                  "comments_count", "shared_count", "view_count", "type", "sticky")

# ---- 逐日序列：平台按天给的原始数组 ----
DAILY_MAP = {
    "impressions": "impl_count_list",
    "views": "view_list",
    "likes": "like_list",
    "collects": "collect_list",
    "comments": "comment_list",
    "shares": "share_list",
    "quotes": "quote_list",
    "fansGained": "rise_fans_list",
    "fansLost": "loss_fans_list",
    "coverClickRate": "cover_click_rate_list",
    "avgViewTime": "avg_view_time_list",
    "fullViewRate": "video_full_view_rate_list",
    "published": "publish_normal_note_num_list",
}

# ---- 周期汇总：平台给的区间总数 ----
SUM_MAP = {
    "impressions": "impl_count",
    "views": "view_count",
    "likes": "like_count",
    "collects": "collect_count",
    "shares": "share_count",
    "quotes": "quote_count",
    "fansGained": "rise_fans_count",
    "fansLost": "loss_fans_count",
    "netFans": "net_rise_fans_count",
    "homeViews": "home_view_count",
}
# 平台没给评论总数（只有逐日），汇总时自己累加
RATE_MAP = {
    "coverClickRate": "cover_click_rate",
    "avgViewTime": "avg_view_time",
    "fullViewRate": "video_full_view_rate",
}

STEPS = [
    ("login", "确认登录"),
    ("account", "账号概览"),
    ("daily", "日序列"),
    ("posts", "逐篇数据"),
]

MAX_PAGES = 10         # 笔记管理页每滚一次 +10 篇左右，40 篇账号约 4 次
PAGE_WAIT_MS = 3000
HOME_WAIT_MS = 9000

# 笔记管理页是内部容器滚动，window.scrollTo 不生效（实测只滚 window 一直是 12 篇）。
# 这里找页面上可滚动且内容最高的那个容器，滚到底触发加载更多。
SCROLL_JS = """() => {
    let best = null, hs = 0;
    document.querySelectorAll('*').forEach(el => {
        const s = getComputedStyle(el);
        if ((s.overflowY === 'auto' || s.overflowY === 'scroll')
            && el.scrollHeight > el.clientHeight + 50
            && el.scrollHeight > hs) { hs = el.scrollHeight; best = el; }
    });
    if (!best) return 0;
    best.scrollTop = best.scrollHeight;
    return best.scrollTop;
}"""


# ================================================================== 小工具

def _num(v):
    """平台数字可能是 int / float / None / 带单位的字符串。拿不准就返回 None。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        mult = 1
        if s.endswith("万"):
            mult, s = 10000, s[:-1]
        elif s.endswith("亿"):
            mult, s = 100000000, s[:-1]
        try:
            f = float(s) * mult
            return int(f) if f == int(f) else round(f, 2)
        except ValueError:
            return None
    return None


def _day(ms) -> str:
    """各种时间写法 -> YYYY-MM-DD（东八区）。

    后台同一个字段有两种形态：看板给毫秒时间戳，笔记列表给 '2023-11-01 01:30' 字符串。
    这里都认，认不出就返回空串（不瞎猜）。
    """
    n = _num(ms)
    if n:
        # 大于 1e12 当毫秒，否则当秒
        ts = n / 1000 if n > 1e12 else n
        try:
            return datetime.fromtimestamp(ts, _TZ).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            pass
    if isinstance(ms, str):
        m = re.match(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})", ms)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def _today() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%d")


def _job() -> dict | None:
    j = store.read("stats/job") or {}
    return j or None


# ================================================================== 状态

def status() -> dict:
    job = _job()
    running = bool(job and job.get("status") == "running")
    if running:
        # 服务重启会把后台线程带走，但 job 文件会停在 running。
        # 用线程是否还活着来纠正，和档案台的 _alive 一个道理。
        t = _THREAD.get("t")
        if not (t and t.is_alive()):
            running = False
            job = {**job, "status": "interrupted",
                   "error": job.get("error") or "服务重启，这次同步中断了"}
    acc = store.read("stats/account").get("items") or []
    shots = store.read("stats/postshots").get("items") or []
    last = acc[-1] if acc else {}
    info = store.read("stats/account_info") or {}
    return {
        "running": running,
        "job": job,
        "account": info.get("account") or {},
        "summary": info.get("summary") or {},
        "counts": {
            "days": len(acc),
            "posts": len({s.get("key") for s in shots if s.get("key")}),
            "snapshots": len(shots),
        },
        "latestDate": last.get("date") or "",
        "lastSyncAt": info.get("syncedAt"),
        "port": browser.port_for("own"),
        "browserRunning": browser.cdp_alive(browser.port_for("own")),
    }


def daily(days: int = 30) -> list[dict]:
    """账号级逐日序列，按日期升序，取最近 days 天。"""
    items = store.read("stats/account").get("items") or []
    items = sorted(items, key=lambda x: x.get("date") or "")
    return items[-int(days):] if days else items


def summary() -> dict:
    """最近一次同步拿到的周期汇总（近 7 日 / 近 30 日）。"""
    info = store.read("stats/account_info") or {}
    return info.get("summary") or {}


def snapshots(key: str = "") -> list[dict]:
    """某篇笔记的快照序列；不传 key 返回全部。"""
    items = store.read("stats/postshots").get("items") or []
    if key:
        items = [s for s in items if s.get("key") == key]
    return sorted(items, key=lambda x: x.get("at") or "")


# ================================================================== 同步

def sync(reason: str = "manual") -> dict:
    """起后台线程同步一次。已在跑就直接返回当前任务。"""
    with _LOCK:
        if _THREAD["t"] and _THREAD["t"].is_alive():
            return {"ok": False, "code": "CONFLICT_RUNNING",
                    "message": "已经在同步了，等它跑完", "job": _job()}

        port = browser.port_for("own")
        if not browser.cdp_alive(port):
            return {
                "ok": False, "code": "CDP_NOT_READY",
                "message": f"「我的号」的浏览器没开（{port} 端口）。"
                           f"请先在采集台启动「我的号」并确认已登录。",
            }

        job = {
            "id": store.new_id("ds"),
            "status": "running",
            "reason": reason,
            "port": port,
            "step": "login",
            "steps": [{"key": k, "name": n, "status": "pending", "detail": ""}
                      for k, n in STEPS],
            "warnings": [],
            "error": "",
            "startedAt": store.now_iso(),
            "endedAt": None,
            "counts": {"days": 0, "posts": 0},
        }
        store.write("stats/job", job, note="stats sync start")

        t = threading.Thread(target=_run, args=(job, port), daemon=True)
        _THREAD["t"] = t
        t.start()
    return {"ok": True, "jobId": job["id"]}


def _step(job: dict, key: str, status: str, detail: str = "") -> None:
    for s in job["steps"]:
        if s["key"] == key:
            s["status"] = status
            if detail:
                s["detail"] = detail
    job["step"] = key
    store.write("stats/job", job, note=f"stats step {key} {status}")


def _run(job: dict, port: int) -> None:
    try:
        data = asyncio.run(_grab(port, job))
        _save(job, data)
        job["status"] = "done"
    except Exception as e:
        job["status"] = "failed"
        job["error"] = _human_error(e)
    finally:
        job["endedAt"] = store.now_iso()
        store.write("stats/job", job, note=f"stats sync {job['status']}")


def _human_error(e: Exception) -> str:
    msg = f"{type(e).__name__}: {e}"
    low = msg.lower()
    if "econnrefused" in low or "connect_over_cdp" in low:
        return "连不上专用浏览器。请先在采集台启动「我的号」，确认已登录后再同步。"
    if "timeout" in low:
        return "页面加载超时。创作者后台有时较慢，稍后重试即可；反复超时请检查网络。"
    return msg[:300]


# ================================================================== 抓取

async def _grab(port: int, job: dict) -> dict:
    from playwright.async_api import async_playwright

    out: dict = {"account": None, "summary": {}, "posts": []}
    pending: list = []
    posts_raw: dict[str, dict] = {}

    def collect(resp) -> None:
        u = resp.url
        if API_PERSONAL in u or API_ACCOUNT in u or API_POSTED in u:
            pending.append(resp)

    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = b.contexts[0] if b.contexts else await b.new_context()

        # ---------- 1 首页：账号概览 + 看板
        _step(job, "login", "running", "打开创作者中心")
        page = await ctx.new_page()
        page.on("response", collect)
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            job["warnings"].append(f"首页加载警告：{type(e).__name__}")
        await page.wait_for_timeout(HOME_WAIT_MS)

        if "login" in page.url or "passport" in page.url:
            _step(job, "login", "failed", "登录已过期")
            raise RuntimeError("登录过期：创作者后台跳到了登录页")

        parsed = await _drain(pending)
        if not parsed.get("personal") and not parsed.get("account"):
            body = ""
            try:
                body = (await page.inner_text("body"))[:200]
            except Exception:
                pass
            if "登录" in body or "扫码" in body:
                _step(job, "login", "failed", "登录已过期")
                raise RuntimeError("登录过期：页面要求重新登录")
            job["warnings"].append("没读到账号概览接口，可能页面结构变了")
        _step(job, "login", "done",
              f"已登录 {parsed.get('personal', {}).get('name') or ''}".strip())

        # ---------- 2 账号概览
        _step(job, "account", "running")
        personal = _pick(parsed.get("personal") or {}, ACCOUNT_WHITELIST)
        out["account"] = personal
        if personal:
            _step(job, "account", "done",
                  f"{personal.get('name') or ''} · 粉丝 {personal.get('fans_count')}")
        else:
            _step(job, "account", "failed", "没读到账号信息")

        # ---------- 3 日序列
        _step(job, "daily", "running")
        daily_rows, summ = _parse_dashboard(parsed.get("account") or {})
        out["summary"] = summ
        if daily_rows:
            _save_daily(job, daily_rows)
            _step(job, "daily", "done", f"{len(daily_rows)} 天")
        else:
            _step(job, "daily", "failed", "看板里没有逐日数据")

        # ---------- 4 逐篇
        _step(job, "posts", "running", "打开笔记管理")
        page2 = await ctx.new_page()
        page2.on("response", collect)
        try:
            await page2.goto(NOTES_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            job["warnings"].append(f"笔记管理页加载警告：{type(e).__name__}")
        await page2.wait_for_timeout(HOME_WAIT_MS)

        for i in range(MAX_PAGES):
            fresh = await _drain(pending, posts_raw)
            if fresh["added"]:
                _step(job, "posts", "running", f"已取 {len(posts_raw)} 篇")
            # 已经到底就不再滚：滚过一次仍无新增，说明没有更多了
            if i and not fresh["added"]:
                break
            try:
                await page2.evaluate(SCROLL_JS)
            except Exception:
                pass
            await page2.wait_for_timeout(PAGE_WAIT_MS)

        await _drain(pending, posts_raw)
        out["posts"] = [_pick(v, POST_WHITELIST) for v in posts_raw.values()]
        if out["posts"]:
            _step(job, "posts", "done", f"{len(out['posts'])} 篇")
        else:
            _step(job, "posts", "failed", "没读到笔记列表")

        for pg in (page, page2):
            try:
                await pg.close()
            except Exception:
                pass
        # 不要 b.close()：connect_over_cdp 上它会掐掉用户的浏览器

    return out


async def _drain(pending: list, posts_raw: dict | None = None) -> dict:
    """把排队中的响应解析掉。pending 会被清空，避免重复解析。

    返回 {"personal", "account", "added"}，added 是这一轮新增的笔记数。
    """
    before = len(posts_raw) if posts_raw is not None else 0
    res: dict = {"personal": None, "account": None}
    while pending:
        resp = pending.pop(0)
        try:
            j = await resp.json()
        except Exception:
            continue
        data = j.get("data") if isinstance(j, dict) else None
        if not isinstance(data, dict):
            continue
        u = resp.url
        if API_PERSONAL in u:
            res["personal"] = data
        elif API_ACCOUNT in u:
            res["account"] = data
        elif API_POSTED in u:
            notes = data.get("notes") or data.get("list") or []
            if posts_raw is not None:
                for n in notes:
                    if isinstance(n, dict) and n.get("id"):
                        posts_raw.setdefault(str(n["id"]), n)
    res["added"] = (len(posts_raw) - before) if posts_raw is not None else 0
    return res


def _pick(src: dict, keys: tuple) -> dict:
    """白名单抽取。不在白名单里的字段一律不进内存结果。"""
    return {k: src.get(k) for k in keys if src.get(k) is not None}


def _parse_dashboard(data: dict) -> tuple[list[dict], dict]:
    """把 account/base 的 seven / thirty 摊成逐日行 + 周期汇总。"""
    seven = data.get("seven") if isinstance(data.get("seven"), dict) else {}
    thirty = data.get("thirty") if isinstance(data.get("thirty"), dict) else {}
    src = seven or thirty
    if not src:
        return [], {}

    # ---- 逐日：以日期为键合并多个序列
    by_date: dict[str, dict] = {}
    for field, api_key in DAILY_MAP.items():
        arr = src.get(api_key)
        if not isinstance(arr, list):
            continue
        for row in arr:
            if not isinstance(row, dict):
                continue
            d = _day(row.get("date"))
            if not d:
                continue
            cell = by_date.setdefault(d, {"date": d})
            v = _num(row.get("count"))
            if v is not None:
                cell[field] = v

    # 评论汇总平台不给，用逐日累加兜底
    if "comments" not in src and "comment_list" in src:
        tot = 0
        ok = False
        for row in src.get("comment_list") or []:
            v = _num((row or {}).get("count"))
            if v is not None:
                tot += v
                ok = True
        if ok:
            src = {**src, "_comment_sum": tot}

    # ---- 周期汇总
    summ: dict = {"span": "seven" if seven else "thirty"}
    for k, api_key in SUM_MAP.items():
        v = _num(src.get(api_key))
        if v is not None:
            summ[k] = v
    for k, api_key in RATE_MAP.items():
        v = _num(src.get(api_key))
        if v is not None:
            summ[k] = v
    if "_comment_sum" in src:
        summ["comments"] = src["_comment_sum"]
    b, e = _day(src.get("begin_time")), _day(src.get("end_time"))
    if b:
        summ["beginDate"] = b
    if e:
        summ["endDate"] = e

    rows = [by_date[d] for d in sorted(by_date)]
    return rows, summ


# ================================================================== 落库

def _save_daily(job: dict, rows: list[dict]) -> None:
    """按日期合并进 stats/account。同一天以最新一次为准，不覆盖别的天。"""
    cur = store.read("stats/account")
    items = {x.get("date"): x for x in (cur.get("items") or []) if x.get("date")}
    at = store.now_iso()
    for r in rows:
        d = r.get("date")
        if not d:
            continue
        items[d] = {**items.get(d, {}), **r, "observedAt": at}
    merged = [items[d] for d in sorted(items)]
    store.write("stats/account", {"schemaVersion": 1, "items": merged},
                note=f"stats daily {len(rows)}")
    job["counts"]["days"] = len(rows)


def _save(job: dict, data: dict) -> None:
    acc = data.get("account") or {}
    summ = data.get("summary") or {}
    posts = data.get("posts") or []
    at = store.now_iso()
    day = _today()

    # ---- 账号信息（白名单已过滤，绝无 phone/realName）
    info = store.read("stats/account_info") or {}
    store.write("stats/account_info", {
        "schemaVersion": 1,
        "account": {**(info.get("account") or {}), **acc},
        "summary": summ,
        "syncedAt": at,
    }, note="stats account info")

    # ---- 逐篇快照：(key, 日期) 唯一，同一天重复同步只留最新
    if posts:
        cur = store.read("stats/postshots")
        shots = cur.get("items") or []
        index = {}
        for s in shots:
            index[(s.get("key"), (s.get("at") or "")[:10])] = s
        for n in posts:
            nid = str(n.get("id") or "")
            if not nid:
                continue
            key = f"xhs:{nid}"
            row = {
                "key": key,
                "noteId": nid,
                "title": n.get("display_title") or "",
                "publishedAt": _day(n.get("time") or n.get("visible_time")),
                "type": n.get("type") or "",
                "sticky": bool(n.get("sticky")),
                "likes": _num(n.get("likes")),
                "collects": _num(n.get("collected_count")),
                "comments": _num(n.get("comments_count")),
                "shares": _num(n.get("shared_count")),
                "views": _num(n.get("view_count")),
                "at": at,
                "source": "creator-center",
            }
            index[(key, day)] = row
        merged = [index[k] for k in sorted(index, key=lambda x: (x[0] or "", x[1] or ""))]
        store.write("stats/postshots", {"schemaVersion": 1, "items": merged},
                    note=f"stats posts {len(posts)}")
        job["counts"]["posts"] = len(posts)

    # ---- 同步结果挂到档案台 owner，方便别处知道"后台数据到哪天"
    owner = store.read("profile/owner") or {}
    if owner:
        store.write("profile/owner", {
            **owner,
            "backend": {"syncedAt": at, "latestDate": (daily(1) or [{}])[-1].get("date") or "",
                        "fans": acc.get("fans_count")},
        }, note="stats sync owner")
