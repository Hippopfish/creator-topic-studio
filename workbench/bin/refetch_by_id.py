"""按笔记 ID 精确补采：把搜索排序变化后沉下去、搜不回来的笔记捞回来。

背景：早期批次因未开媒体下载被移出内容池，按关键词重采只召回约两成——
小红书搜索结果每次排序不同，老笔记会沉底。但按 ID 走 detail 模式不受排序影响，
只要笔记还在，就能连同图片一起抓回来。

用法（workbench 目录下，服务必须已启动）：
    python bin/refetch_by_id.py --dry            # 只看要补多少条
    python bin/refetch_by_id.py                  # 补采（默认每批 15 条，批间隔 25 秒）
    python bin/refetch_by_id.py --long-only      # 只补正文 >=60 字的（先保住干货）
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import chat, store  # noqa: E402
from bin import guard_client  # noqa: E402

BASE = guard_client.BASE
ARCH = HERE / "data" / "contents" / "archive"
BATCH = 15
GAP = 25          # 批间隔秒数：刚被风控过，宁可慢
TIMEOUT = 900


def _call(path: str, body: dict | None = None, method: str = "GET") -> dict:
    return guard_client.call(path, body, method)


def missing_ids(long_only: bool) -> tuple[list[str], dict]:
    """返回待补笔记的完整 URL。

    必须用完整 URL 而不是裸 note_id：小红书 detail 接口要 xsec_token，
    只给 ID 时 token 为空，接口直接拒绝（日志里是 xsec_token=''）。
    我们采集时把带 token 的 URL 存进了 url 字段，正好能拿来补。
    """
    snaps = sorted(ARCH.glob("xhs_full_*.json"))
    if not snaps:
        raise SystemExit("没有快照可比")
    old = (json.loads(snaps[-1].read_text(encoding="utf-8")).get("items") or {})
    cur = store.read("contents/xhs").get("items") or {}
    out = []
    for k, v in old.items():
        if v.get("media") or k in cur:
            continue
        if long_only and chat._net_chars(v.get("body")) < 60:
            continue
        url = (v.get("url") or "").strip()
        out.append(url if "xsec_token=" in url else k.split(":", 1)[1])
    return out, old


def refetch(ids: list[str]) -> dict:
    res = _call("/api/crawl/jobs", {"platform": "xhs", "crawlerType": "detail",
                                    "noteIds": ids, "withComments": True}, "POST")
    job_id, batch_id = res["jobId"], res["batchId"]
    t0 = time.time()
    while True:
        time.sleep(8)
        j = _call(f"/api/crawl/jobs/{job_id}")
        if j.get("status") != "running":
            break
        if time.time() - t0 > TIMEOUT:
            _call("/api/crawl/jobs/stop-current", {}, "POST")
            break
    try:
        st = _call(f"/api/crawl/batches/{batch_id}/import", {}, "POST")
    except Exception as e:
        st = {"error": str(e)}
    print(f"    → {j.get('status')} · 采到 {st.get('contents')} 条，新增 {st.get('added')}", flush=True)
    return st


def main() -> None:
    long_only = "--long-only" in sys.argv
    ids, _ = missing_ids(long_only)
    print(f"待补 {len(ids)} 条（{'仅长文' if long_only else '全部'}），每批 {BATCH} 条")
    if "--dry" in sys.argv or not ids:
        return

    total = 0
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        print(f"[{i // BATCH + 1}/{(len(ids) + BATCH - 1) // BATCH}] {len(chunk)} 条", flush=True)
        # 按 ID 补采走 detail 接口，不吃搜索日限额，但照样要吃组间冷却和风控刹车。
        # 原来的固定 sleep(GAP) 保留：动作 guard_client 里的最小间隔，两者取大。
        ok, why = guard_client.wait_gate(min_gap=GAP, search_limited=False)
        if not ok:
            print(f"    ! 停下：{why}", flush=True)
            break
        try:
            st = refetch(chunk)
            total += int(st.get("added") or 0)
        except guard_client.ApiError as e:
            print(f"    ! 被护栏拦下：{e}", flush=True)
            if e.code == 429:
                break
        except Exception as e:
            print(f"    ! {type(e).__name__}: {e}", flush=True)

    cur = store.read("contents/xhs").get("items") or {}
    with_media = sum(1 for v in cur.values() if v.get("media"))
    print(f"\n完成：累计新增 {total} 条；库内 {len(cur)} 条，有本地图片 {with_media} 条")


if __name__ == "__main__":
    main()
