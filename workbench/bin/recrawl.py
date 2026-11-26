"""按关键词批量重采，把图片补进库里。

背景：早先采集没开 --get_media，187 条笔记只有图片 URL、没有文件；
而 xhscdn 的链接带签名时间戳，1–2 天后一律 403，事后再也补不回来。
所以只能重采一遍，图才会落到 data/media。

用法（workbench 目录下，服务必须已启动）：
    ..\\MediaCrawler\\.venv\\Scripts\\python.exe bin/recrawl.py
    ..\\MediaCrawler\\.venv\\Scripts\\python.exe bin/recrawl.py "INTP 拖延" "INTP 早八"

不传关键词时用库里出现过的词。串行跑（工作台全局只允许一个采集任务）。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from core import store  # noqa: E402
from bin import guard_client  # noqa: E402

BASE = guard_client.BASE
MAX_NOTES = int(os.environ.get("RECRAWL_MAX", "20"))
DEFAULT_KEYWORDS = ["MBTI", "INTP", "INTP 拖延", "INTP 二次元", "INTP 追番", "INTP 谷子"]


def _call(path: str, body: dict | None = None, method: str = "GET") -> dict:
    return guard_client.call(path, body, method)


def run(kw: str) -> dict:
    """起一组搜索采集。被护栏拦下（429）时交给 wait_gate 等，不再自己硬撞。"""
    for attempt in range(3):
        try:
            res = _call("/api/crawl/jobs", {"platform": "xhs", "keywords": [kw],
                                            "maxNotes": MAX_NOTES, "withComments": True,
                                            "sortType": "popularity_descending"}, "POST")
            break
        except guard_client.ApiError as e:
            if e.code != 429 or attempt == 2:
                raise
            ok, why = guard_client.wait_gate(min_gap=0)
            if not ok:
                raise SystemExit(f"护栏拦下：{why}")
    job_id, batch_id = res["jobId"], res["batchId"]
    print(f"  → {job_id}", flush=True)

    last = -1
    while True:
        time.sleep(8)
        j = _call(f"/api/crawl/jobs/{job_id}")
        if j.get("lineCount") != last:
            last = j.get("lineCount")
        st = j.get("status")
        if st != "running":
            break
        if time.time() - _started > 600:
            print("  ! 超过 10 分钟，跳过", flush=True)
            _call("/api/crawl/jobs/stop-current", {}, "POST")
            break

    stats = {}
    try:
        stats = _call(f"/api/crawl/batches/{batch_id}/import", {}, "POST")
    except Exception as e:
        print(f"  ! 导入失败 {e}", flush=True)
    print(f"  {st} · 内容 {stats.get('contents')} 条，新增 {stats.get('added')}，"
          f"评论 {stats.get('comments')}", flush=True)
    return {"job": job_id, "batch": batch_id, "status": st, **stats}


_started = 0.0

if __name__ == "__main__":
    kws = sys.argv[1:] or DEFAULT_KEYWORDS
    print(f"共 {len(kws)} 组：{'、'.join(kws)}", flush=True)
    out = []
    for i, kw in enumerate(kws, start=1):
        # 每组开跑前先过护栏：冷却没走完就等，额度见底就停。
        # 原来这里是裸跑，上一组刚结束就起下一组，正是 461 的触发条件。
        ok, why = guard_client.wait_gate()
        if not ok:
            print(f"\n[停在第 {i} 组] {why}", flush=True)
            break
        print(f"[{i}/{len(kws)}] {kw}", flush=True)
        _started = time.time()
        try:
            out.append({"kw": kw, **run(kw)})
        except SystemExit:
            raise
        except Exception as e:
            print(f"  ! {type(e).__name__}: {e}", flush=True)

    total = sum(int(x.get("added") or 0) for x in out)
    print(f"\n完成：{len(out)}/{len(kws)} 组，累计新增 {total} 条", flush=True)
    items = store.read("contents/xhs").get("items") or {}
    with_media = sum(1 for v in items.values() if v.get("media"))
    print(f"库内 {len(items)} 条，有本地图片 {with_media} 条", flush=True)
