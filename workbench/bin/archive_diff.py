"""对比「归档前的旧库快照」和当前主池，看重采捞回了多少。

用途：早期批次因未开媒体下载被移出内容池，重采之后要回答一个具体问题——
原来那些笔记，有多少条真的被重新采到了？没捞回来的都是哪些方向？

用法（workbench 目录下）：
    python bin/archive_diff.py                    # 拿最新快照比
    python bin/archive_diff.py <快照文件名>
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import store  # noqa: E402

ARCH = HERE / "data" / "contents" / "archive"


def main() -> None:
    snaps = sorted(ARCH.glob("xhs_full_*.json"))
    if not snaps:
        print("没有快照可比（data/contents/archive/xhs_full_*.json）")
        return
    name = sys.argv[1] if len(sys.argv) > 1 else snaps[-1].name
    snap = json.loads((ARCH / name).read_text(encoding="utf-8"))
    old = (snap.get("items") or {})

    cur = (store.read("contents/xhs").get("items") or {})
    old_nomedia = {k: v for k, v in old.items() if not v.get("media")}

    back = [k for k in old_nomedia if k in cur and cur[k].get("media")]
    found_nomedia = [k for k in old_nomedia if k in cur and not cur[k].get("media")]
    gone = [k for k in old_nomedia if k not in cur]

    print(f"快照 {name}：总 {len(old)} 条，其中无本地图 {len(old_nomedia)} 条")
    print(f"当前主池 {len(cur)} 条")
    print()
    print(f"  重采捞回（现在有图）: {len(back)}")
    print(f"  仍在库但没图        : {len(found_nomedia)}")
    print(f"  没再采到            : {len(gone)}")
    if old_nomedia:
        print(f"  召回率              : {len(back) / len(old_nomedia):.0%}")

    def kw(keys, src):
        return Counter((src[k].get("source") or {}).get("keyword") or "?" for k in keys)

    for label, keys in (("捞回", back), ("没再采到", gone)):
        if keys:
            print(f"\n{label} 按关键词：")
            for k, n in kw(keys, {**old, **cur}).most_common():
                print(f"  {n:>3}  {k}")


if __name__ == "__main__":
    main()
