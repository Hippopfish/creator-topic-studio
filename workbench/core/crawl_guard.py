"""采集护栏：组间冷却 + 日限额 + 风控刹车。

依据 2026-11-26 的实测（当天 46 分钟内连采 318 条）：

1. 461 CAPTCHA 只拦单次请求。命中后 16 秒，同一个浏览器、同一个账号正常出了 40 条，
   账号没有被封，也没有降权迹象。
2. 触发原因不是累计总量，是密度：上一组 12:51:27 结束、下一组 12:51:30 开跑，
   间隔 3 秒，连撞 4 组之后开始出 461。当天累计 318 条本身没被拦。
3. 所以护栏不需要「一天只能采几条」这种硬上限来保命，它要防的是
   「手快连点 / 补采脚本一把梭 / 队列不喘气」造成的密度堆积。

由此定三个开关（值可在设置里改，见 store.settings()['crawler']）：

- guardCooldownSec  组间冷却，上一组结束后至少等这么久再起下一组，默认 60 秒
- guardDailySearch  搜索类采集一天累计上限，默认 300 条；按 ID 补采（detail）不计入
- guardRiskStrikes / guardRiskCooldownMin
  日志里连续撞到 N 次风控（默认 2 次）→ 全局禁采 M 分钟（默认 30）

状态落在 data/crawlGuard.json。刻意不存内存：重启进程不能把限额和刹车清零，
否则「重启一下就绕过」等于没做。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from . import paths, store

_TZ = timezone(timedelta(hours=8))
_LOCK = threading.RLock()
_FILE = "crawlGuard"

# 默认值：settings.crawler 里没配就用这些。key 与设置项同名，避免两套名字对不上
DEFAULTS = {
    "guardCooldownSec": 60,
    "guardDailySearch": 300,
    "guardRiskStrikes": 2,
    "guardRiskCooldownMin": 30,
}

# 搜索类才吃日限额；detail（按 ID 补采）和 creator（读自己后台）单独计数、不设上限
LIMITED_TYPES = ("search",)

# 风控特征。必须是采集器的错误/警告行才算，且只认这几种原文——
# 2026-11-26 用 12454 行历史日志验过：不卡级别时 36 行命中，真的只有 3 行，
# 其余全是笔记正文里出现「频繁」、note_id / 时间戳里恰好带 461 数字。
RISK_MARKS = (
    "captcha appeared",     # client.py:163 - CAPTCHA appeared, request failed
    "verifytype",           # 同上，验证码字段
    "ipblock",              # IP 层限流
    "status code 461",      # <Response [461 status code 461]>
    "status_code: 461",
    "banned by xhs",        # 账号被平台处理，比风控更严重
)


def _now() -> datetime:
    return datetime.now(_TZ)


def _iso(t: datetime | None = None) -> str:
    return (t or _now()).isoformat(timespec="seconds")


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s))
        return t if t.tzinfo else t.replace(tzinfo=_TZ)
    except Exception:
        return None


# ------------------------------------------------------------------ 配置

def limits() -> dict:
    """从设置里读三个开关。缺字段/写错类型一律回默认值，不让配置错误挡住采集。"""
    c = (store.settings().get("crawler") or {})

    def _int(key: str, lo: int, hi: int) -> int:
        try:
            v = int(c.get(key, DEFAULTS[key]))
        except (TypeError, ValueError):
            v = DEFAULTS[key]
        return max(lo, min(hi, v))

    return {
        "cooldownSec": _int("guardCooldownSec", 0, 600),
        "dailySearch": _int("guardDailySearch", 0, 5000),
        "riskStrikes": _int("guardRiskStrikes", 1, 10),
        "riskCooldownMin": _int("guardRiskCooldownMin", 1, 720),
    }

# ------------------------------------------------------------------ 状态

def _blank() -> dict:
    return {"schemaVersion": 1, "events": [], "lastEndAt": None,
            "risk": {"streak": 0, "blockedUntil": None, "lastMark": ""},
            "note": ""}


def state() -> dict:
    s = store.read(_FILE, default=_blank())
    base = _blank()
    for k, v in base.items():
        if k not in s:
            s[k] = v
    if not isinstance(s.get("events"), list):
        s["events"] = []
    if not isinstance(s.get("risk"), dict):
        s["risk"] = base["risk"]
    for k, v in base["risk"].items():
        s["risk"].setdefault(k, v)
    return s


def _save(s: dict) -> None:
    # 只留最近 300 条流水，够算当天用量，也不让文件无限长
    s["events"] = (s.get("events") or [])[-300:]
    store.write(_FILE, s, note="crawl guard")


# ------------------------------------------------------------------ 计数

def _used(events: list[dict], kind: str = "", day: str = "") -> int:
    """某天用掉的条数。actual 有值按 actual，没有就按 planned 估（偏保守）。"""
    day = day or _today()
    n = 0
    for e in events:
        if not str(e.get("at") or "").startswith(day):
            continue
        if kind and e.get("type") != kind:
            continue
        try:
            n += int(e.get("actual") if e.get("actual") is not None else e.get("planned") or 0)
        except (TypeError, ValueError):
            continue
    return n


def today() -> dict:
    """当日用量明细，给界面显示。"""
    ev = state().get("events") or []
    out = {}
    for t in ("search", "detail", "creator"):
        out[t] = _used(ev, t)
    out["total"] = _used(ev)
    out["day"] = _today()
    return out


def _risk_left(s: dict) -> int:
    """刹车还剩几秒。没刹车返回 0。"""
    until = _parse(s.get("risk", {}).get("blockedUntil"))
    if not until:
        return 0
    return max(0, int((until - _now()).total_seconds()))


def _cooldown_left(s: dict) -> int:
    lim = limits()["cooldownSec"]
    if lim <= 0:
        return 0
    last = _parse(s.get("lastEndAt"))
    if not last:
        return 0
    return max(0, lim - int((_now() - last).total_seconds()))


def _mmss(sec: int) -> str:
    if sec >= 60:
        return f"{sec // 60} 分 {sec % 60} 秒"
    return f"{sec} 秒"


# ------------------------------------------------------------------ 判定

def check(crawler_type: str = "search", planned: int = 0) -> dict:
    """起采集前的三道闸。ok=True 才允许跑。

    planned = 本次计划条数（maxNotes × 关键词数），用于日限额预估。
    """
    lim = limits()
    s = state()
    with _LOCK:
        left = _risk_left(s)
        if left:
            return {
                "ok": False, "code": "GUARD_RISK", "retryAfterSec": left,
                "message": f"连续撞到风控，已自动刹车。"
                           f"还要等 {_mmss(left)}（到 "
                           f"{_parse(s['risk']['blockedUntil']).strftime('%H:%M')}）"
                           f"才允许再采。急着采可在采集台点「解除刹车」。",
            }

        left = _cooldown_left(s)
        if left:
            return {
                "ok": False, "code": "GUARD_COOLDOWN", "retryAfterSec": left,
                "message": f"上一组刚结束，组间冷却还剩 {_mmss(left)}"
                           f"（设定 {lim['cooldownSec']} 秒）。这是 461 的主要触发点。",
            }

        ct = (crawler_type or "search").strip()
        if ct in LIMITED_TYPES:
            used = _used(s.get("events") or [], "search")
            cap = lim["dailySearch"]
            if cap and used + max(0, int(planned)) > cap:
                return {
                    "ok": False, "code": "GUARD_DAILY",
                    "message": f"今天搜索类已采 {used} 条，上限 {cap} 条；"
                               f"这次要 {planned} 条，超了 {used + planned - cap} 条。"
                               f"要么减条数，要么明天再采（按 ID 补采不受此限）。",
                    "used": used, "cap": cap,
                }
    return {"ok": True}


# ------------------------------------------------------------------ 记账

def record_start(job_id: str, crawler_type: str, planned: int,
                 keywords: list[str] | None = None) -> None:
    """采集进程起来了才记账。只入队不算——排队可能几小时都不跑。"""
    with _LOCK:
        s = state()
        s["events"].append({
            "id": job_id,
            "at": _iso(),
            "type": (crawler_type or "search").strip(),
            "planned": max(0, int(planned)),
            "actual": None,
            "status": "running",
            "keywords": list(keywords or [])[:5],
        })
        _save(s)


def record_end(job_id: str, actual: int | None, status: str = "done") -> None:
    """采集结束：回填实际条数、记下结束时刻（冷却从这一刻起算）、没撞风控就清连续计数。"""
    with _LOCK:
        s = state()
        for e in reversed(s.get("events") or []):
            if e.get("id") != job_id:
                continue
            if actual is not None:
                e["actual"] = max(0, int(actual))
            e["status"] = status
            break
        s["lastEndAt"] = _iso()
        if status in ("done", "empty") and not _risk_left(s):
            # 跑完了、且当前没处于刹车中 → 说明没被盯上，连续计数清零
            s["risk"]["streak"] = 0
        _save(s)


def looks_like_risk(text: str) -> bool:
    s = str(text or "")
    if not s:
        return False
    # 先卡日志级别：笔记/评论的原始数据也是打日志的（INFO 行），正文里啥字都可能有
    if not any(k in s for k in ("ERROR", "WARN", "WARNING")):
        return False
    return any(k in s.lower() for k in RISK_MARKS)


def note_risk(text: str = "") -> dict:
    """日志里撞到风控。连续 N 次就全局刹车。返回最新状态（含是否刚触发刹车）。"""
    LIM = limits()
    with _LOCK:
        s = state()
        left = _risk_left(s)
        if left:
            # 已经在刹车里，就别再累计、也别顺延刹车时间——否则同一组连撞 4 次
            # 会把 30 分钟拖成 2 小时，跟「冷却」的初衷相反
            return {"ok": True, "triggered": False, "blockedUntil": s["risk"].get("blockedUntil"),
                    "cooldownMin": LIM["riskCooldownMin"], "already": True,
                    "mark": s["risk"].get("lastMark") or ""}
        s["risk"]["streak"] = int(s["risk"].get("streak") or 0) + 1
        s["risk"]["lastMark"] = str(text or "")[:160]
        triggered = False
        if s["risk"]["streak"] >= LIM["riskStrikes"]:
            s["risk"]["blockedUntil"] = _iso(_now() + timedelta(minutes=LIM["riskCooldownMin"]))
            s["note"] = (f"连续 {s['risk']['streak']} 次风控，"
                         f"禁采到 {_parse(s['risk']['blockedUntil']).strftime('%H:%M')}")
            s["risk"]["streak"] = 0          # 刹车已生效，计数归零重新数
            triggered = True
        _save(s)
        return {"ok": True, "triggered": triggered,
                "blockedUntil": s["risk"].get("blockedUntil"),
                "cooldownMin": LIM["riskCooldownMin"],
                "mark": s["risk"]["lastMark"]}


def reset(what: str = "risk") -> dict:
    """手动解除。what = risk（刹车）/ cooldown（组间冷却）/ all。"""
    with _LOCK:
        s = state()
        what = (what or "risk").strip()
        if what in ("risk", "all"):
            s["risk"]["streak"] = 0
            s["risk"]["blockedUntil"] = None
            s["note"] = ""
        if what in ("cooldown", "all"):
            s["lastEndAt"] = None
        _save(s)
    return {"ok": True, "message": "已解除" if what != "all" else "已全部解除",
            "snapshot": snapshot()}


# ------------------------------------------------------------------ 展示

def snapshot() -> dict:
    """给界面用的一屏状态。"""
    lim = limits()
    s = state()
    used = today()
    return {
        "limits": lim,
        "today": used,
        "cooldown": {"leftSec": _cooldown_left(s), "lastEndAt": s.get("lastEndAt")},
        "risk": {
            "leftSec": _risk_left(s),
            "blockedUntil": s["risk"].get("blockedUntil"),
            "streak": int(s["risk"].get("streak") or 0),
            "lastMark": s["risk"].get("lastMark") or "",
            "strikes": lim["riskStrikes"],
        },
        "note": s.get("note") or "",
        "recent": (s.get("events") or [])[-12:],
    }
