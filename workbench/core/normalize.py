"""三平台原始数据 → 统一结构。

字段名全部照 store/*/__init__.py 的实际落盘字段写（不是照 README 或 SOP 表）。
关键原则：**缺失就是未知，绝不补 0**——补 0 会让"互动率低"这种结论变成假数据。
"""
from __future__ import annotations

import re

PLATFORM_ID_FIELD = {"xhs": "note_id", "dy": "aweme_id", "bili": "video_id"}

_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([万亿千wWkK]?)")
_UNIT = {"万": 1e4, "w": 1e4, "W": 1e4, "亿": 1e8, "千": 1e3, "k": 1e3, "K": 1e3}


def parse_count(v) -> int | None:
    """'1.2万' / '1,234' / 1234 / '' / None -> int | None。无法解析返回 None。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    if not s or s in ("-", "—", "暂无", "null", "None"):
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    num = float(m.group(1)) * _UNIT.get(m.group(2), 1)
    return int(num)


def _rate(num: int | None, den: int | None) -> float | None:
    if num is None or den is None or den <= 0:
        return None
    return round(num / den, 4)


def _clean(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return " ".join(str(x) for x in v if x)
    return str(v).strip()


def normalize(platform: str, raw: dict, batch_id: str, keyword: str = "") -> dict | None:
    """一条原始记录 -> 统一结构。缺主键返回 None（调用方计为坏行）。"""
    pid_field = PLATFORM_ID_FIELD.get(platform)
    if not pid_field:
        return None
    rid = _clean(raw.get(pid_field))
    if not rid:
        return None

    if platform == "xhs":
        title = _clean(raw.get("title"))
        body = _clean(raw.get("desc"))
        url = _clean(raw.get("note_url"))
        cover = _first(raw.get("image_list"))
        images = _as_list(raw.get("image_list"))
        video = _clean(raw.get("video_url"))
        tags = _as_list(raw.get("tag_list"))
        published = raw.get("time")
        m = {
            "like": parse_count(raw.get("liked_count")),
            "collect": parse_count(raw.get("collected_count")),
            "comment": parse_count(raw.get("comment_count")),
            "share": parse_count(raw.get("share_count")),
            "play": None,
            "coin": None,
            "danmaku": None,
        }
    elif platform == "dy":
        title = _clean(raw.get("title"))
        body = _clean(raw.get("desc"))
        url = _clean(raw.get("aweme_url"))
        cover = _clean(raw.get("cover_url"))
        images = []
        video = _clean(raw.get("video_download_url"))
        tags = []
        published = raw.get("create_time")
        m = {
            "like": parse_count(raw.get("liked_count")),
            "collect": parse_count(raw.get("collected_count")),
            "comment": parse_count(raw.get("comment_count")),
            "share": parse_count(raw.get("share_count")),
            "play": None,
            "coin": None,
            "danmaku": None,
        }
    else:  # bili
        title = _clean(raw.get("title"))
        body = _clean(raw.get("desc"))
        url = _clean(raw.get("video_url"))
        cover = _clean(raw.get("video_cover_url"))
        images = []
        video = ""
        tags = []
        published = raw.get("create_time")
        m = {
            "like": parse_count(raw.get("liked_count")),
            "collect": parse_count(raw.get("video_favorite_count")),
            "comment": parse_count(raw.get("video_comment")),
            "share": parse_count(raw.get("video_share_count")),
            "play": parse_count(raw.get("video_play_count")),
            "coin": parse_count(raw.get("video_coin_count")),
            "danmaku": parse_count(raw.get("video_danmaku")),
        }

    # 抖音/B站没有独立标题时，用正文首句兜底（抖音 title 就是 desc 的场景）
    if not title and body:
        title = body[:60]

    missing = [k for k, v in m.items() if v is None and k in ("like", "collect", "comment", "share")]

    m["collectRate"] = _rate(m["collect"], m["like"])
    m["commentRate"] = _rate(m["comment"], m["like"])
    m["shareRate"] = _rate(m["share"], m["like"])

    return {
        "key": f"{platform}:{rid}",
        "id": rid,
        "platform": platform,
        "title": title,
        "body": body,
        "author": {
            "hash": _clean(raw.get("creator_hash")),
            "nicknameMasked": _clean(raw.get("nickname")),
        },
        "publishedAt": published,
        "url": url,
        "coverUrl": cover,
        "images": images,
        "videoUrl": video,
        "tags": tags,
        "metrics": m,
        "metricsMissing": missing,
        "classification": {"structure": "unknown", "layer": None, "by": "rule"},
        # 优先用原始数据里带的来源关键词：同一批次可能搜了多个词，
        # 批次级的 keyword 只是第一个词，用它会把后面的词全部标错。
        "source": {"keyword": _clean(raw.get("source_keyword")) or keyword,
                   "batchId": batch_id, "rankInSearch": None},
        "relativeRank": None,
        "observations": [],
        "manual": {"starred": False, "tags": [], "note": ""},
        "raw": raw,
    }


def classify(metrics: dict, th: dict) -> str:
    """四类互动结构。任一核心指标缺失 → unknown（不许当"低互动"判）。"""
    for k in ("like", "collect", "comment", "share"):
        if metrics.get(k) is None:
            return "unknown"
    like = metrics["like"] or 0
    if like <= 0:
        return "unknown"
    cr = metrics.get("collectRate") or 0
    mr = metrics.get("commentRate") or 0
    sr = metrics.get("shareRate") or 0
    if cr >= float(th.get("collect", 0.5)):
        return "collect"
    if mr >= float(th.get("comment", 0.04)):
        return "debate"
    if sr >= float(th.get("share", 0.08)) and cr < float(th.get("shareCollectMax", 0.3)):
        return "resonance"
    return "pass_by"


STRUCTURE_NAMES = {
    "collect": "收藏型",
    "debate": "争论型",
    "resonance": "共鸣型",
    "pass_by": "路过型",
    "unknown": "未知",
}


def snapshot(rec: dict) -> dict:
    return {
        "at": rec.get("lastSeenAt"),
        "like": rec["metrics"].get("like"),
        "collect": rec["metrics"].get("collect"),
        "comment": rec["metrics"].get("comment"),
        "share": rec["metrics"].get("share"),
    }


def _first(v) -> str:
    lst = _as_list(v)
    return lst[0] if lst else ""


def _as_list(v) -> list[str]:
    """多值字段 -> 列表，**字符串也要按逗号切开**。

    MediaCrawler 落 jsonl 时把 image_list / tag_list 写成逗号分隔的字符串
    （store/xhs/__init__.py 的 `','.join(...)`）。早先这里只认 list，
    结果：多图笔记的全部图片被压成一个 1387 字的假 URL（前端封面直接挂），
    标签也变成一个长串，筛选和展示全部失效。
    """
    if not v:
        return []
    items = v if isinstance(v, list) else [v]
    out: list[str] = []
    for x in items:
        if x is None:
            continue
        for part in str(x).split(","):
            p = part.strip()
            if p:
                out.append(p)
    return out
