"""复盘基线：同账号最近约 20 条已发布内容的同项中位数。

原版依据（beav-archive/builtin-skills/content-review/SKILL.md）：
    第 23 行：找基线：取同账号最近 20 条左右已发布内容的同项数据，算中位数。
             这一条比中位数高多少、低多少，用倍数说，不用形容词。
    第 70 行：不把对比基线说成「行业平均」；基线只是这个账号自己的。

两条数据来源要分清（原版第 22 行「缺的数字是未知，不是 0」）：
    backend  = 创作者后台读到的累计值（真实观测，优先用）
    manual   = 复盘页手填的 D0/D1/D3/D7
同一个账号里两者可能并存，统一在 latest_of 里挑一个，缺失就是 None，不补 0。
"""
from __future__ import annotations

import statistics

# 参与基线的指标。倍数只对有意义的几项算，比率类不重复算倍数。
BASE_FIELDS = ("like", "collect", "comment", "share", "views",
               "impressions", "fansGained")
RATE_FIELDS = ("collectRate", "commentRate", "shareRate")
COMPARE_FIELDS = ("like", "collect", "collectRate", "commentRate",
                  "views", "impressions", "fansGained")


def latest_of(review: dict) -> dict:
    """取这条复盘当前最可信的一组指标。后台观测优先，没有才回落到手填。"""
    b = review.get("backend") or {}
    if isinstance(b.get("likes"), (int, float)) or isinstance(b.get("views"), (int, float)):
        return {
            "like": b.get("likes"),
            "collect": b.get("collects"),
            "comment": b.get("comments"),
            "share": b.get("shares"),
            "views": b.get("views"),
            "impressions": b.get("impressions"),
            "fansGained": b.get("fansGained"),
            "source": "backend",
            "at": b.get("at"),
            "daysSincePublish": b.get("daysSincePublish"),
        }
    rows = [m for m in (review.get("metrics") or []) if m.get("like") is not None]
    if not rows:
        return {}
    rows.sort(key=lambda m: m.get("day", 0))
    m = rows[-1]
    return {
        "like": m.get("like"), "collect": m.get("collect"),
        "comment": m.get("comment"), "share": m.get("share"),
        "views": m.get("views"), "impressions": m.get("impressions"),
        "fansGained": m.get("fansGained"),
        "source": "manual", "day": m.get("day"),
    }


def _rates(m: dict) -> dict:
    like = m.get("like")
    out = {}
    if isinstance(like, (int, float)) and like > 0:
        for k, src in (("collectRate", "collect"), ("commentRate", "comment"),
                       ("shareRate", "share")):
            v = m.get(src)
            if isinstance(v, (int, float)):
                out[k] = round(v / like, 4)
    return out


def compute(reviews: list[dict], account_id: str = "", n: int = 20) -> dict:
    """算基线。account_id 为空时用全部复盘（工作台默认只有一个号）。"""
    rows = [r for r in reviews
            if (not account_id or (r.get("accountId") or "") == account_id)]
    rows = [r for r in rows if latest_of(r)]
    rows.sort(key=lambda r: r.get("publishedAt") or "", reverse=True)
    sample = rows[: max(1, int(n))]

    vals: dict[str, list[float]] = {k: [] for k in BASE_FIELDS + RATE_FIELDS}
    for r in sample:
        m = latest_of(r)
        for k in BASE_FIELDS:
            v = m.get(k)
            if isinstance(v, (int, float)):
                vals[k].append(float(v))
        for k, v in _rates(m).items():
            vals[k].append(float(v))

    med = {k: (round(statistics.median(v), 4) if v else None) for k, v in vals.items()}
    return {
        "accountId": account_id,
        "sampleSize": len(sample),
        "median": med,
        "computedAt": None,  # 由调用方补 store.now_iso()
    }


def compare(latest: dict, base: dict) -> dict | None:
    """倍数对比。主判据仍然是点赞（原版：依据是哪个数字比基线）。"""
    med = (base or {}).get("median") or {}
    if not med.get("like"):
        return None
    out: dict = {}
    for k in COMPARE_FIELDS:
        b = med.get(k)
        v = latest.get(k)
        out[f"{k}Multiple"] = round(v / b, 2) if (b and isinstance(v, (int, float))) else None

    m = out.get("likeMultiple")
    if m is None:
        verdict = "normal"
    elif m >= 1.3:
        verdict = "above"
    elif m <= 0.7:
        verdict = "below"
    else:
        verdict = "normal"
    out["verdict"] = verdict
    return out
