"""把 MediaCrawler 的 jsonl 导入成工作台统一结构。

去重键：xhs:note_id / dy:aweme_id / bili:video_id。
重复导入同一条：只更新互动数据，新增一条 observation 记录变化；指标没变则计为"重复"。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import normalize, paths, store

# 媒体落地根目录：data/media/<platform>/<笔记ID>/001.webp
# 不能留在 data_raw/<批次>/ 里——批次目录是采集的临时产物，会被清理；
# 更要紧的是原图链接 1–2 天就失效，文件是唯一还能用的证据。
MEDIA_ROOT = paths.DATA_DIR / "media"


def scan_raw(batch: dict) -> dict:
    """列出某批次目录下的 jsonl 文件，按 contents / comments / 其他 分类。"""
    raw_dir = Path(batch.get("rawDir") or "")
    out = {"contents": [], "comments": [], "missing": not raw_dir.exists()}
    if not raw_dir.exists():
        return out
    for f in sorted(raw_dir.rglob("*.jsonl")):
        name = f.name.lower()
        if "comment" in name:
            out["comments"].append(f)
        elif "content" in name:
            out["contents"].append(f)
    return out


def import_batch(batch_id: str, replace: bool = False) -> dict:
    batch = _find_batch(batch_id)
    if not batch:
        return {"ok": False, "message": "批次不存在"}

    platform = batch["platform"]
    # creator 批次 = 自己的账号，落到 own/ 库；search 批次 = 赛道样本，落到 contents/。
    # 两者必须分开：混库会让选题把自己的旧作当成竞品样本。
    is_own = (batch.get("crawlerType") or "search") == "creator"
    keyword = (batch.get("creatorId") or "自己账号") if is_own else (batch.get("keywords") or [""])[0]
    files = scan_raw(batch)
    if files["missing"]:
        return {"ok": False, "message": f"原始目录不存在：{batch.get('rawDir')}"}

    stats = {"contents": 0, "comments": 0, "added": 0, "updated": 0,
             "duplicated": 0, "errors": 0, "target": "own" if is_own else "sample"}

    # ---------------- 内容
    store_name = f"own/{platform}" if is_own else f"contents/{platform}"
    data = store.read(store_name)
    items: dict = data.setdefault("items", {})
    if replace:
        items.clear()

    now = store.now_iso()
    th = store.settings().get("thresholds", {})
    batch_keys: list[str] = []

    for f in files["contents"]:
        for line in _iter_jsonl(f):
            stats["contents"] += 1
            rec = normalize.normalize(platform, line, batch_id, keyword)
            if not rec:
                stats["errors"] += 1
                continue
            key = rec["key"]
            batch_keys.append(key)
            _attach_media(rec, platform, Path(batch.get("rawDir") or ""))
            rec["classification"]["structure"] = normalize.classify(rec["metrics"], th)
            rec["classification"]["thresholdsSnapshot"] = {
                k: th.get(k) for k in ("collect", "comment", "share", "shareCollectMax")
            }

            old = items.get(key)
            if old is None:
                rec["firstSeenAt"] = rec["lastSeenAt"] = now
                rec["seenCount"] = 1
                rec["observations"] = [normalize.snapshot({**rec, "lastSeenAt": now})]
                items[key] = rec
                stats["added"] += 1
            else:
                same = all(
                    old.get("metrics", {}).get(k) == rec["metrics"].get(k)
                    for k in ("like", "collect", "comment", "share")
                )
                old["metrics"] = {**old.get("metrics", {}), **rec["metrics"]}
                old["metricsMissing"] = rec["metricsMissing"]
                old["classification"]["structure"] = rec["classification"]["structure"]
                old["classification"]["thresholdsSnapshot"] = rec["classification"]["thresholdsSnapshot"]
                old["lastSeenAt"] = now
                old["seenCount"] = int(old.get("seenCount") or 1) + 1
                old["raw"] = rec["raw"]
                # 标题/正文可能被作者改过，以最新为准
                for fld in ("title", "body", "url", "coverUrl", "videoUrl", "tags", "publishedAt",
                            "images", "media"):
                    if rec.get(fld):
                        old[fld] = rec[fld]
                if same:
                    stats["duplicated"] += 1
                else:
                    old.setdefault("observations", []).append(
                        normalize.snapshot({**rec, "lastSeenAt": now})
                    )
                    stats["updated"] += 1

    _recompute_rank(items, batch_keys)
    store.write(store_name, data, note=f"import {batch_id} contents")

    # ---------------- 评论
    if files["comments"]:
        cname = f"comments/{platform}"
        cdata = store.read(cname)
        by_key: dict = cdata.setdefault("items", {})
        for f in files["comments"]:
            for line in _iter_jsonl(f):
                stats["comments"] += 1
                c = _norm_comment(platform, line)
                if not c:
                    stats["errors"] += 1
                    continue
                bucket = by_key.setdefault(
                    c["contentKey"],
                    {"contentKey": c["contentKey"], "fetchedAt": now,
                     "batchId": batch_id, "items": [], "stats": {}},
                )
                if not any(x["id"] == c["id"] for x in bucket["items"]):
                    bucket["items"].append(c)
        for bucket in by_key.values():
            bucket["stats"] = _comment_stats(bucket["items"])
        store.write(cname, cdata, note=f"import {batch_id} comments")

    # ---------------- 有效率
    # 有效率的含义：这次采回来的行里，有多少是库里原本没有的。
    # 偏低说明采集窗口和上一次重叠太多（多半是排序没换、或关键词重复）。
    stats["repeated"] = stats["updated"] + stats["duplicated"]
    stats["efficiency"] = (
        round(stats["added"] / stats["contents"], 4) if stats["contents"] else None
    )

    # ---------------- 批次回写
    # 重导一次不能把第一次的结果抹掉：第二次必然全是重复，added=0、效率=0%，
    # 采集台就会显示「0 · 效 0%」，看起来像采集失败（实际已经进了 58 条）。
    # 所以留一份历史，累计新增按历史求和，有效率取第一次。
    hist = list(batch.get("importHistory") or [])
    hist.append({"at": now, "added": stats["added"], "updated": stats["updated"],
                 "duplicated": stats["duplicated"], "contents": stats["contents"],
                 "efficiency": stats["efficiency"]})
    batch["importHistory"] = hist[-5:]
    batch["importTotalAdded"] = sum(int(h.get("added") or 0) for h in batch["importHistory"])
    batch["firstEfficiency"] = batch["importHistory"][0].get("efficiency")
    batch["importTimes"] = len(batch["importHistory"])
    batch["importedAt"] = now
    batch["importStats"] = stats
    store.upsert_item("batches", batch, note="mark imported")

    return {"ok": True, **stats}


def _attach_media(rec: dict, platform: str, raw_root: Path) -> None:
    """把批次目录里下好的图片搬进 data/media/<platform>/<笔记ID>/。

    采集器按 {SAVE_DATA_PATH}/{platform}/media/{内容ID}/ 落盘，
    SAVE_DATA_PATH 是我们给的批次目录，所以这批文件目前躺在 data_raw 里。
    data_raw 是临时产物，会被清理，而原图链接一两天就失效——
    所以必须搬出来存成长期资产，不能让选题依赖一个会过期的 URL。
    """
    src_dir = raw_root / platform / "media" / str(rec["id"])
    if not src_dir.is_dir():
        return

    dst_dir = MEDIA_ROOT / platform / str(rec["id"])
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    files: list[str] = []
    total = 0
    for f in sorted(src_dir.iterdir()):
        if not f.is_file():
            continue
        dst = dst_dir / f.name
        try:
            if not (dst.exists() and dst.stat().st_size == f.stat().st_size):
                shutil.copy2(f, dst)
            files.append(f.name)
            total += dst.stat().st_size
        except OSError:
            continue

    if not files:
        return
    rec["media"] = {
        "dir": str(dst_dir.relative_to(paths.DATA_DIR.parent)).replace("\\", "/"),
        "files": files,
        "count": len(files),
        "bytes": total,
        "fetchedAt": store.now_iso(),
    }


def _recompute_rank(items: dict, keys: list[str]) -> None:
    """同批同关键词内按点赞算分位（0-1）。跨平台比较只允许用这个。"""
    groups: dict[str, list[str]] = {}
    for k in keys:
        it = items.get(k)
        if not it:
            continue
        kw = (it.get("source") or {}).get("keyword") or ""
        groups.setdefault(kw, []).append(k)

    for _, ks in groups.items():
        valid = [items[k] for k in ks if isinstance(items[k]["metrics"].get("like"), int)]
        if len(valid) < 2:
            for k in ks:
                items[k]["relativeRank"] = 1.0 if valid else None
            continue
        likes = sorted(x["metrics"]["like"] for x in valid)
        n = len(likes)
        import bisect

        for x in valid:
            pos = bisect.bisect_right(likes, x["metrics"]["like"])
            x["relativeRank"] = round(pos / n, 3)


def _norm_comment(platform: str, raw: dict) -> dict | None:
    cid = raw.get("comment_id") or raw.get("rpid") or raw.get("id")
    if not cid:
        return None
    pid_field = normalize.PLATFORM_ID_FIELD.get(platform, "")
    content_id = raw.get(pid_field) or raw.get("note_id") or raw.get("aweme_id") or raw.get("video_id")
    if not content_id:
        return None

    parent = raw.get("parent_comment_id")
    if parent in ("0", 0):
        parent = ""

    return {
        "id": str(cid),
        "contentKey": f"{platform}:{content_id}",
        "parentId": str(parent or ""),
        "content": str(raw.get("content") or "").strip(),
        "like": normalize.parse_count(raw.get("like_count")),
        "createdAt": raw.get("create_time"),
        "nicknameMasked": str(raw.get("nickname") or ""),
        "pictures": raw.get("pictures") or [],
        "subCount": normalize.parse_count(raw.get("sub_comment_count")) or 0,
    }


def _comment_stats(items: list[dict]) -> dict:
    likes = [c["like"] for c in items if isinstance(c.get("like"), int)]
    return {
        "total": len(items),
        "topLevel": len([c for c in items if not c.get("parentId")]),
        "maxLike": max(likes) if likes else None,
    }


def _iter_jsonl(path: Path):
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except Exception:
        return


def _find_batch(batch_id: str) -> dict | None:
    for b in store.list_items("batches"):
        if b.get("id") == batch_id:
            return b
    return None
