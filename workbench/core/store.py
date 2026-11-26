"""JSON 存储层：原子写 + 单份 .bak + 追加流水 + ID 生成。

设计取舍：
- 数据量小（单平台几千条），整体读入内存、整体写回最简单可靠。
- 原子写用 os.replace（同盘 rename 是原子的），避免写一半断电留下坏文件。
- 每次写前把旧文件复制成 .bak，误操作可回退一步。
- _journal.ndjson 记录每次写操作的动作和时间，误删可回溯。
"""
from __future__ import annotations

import json
import os
import random
import shutil
import string
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable

from . import paths

_LOCK = threading.RLock()
_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    """t_20261123_1830_a1b2 这种，可读且天然按时间排序。"""
    ts = datetime.now(_TZ).strftime("%Y%m%d_%H%M")
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{prefix}_{ts}_{rand}"


def _default_for(name: str) -> dict:
    if name in ("topics", "drafts", "reviews", "insights", "batches", "account",
                "memos", "chats", "collectQueue", "stats/account", "stats/postshots"):
        return {"schemaVersion": 1, "items": []}
    if name.startswith("contents/") or name.startswith("comments/") or name.startswith("own/"):
        return {"schemaVersion": 1, "items": {}}
    if name == "profile/memory":
        return {"schemaVersion": 1, "items": []}
    return {"schemaVersion": 1}


def read(name: str, default: Any = None) -> dict:
    """读一个数据文件。不存在或损坏时返回默认结构，绝不抛异常。"""
    p = paths.data_file(name)
    with _LOCK:
        if not p.exists():
            return _default_for(name) if default is None else default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            bad = p.with_suffix(".corrupt")
            try:
                shutil.copy2(p, bad)
            except Exception:
                pass
            return _default_for(name) if default is None else default


def write(name: str, data: dict, note: str = "") -> None:
    """原子写：tmp -> bak -> replace。note 会记进 journal。"""
    p = paths.data_file(name)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if p.exists():
            try:
                shutil.copy2(p, p.with_suffix(p.suffix + ".bak"))
            except Exception:
                pass
        os.replace(tmp, p)
    _journal("write", {"target": name, "note": note})


def update(name: str, fn: Callable[[dict], dict], note: str = "") -> dict:
    """读-改-写，期间加锁。fn 收到当前数据、返回新数据。"""
    with _LOCK:
        data = read(name)
        new = fn(data)
        write(name, new, note=note)
        return new


def _journal(action: str, payload: dict) -> None:
    """追加一行流水。失败不影响主流程。"""
    try:
        rec = {"at": now_iso(), "action": action, **payload}
        with paths.JOURNAL_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def journal(action: str, payload: dict) -> None:
    _journal(action, payload)


# ---------------------------------------------------------------- 便捷封装

def list_items(name: str) -> list[dict]:
    return read(name).get("items", [])


def upsert_item(name: str, item: dict, id_field: str = "id", note: str = "") -> dict:
    def _fn(data: dict) -> dict:
        items = data.setdefault("items", [])
        for i, old in enumerate(items):
            if old.get(id_field) == item.get(id_field):
                items[i] = {**old, **item}
                return data
        items.insert(0, item)
        return data

    update(name, _fn, note=note or f"upsert {name}")
    return item


def remove_item(name: str, item_id: str, id_field: str = "id", note: str = "") -> bool:
    hit = {"ok": False}

    def _fn(data: dict) -> dict:
        items = data.setdefault("items", [])
        kept = [x for x in items if x.get(id_field) != item_id]
        hit["ok"] = len(kept) != len(items)
        data["items"] = kept
        return data

    update(name, _fn, note=note or f"remove {item_id} from {name}")
    return hit["ok"]


def settings() -> dict:
    """读设置，缺字段用默认值补齐（保证前端永远拿到完整结构）。"""
    s = read("settings")
    base = {
        "ai": {
            "mode": "prompt",
            "baseUrl": "",
            "model": "",
            "thinking": False,
            "temperature": 0.6,
            "timeoutSec": 180,
            "jsonMode": True,
        },
        "crawler": {
            "chromePath": "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
            "cdpPort": 9222,
            # 按角色分端口：两个号各自独立 profile，同时只能开一个
            "cdpPortByRole": {"own": 9222, "crawl": 9223},
            "maxNotes": 20,
            "withComments": True,
            "withSubComments": False,
            "maxJobMinutes": 30,
            # 采集护栏（core/crawl_guard.py）。默认值来自实测：461 是密度触发，
            # 上一组结束后 3 秒就开下一组必撞；当天连采 318 条本身没被拦。
            "guardCooldownSec": 60,      # 组间接力冷却
            "guardDailySearch": 300,     # 搜索类一天最多采多少条
            "guardRiskStrikes": 2,       # 连续撞几次风控就刹车
            "guardRiskCooldownMin": 30,  # 刹车多久
        },
        "thresholds": {
            "collect": 0.5,
            "comment": 0.04,
            "share": 0.08,
            "shareCollectMax": 0.3,
            "calibratedAt": None,
        },
        "ui": {"defaultPlatform": "xhs", "pageSize": 50, "autoBackup": True},
    }
    for k, v in base.items():
        if not isinstance(s.get(k), dict):
            s[k] = v
        else:
            for kk, vv in v.items():
                s[k].setdefault(kk, vv)
    return s


def save_settings(patch: dict) -> dict:
    cur = settings()
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(cur.get(k), dict):
            cur[k].update(v)
        else:
            cur[k] = v
    write("settings", cur, note="save settings")
    return cur


def secret() -> str:
    return str(read("secrets").get("openaiApiKey") or "")


def save_secret(key: str) -> None:
    write("secrets", {"schemaVersion": 1, "openaiApiKey": (key or "").strip()},
          note="save secret")


def mask_key(key: str) -> str:
    if not key:
        return ""
    return f"{key[:3]}****{key[-4:]}" if len(key) > 8 else "****"
