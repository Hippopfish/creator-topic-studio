"""一次性修复：把被压成字符串的 images / tags 从 raw 里重新拆开。

背景：MediaCrawler 的 xhs store 把 image_list、tag_list 写成逗号分隔的字符串。
normalize 早先只认 list，于是
  - 107 条多图笔记的全部图片被拼成一个超长假 URL（前端封面全挂）
  - 179 条的标签变成一个长串（筛选、展示、搜索全废）

normalize 已修，但已入库的记录不会自动重算，所以用这个脚本按 raw 回填。
幂等：可以反复跑。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import normalize, store  # noqa: E402


def main() -> int:
    data = store.read("contents/xhs")
    items: dict = data.get("items") or {}
    fixed_imgs = fixed_tags = 0
    for _, rec in items.items():
        raw = rec.get("raw") or {}
        if not raw:
            continue
        imgs = normalize._as_list(raw.get("image_list"))
        tags = normalize._as_list(raw.get("tag_list"))

        if imgs and imgs != (rec.get("images") or []):
            rec["images"] = imgs
            rec["coverUrl"] = imgs[0]
            if len(imgs) > 1:
                fixed_imgs += 1
        if tags and tags != (rec.get("tags") or []):
            rec["tags"] = tags
            fixed_tags += 1

    store.write("contents/xhs", data, note="fix: split images/tags from raw")
    print(f"多图被压平的：{fixed_imgs} 条")
    print(f"标签被压平的：{fixed_tags} 条")

    total = len(items)
    multi = sum(1 for r in items.values() if len(r.get("images") or []) > 1)
    cover_bad = sum(1 for r in items.values() if len(r.get("coverUrl") or "") > 300)
    print(f"库内 {total} 条，其中多图 {multi} 条，封面 URL 异常 {cover_bad} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
