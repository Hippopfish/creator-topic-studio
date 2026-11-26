"""两个补采脚本（recrawl / refetch_by_id）共用的护栏客户端。

为什么不各写一套等待逻辑：判断「还能不能采」是服务端的事，规则只在
core/crawl_guard.py 里定义一次。脚本这边只做两件事：

1. 开跑前问一句还能不能跑（GET /api/crawl/guard）；
2. 被拦了就按它给的秒数等，而不是自己每 8 秒硬撞一次。

否则脚本一套节奏、队列另一套节奏，两边各自试探，等于没护栏。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8737"


class ApiError(RuntimeError):
    """带 HTTP 状态码的调用错误，429 = 被护栏拦下。"""

    def __init__(self, msg: str, code: int = 0):
        super().__init__(msg)
        self.code = code


def call(path: str, body: dict | None = None, method: str = "GET",
         timeout: int = 120) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(raw).get("detail") or raw
        except Exception:
            msg = raw
        raise ApiError(str(msg)[:300], e.code) from None


def snapshot() -> dict:
    try:
        return call("/api/crawl/guard")
    except Exception:
        return {}


def budget_left() -> int:
    """今天搜索类还能采多少条。没配上限时返回一个大数。"""
    g = snapshot()
    cap = int((g.get("limits") or {}).get("dailySearch") or 0)
    if not cap:
        return 10 ** 9
    used = int((g.get("today") or {}).get("search") or 0)
    return cap - used


def wait_gate(min_gap: int = 0, max_wait: int = 180,
              search_limited: bool = True) -> tuple[bool, str]:
    """等到可以开跑为止。返回 (能不能继续, 原因)。

    min_gap：脚本自己想要的额外间隔（比如补采脚本原本就有的 25 秒），
             取它和护栏冷却的较大值。max_wait：等超过这么久就放弃——
             半小时的刹车脚本不该干等，交给用户决定。
    search_limited：这次采集算不算搜索类。按 ID 补采（detail）不吃日限额。
    """
    g = snapshot()
    if not g:
        return True, ""

    risk = int((g.get("risk") or {}).get("leftSec") or 0)
    if risk > max_wait:
        return False, (f"风控刹车中，还要 {risk // 60} 分钟才解禁。"
                       f"先停下，别硬撞（可在采集台点「解除刹车」）")
    if risk > 0:
        print(f"  … 风控刹车中，等 {risk} 秒", flush=True)
        time.sleep(risk + 2)

    left = int((g.get("cooldown") or {}).get("leftSec") or 0)
    wait = max(left, int(min_gap or 0))
    if wait > 0:
        print(f"  … 组间冷却，等 {wait} 秒", flush=True)
        time.sleep(wait + 1)

    if search_limited and budget_left() < 20:
        t = g.get("today") or {}
        lim = g.get("limits") or {}
        return False, (f"今天搜索类已采 {t.get('search')} / {lim.get('dailySearch')} 条，"
                       f"额度见底。明天再补，或按 ID 补采（detail 不受此限）")
    return True, ""
