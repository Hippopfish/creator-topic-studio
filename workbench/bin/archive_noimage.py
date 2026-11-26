"""把「没有本地图片」的历史笔记从内容池移出，存成独立归档文件。

为什么要做：这批是 11-24/25 采的，当时采集没开 --get_media，图片只剩 URL，
而小红书图片链接 1-2 天就 403。对对话来说这批只剩标题 + 互动数 + 评论，
拿去做选题会被当成「读过的料」，污染判断。移出主池、单独存放，随时可按 ref 恢复。

用法：
  python bin/archive_noimage.py            # 真跑
  python bin/archive_noimage.py --dry      # 只看不写
"""
from __future__ import annotations

import json
import shutil
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import store  # noqa: E402


def main() -> None:
    dry = "--dry" in sys.argv
    d = store.read("contents/xhs")
    items = d.get("items") or {}

    keep, move = {}, {}
    for k, v in items.items():
        (keep if v.get("media") else move)[k] = v

    print(f"总 {len(items)} 条 → 保留(有本地图) {len(keep)} / 移出(无本地图) {len(move)}")
    batches = Counter((v.get("source") or {}).get("batchId") or "?" for v in move.values())
    for b, n in sorted(batches.items()):
        print(f"  {b}: {n} 条")
    if dry:
        return

    arch_dir = HERE / "data" / "contents" / "archive"
    arch_dir.mkdir(parents=True, exist_ok=True)

    # 1) 完整快照，出任何问题都能整份还原
    stamp = store.now_iso().replace(":", "").replace("-", "")[:13]
    snap = arch_dir / f"xhs_full_{stamp}.json"
    shutil.copy2(HERE / "data" / "contents" / "xhs.json", snap)
    print(f"完整快照 → {snap.name}")

    # 2) 被移出的记录单独存档，带来源批次和原因
    arch = arch_dir / "xhs_noimage.json"
    old = json.loads(arch.read_text(encoding="utf-8")) if arch.is_file() else {}
    merged = old.get("items") or {}
    for k, v in move.items():
        v = dict(v)
        v["archivedAt"] = store.now_iso()
        v["archivedReason"] = "noLocalImage"
        merged[k] = v
    arch.write_text(json.dumps({
        "archivedAt": store.now_iso(),
        "reason": "采集时未开媒体下载，图片链接已过期，正文/评论之外的证据不可得",
        "note": "需要恢复某条：把 items 里的记录搬回 contents/xhs.json 的 items 即可",
        "count": len(merged),
        "items": merged,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"归档 → {arch.name}（累计 {len(merged)} 条）")

    # 3) 主池只留有本地图的
    out = dict(d)
    out["items"] = keep
    store.write("contents/xhs", out, note=f"移出 {len(move)} 条无本地图笔记到 archive")
    print(f"主池 → {len(keep)} 条")

    # 4) 证据落在这批里的选题，标出来，别让它带着断链证据继续流转
    moved_keys = set(move)
    stale = []
    for t in store.list_items("topics"):
        refs = {e.get("ref") for e in (t.get("evidence") or []) if e.get("ref")}
        bad = refs & moved_keys
        if not bad:
            continue
        stale.append((t["id"], t.get("title"), len(bad), len(refs)))

        def fn(data, tid=t["id"], bad=bad):
            for x in data.get("items") or []:
                if x.get("id") == tid:
                    x["stale"] = True
                    x["staleReason"] = f"{len(bad)} 条证据素材已归档（无本地图片）"
            return data

        store.update("topics", fn, note=f"{t['id']} 证据素材归档，标记待重做")

    if stale:
        print("\n受影响的选题（证据素材已归档）：")
        for tid, title, nb, nr in stale:
            print(f"  {tid} | {title[:24]} | {nb}/{nr} 条证据断链")
    else:
        print("\n没有选题受影响")


if __name__ == "__main__":
    main()
