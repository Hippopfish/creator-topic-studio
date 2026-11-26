"""把 refs/原文-75个标题公式.md 解析成 formula_library.json。

格式约定（原文实测）：
    ## 一、认知冲突型（反常识）        -> category
    ### 1. 为什么 [X] 其实对你有害？    -> id + template
    - 📌 原始爆款：「……」               -> example
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE.parent / "选题工作流" / "refs" / "原文-75个标题公式.md"
DST = BASE / "prompts" / "formula_library.json"

CAT_RE = re.compile(r"^##\s+(.+?)\s*$")
ITEM_RE = re.compile(r"^###\s+(\d+)\.\s*(.+?)\s*$")
PKG_RE = re.compile(r"📌\s*原始爆款[：:]\s*[「“\"](.+?)[」”\"]")


def main() -> int:
    if not SRC.exists():
        print(f"找不到源文件：{SRC}")
        return 1

    categories: list[str] = []
    items: list[dict] = []
    cat = ""
    cur: dict | None = None

    for line in SRC.read_text(encoding="utf-8").splitlines():
        m = CAT_RE.match(line)
        if m:
            raw = m.group(1)
            raw = re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", raw)
            cat = raw.strip()
            if cat not in categories:
                categories.append(cat)
            cur = None
            continue

        m = ITEM_RE.match(line)
        if m:
            cur = {
                "id": int(m.group(1)),
                "category": cat,
                "template": m.group(2).strip(),
                "example": "",
            }
            items.append(cur)
            continue

        if cur is not None and not cur["example"]:
            m = PKG_RE.search(line)
            if m:
                cur["example"] = m.group(1).strip()

    out = {
        "schemaVersion": 1,
        "source": "选题工作流/refs/原文-75个标题公式.md（原文照搬解析）",
        "categories": categories,
        "items": items,
    }
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"解析完成：{len(items)} 个公式，{len(categories)} 个类别")
    for c in categories:
        n = len([x for x in items if x["category"] == c])
        print(f"  {c}: {n}")
    missing = [x["id"] for x in items if not x["example"]]
    print(f"无原始爆款的条目：{missing or '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
