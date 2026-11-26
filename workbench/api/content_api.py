"""内容库接口：采集结果查询、详情、评论、手工标注。"""
from __future__ import annotations

import time
from datetime import datetime

from fastapi import APIRouter, Body, HTTPException

from core import normalize, paths, store

content_router = APIRouter(prefix="/api")


def _published_ts(rec: dict) -> float | None:
    """发布时间统一成秒级时间戳。各平台给的类型不一样，能解析就返回，不能就 None。"""
    v = rec.get("publishedAt")
    if v in (None, "", 0):
        return None
    if isinstance(v, (int, float)):
        n = float(v)
        return n / 1000 if n > 1e11 else n
    s = str(v).strip()
    if s.isdigit():
        n = float(s)
        return n / 1000 if n > 1e11 else n
    try:
        return datetime.fromisoformat(s[:19]).timestamp()
    except Exception:
        return None


SORT_FIELDS = {"like", "collect", "comment", "share", "collectRate",
               "commentRate", "shareRate", "relativeRank", "publishedAt"}


@content_router.get("/contents")
def list_contents(
    platform: str = "",
    keyword: str = "",
    batchId: str = "",
    structure: str = "",
    layer: str = "",
    q: str = "",
    minLike: int = 0,
    sort: str = "like",
    order: str = "desc",
    page: int = 1,
    pageSize: int = 50,
    starred: str = "",
    age: int = 0,
) -> dict:
    rows: list[dict] = []
    platforms = [platform] if platform in paths.PLATFORMS else list(paths.PLATFORMS)
    for p in platforms:
        items = store.read(f"contents/{p}").get("items", {})
        rows.extend(items.values())

    if keyword:
        rows = [r for r in rows if (r.get("source") or {}).get("keyword") == keyword]
    if batchId:
        rows = [r for r in rows if (r.get("source") or {}).get("batchId") == batchId]
    if structure:
        rows = [r for r in rows if (r.get("classification") or {}).get("structure") == structure]
    if layer:
        rows = [r for r in rows if (r.get("classification") or {}).get("layer") == layer]
    if starred in ("true", "1"):
        rows = [r for r in rows if (r.get("manual") or {}).get("starred")]

    if q:
        needle = q.lower()
        rows = [r for r in rows
                if needle in (r.get("title") or "").lower()
                or needle in (r.get("body") or "").lower()
                or any(needle in str(t).lower() for t in (r.get("tags") or []))]

    if minLike:
        rows = [r for r in rows if (r["metrics"].get("like") or 0) >= minLike]

    if age > 0:
        # age 单位天：只保留发布在 N 天以内的。发布日期缺失或未来的不淘汰（无法判断时不误杀）
        cutoff = time.time() - age * 86400
        rows = [r for r in rows if (pub := _published_ts(r)) is None or pub >= cutoff]

    key = sort if sort in SORT_FIELDS else "like"

    def sort_val(r: dict):
        if key in ("like", "collect", "comment", "share"):
            v = r["metrics"].get(key)
        elif key in ("collectRate", "commentRate", "shareRate"):
            v = r["metrics"].get(key)
        else:
            v = r.get(key)
        return (-1 if v is None else v) if order == "desc" else (10 ** 18 if v is None else v)

    rows.sort(key=sort_val, reverse=(order == "desc"))

    total = len(rows)
    start = max(0, (page - 1) * pageSize)
    page_rows = rows[start:start + pageSize]

    return {
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "items": [_brief(r) for r in page_rows],
        "facets": _facets(platforms),
    }


def _brief(r: dict) -> dict:
    m = r.get("metrics") or {}
    c = r.get("classification") or {}
    ts = _published_ts(r)
    return {
        "key": r.get("key"),
        "id": r.get("id"),
        "platform": r.get("platform"),
        "title": r.get("title"),
        "url": r.get("url"),
        "coverUrl": r.get("coverUrl"),
        "publishedAt": r.get("publishedAt"),
        "ageDays": None if ts is None else max(0, int((time.time() - ts) // 86400)),
        "metrics": {k: m.get(k) for k in ("like", "collect", "comment", "share", "play",
                                          "collectRate", "commentRate", "shareRate")},
        "metricsMissing": r.get("metricsMissing") or [],
        "structure": c.get("structure"),
        "structureName": normalize.STRUCTURE_NAMES.get(c.get("structure") or "", ""),
        "layer": c.get("layer"),
        "relativeRank": r.get("relativeRank"),
        "keyword": (r.get("source") or {}).get("keyword"),
        "starred": (r.get("manual") or {}).get("starred", False),
        "hasNote": bool((r.get("manual") or {}).get("note")),
    }


def _facets(platforms: list[str]) -> dict:
    kws: set[str] = set()
    batches: set[str] = set()
    count = 0
    for p in platforms:
        for r in store.read(f"contents/{p}").get("items", {}).values():
            count += 1
            src = r.get("source") or {}
            if src.get("keyword"):
                kws.add(src["keyword"])
            if src.get("batchId"):
                batches.add(src["batchId"])
    return {
        "total": count,
        "keywords": sorted(kws),
        "batches": sorted(batches, reverse=True),
    }


@content_router.get("/contents/{platform}/{content_id}")
def content_detail(platform: str, content_id: str) -> dict:
    items = store.read(f"contents/{platform}").get("items", {})
    rec = items.get(f"{platform}:{content_id}")
    if not rec:
        raise HTTPException(404, "内容不存在")
    return rec


@content_router.patch("/contents/{platform}/{content_id}")
def patch_content(platform: str, content_id: str, payload: dict = Body(...)) -> dict:
    name = f"contents/{platform}"
    key = f"{platform}:{content_id}"
    hit = {"rec": None}

    def _fn(data: dict) -> dict:
        rec = data.get("items", {}).get(key)
        if not rec:
            return data
        manual = {**rec.get("manual", {}), **(payload.get("manual") or {})}
        rec["manual"] = manual
        cls = rec.setdefault("classification", {})
        if "layer" in payload:
            cls["layer"] = payload["layer"]
            cls["by"] = "manual"
        hit["rec"] = rec
        return data

    store.update(name, _fn, note=f"patch content {key}")
    if not hit["rec"]:
        raise HTTPException(404, "内容不存在")
    return hit["rec"]


@content_router.delete("/contents/{platform}/{content_id}")
def delete_content(platform: str, content_id: str) -> dict:
    name = f"contents/{platform}"
    key = f"{platform}:{content_id}"
    hit = {"ok": False}

    def _fn(data: dict) -> dict:
        items = data.get("items", {})
        if key in items:
            items.pop(key)
            hit["ok"] = True
        return data

    store.update(name, _fn, note=f"delete content {key}")
    return {"ok": hit["ok"]}


# ------------------------------------------------------------------ 评论

@content_router.get("/contents/{platform}/{content_id}/comments")
def content_comments(platform: str, content_id: str, limit: int = 200,
                     sort: str = "like") -> dict:
    key = f"{platform}:{content_id}"
    bucket = store.read(f"comments/{platform}").get("items", {}).get(key)
    if not bucket:
        return {"contentKey": key, "items": [], "stats": {}, "groups": _empty_groups()}

    items = list(bucket.get("items") or [])
    if sort == "like":
        items.sort(key=lambda c: (-1 if c.get("like") is None else c["like"]), reverse=True)
    else:
        items.sort(key=lambda c: c.get("createdAt") or 0, reverse=True)

    return {
        "contentKey": key,
        "fetchedAt": bucket.get("fetchedAt"),
        "stats": bucket.get("stats") or {},
        "groups": group_comments(items),
        "items": items[:limit],
    }


def _empty_groups() -> dict:
    return {"ask": 0, "disagree": 0, "add": 0, "emotion": 0, "action": 0, "author": 0}


# 六类信号的规则关键词（照搬 xhs-comment-insight 的六个分型，规则为〔我方〕实现）
GROUP_RULES = {
    "ask": ("怎么", "为什么", "吗", "?", "？", "求", "能否", "哪个", "多少"),
    "disagree": ("不是", "不对", "反对", "错", "反驳", "然而", "其实不", "瞎说"),
    "add": ("补充", "还有", "另外", "其实", "我也", "确实", "同上", "＋", "+1"),
    "action": ("链接", "在哪买", "求链接", "收藏了", "码住", "去买", "怎么买", "下单"),
    "emotion": ("笑死", "哈哈哈", "太真实", "哭", "破防", "救命", "确实了", "懂我"),
}


def group_comments(items: list[dict]) -> dict:
    groups = _empty_groups()
    for c in items:
        text = str(c.get("content") or "")
        if c.get("parentId"):
            continue
        hit = False
        for name, kws in GROUP_RULES.items():
            if any(k in text for k in kws):
                groups[name] += 1
                hit = True
                break
        if not hit and len(text) >= 15:
            groups["add"] += 1
    return groups
