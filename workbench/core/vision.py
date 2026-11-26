"""按需读图：只有在真要用到某篇笔记时，才让模型去看它的图。

对齐原版的两段式：
  1. 采集时把图存到本地（importer._attach_media 负责，因为链接一两天就失效）
  2. 要用时（read_note）才把本地图交给模型，读完缓存回记录

**不做全库预读**。预读有两个坏处：钱花在没人会看的 170 条上；识别结果一旦提前
写进库，后面所有环节都会把它当原文引用，而它其实是模型读的二手信息。

缓存不是预读：同一篇第二次被读到时直接命中，不重复花钱。
"""
from __future__ import annotations

import base64
import io
import time
from pathlib import Path

import httpx

from . import paths, store

# 一次最多读 12 张。实测清单型图集（「各MBTI的怪癖」17 张）每页都是干货，
# 只读前 6 张会漏掉大半内容；每张约 700–1024 输入 token，12 张也就一分钱。
MAX_IMAGES = 12

# 竖版超长图（备忘录、清单长截图）整张送进去会被压到 1300×1300 等效像素，
# 小字直接糊掉。超过这个高宽比就先上下切两半，各自保证有效分辨率。
SPLIT_RATIO = 2.0

_PROMPT = (
    "这些是同一篇小红书笔记里的图片，按顺序编号。请逐张把图上出现的文字抄下来，"
    "按从上到下、从左到右的阅读顺序换行。\n"
    "要求：\n"
    "1. 只抄字，不要描述画面、不要总结、不要评价；\n"
    "2. 用「### 图1」这样的标题分隔每张图，序号与传入顺序一致；\n"
    "3. 看不清的字写 [不清]，不要猜；\n"
    "4. 图里没有文字就写「（无文字）」，不要编。"
)


def _media_dir(rec: dict) -> Path | None:
    m = rec.get("media") or {}
    d = str(m.get("dir") or "").strip()
    if not d:
        return None
    p = Path(d)
    if not p.is_absolute():
        p = paths.DATA_DIR.parent / p
    return p if p.is_dir() else None


def _encode(path: Path) -> list[tuple[str, str]]:
    """一个文件 -> [(标签, data URL)]。长图切两半，返回两条。"""
    try:
        from PIL import Image
    except Exception:
        Image = None  # 没有 PIL 就整张送，不切

    if Image is None:
        raw = path.read_bytes()
        return [(path.name, "data:image/jpeg;base64," + base64.b64encode(raw).decode())]

    try:
        with Image.open(path) as im:
            im.load()
            w, h = im.size
            if h / max(w, 1) <= SPLIT_RATIO:
                buf = io.BytesIO()
                im.convert("RGB").save(buf, format="JPEG", quality=88)
                return [(path.name, "data:image/jpeg;base64,"
                         + base64.b64encode(buf.getvalue()).decode())]

            half = h // 2
            out: list[tuple[str, str]] = []
            for i, box in enumerate(((0, 0, w, half), (0, half, w, h)), start=1):
                piece = im.crop(box).convert("RGB")
                buf = io.BytesIO()
                piece.save(buf, format="JPEG", quality=88)
                out.append((f"{path.stem}·{i}", "data:image/jpeg;base64,"
                            + base64.b64encode(buf.getvalue()).decode()))
            return out
    except Exception:
        try:
            raw = path.read_bytes()
            return [(path.name, "data:image/jpeg;base64," + base64.b64encode(raw).decode())]
        except Exception:
            return []


def _collect(rec: dict) -> list[tuple[str, str]]:
    d = _media_dir(rec)
    if not d:
        return []
    m = rec.get("media") or {}
    names = [n for n in (m.get("files") or []) if str(n).lower().endswith(
        (".webp", ".jpg", ".jpeg", ".png", ".gif"))]
    blocks: list[tuple[str, str]] = []
    for name in names:
        if len(blocks) >= MAX_IMAGES:
            break
        p = d / name
        if not p.is_file():
            continue
        blocks.extend(_encode(p))  # 长图会变成两块，允许略超上限
    return blocks


def _signature(rec: dict) -> str:
    m = rec.get("media") or {}
    return "|".join(f"{n}:{m.get('bytes')}" for n in (m.get("files") or [])) + f"|{m.get('fetchedAt')}"


def read(rec: dict, force: bool = False) -> dict:
    """读一篇笔记的图，返回给模型看的结果。缓存优先。

    返回结构直接拼进 read_note 的响应，字段名用中文说明状态，避免模型误读成正文。
    """
    cache = rec.get("imageRead") or {}
    sig = _signature(rec)
    if not force and cache.get("text") and cache.get("signature") == sig:
        return {
            "imageText": cache["text"],
            "imageTextSource": "模型读图",
            "imageTextCached": True,
            "imageReadAt": cache.get("at"),
            "imageTextNote": "这是模型从图上抄下来的字，不是作者原文，引用时请说明「图上写着」。",
        }

    blocks = _collect(rec)
    if not blocks:
        return {
            "imageText": "",
            "imageTextSource": "",
            "imageReadError": "这篇没有本地图片（早批次的图没下载，原始链接已过期），只能看正文和评论。",
        }

    s = store.settings().get("ai") or {}
    key = store.secret()
    base = str(s.get("baseUrl") or "").rstrip("/")
    if not key or not base:
        return {"imageText": "", "imageTextSource": "", "imageReadError": "没有配置模型密钥，无法读图。"}

    content: list[dict] = [{"type": "text", "text": _PROMPT}]
    for idx, (label, data_url) in enumerate(blocks, start=1):
        content.append({"type": "text", "text": f"（第 {idx} 张，文件名 {label}）"})
        content.append({"type": "image_url", "image_url": {"url": data_url}})

    payload = {
        "model": s.get("model"),
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 2400,
        "temperature": 0,
        # 不关思考会 200 但返回空：输出 token 全烧在推理上，撞 max_tokens 就没了
        "thinking": {"type": "disabled"},
    }

    t0 = time.time()
    tries = [payload, {k: v for k, v in payload.items() if k != "thinking"}]
    last = ""
    for i, body in enumerate(tries):
        try:
            with httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0)) as c:
                r = c.post(f"{base}/chat/completions",
                           headers={"Authorization": f"Bearer {key}"}, json=body)
            if r.status_code >= 400:
                last = f"HTTP {r.status_code}"
                if i == 0:
                    continue  # 有的网关不认 thinking 字段，去掉再试一次
                break
            msg = ((r.json().get("choices") or [{}])[0] or {}).get("message") or {}
            text = str(msg.get("content") or "").strip()
            if not text:
                last = "模型返回空"
                break
            used = r.json().get("usage") or {}
            elapsed = round(time.time() - t0, 1)

            def _fn(data: dict) -> dict:
                it = (data.get("items") or {}).get(rec.get("key"))
                if it:
                    it["imageRead"] = {
                        "text": text, "at": store.now_iso(),
                        "model": s.get("model"), "signature": sig,
                        "images": len(blocks), "seconds": elapsed,
                        "tokens": {"in": used.get("prompt_tokens"), "out": used.get("completion_tokens")},
                    }
                return data

            try:
                store.update(f"contents/{rec.get('platform') or 'xhs'}", _fn, note="vision read")
            except Exception:
                pass

            return {
                "imageText": text[:4000],
                "imageTextSource": "模型读图",
                "imageTextCached": False,
                "imageCount": len(blocks),
                "imageReadAt": store.now_iso(),
                "imageTextNote": "这是模型从图上抄下来的字，不是作者原文，引用时请说明「图上写着」。",
            }
        except Exception as e:
            last = type(e).__name__

    return {"imageText": "", "imageTextSource": "",
            "imageReadError": f"读图失败：{last}"}
