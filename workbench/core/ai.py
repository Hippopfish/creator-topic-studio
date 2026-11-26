"""双模 AI：同一套模板，两种执行路径。

- prepare() 拼出完整提示词，用户复制到任意 AI 跑
- run()     配了 Key 就直接调 OpenAI 兼容接口
- ingest()  把复制模式跑出来的结果贴回来，解析入库

失败时的原则：**不报错挡路，降级成复制模式**，用户操作路径不变。
"""
from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime

import httpx

from . import normalize, paths, prompts, store

TASK_OUTPUT = {
    "title_gen": "json",
    "draft_write": "markdown",
    "page_plan": "json",
    "cover_plan": "json",
    "comment_insight": "json",
    "review": "json",
    "profile_classify": "json",
    "profile_rules": "markdown",
    "profile_memory": "json",
    "profile_creator": "json",
    "profile_selfcheck": "json",
}

# DeepSeek 现行模型（核对日期 2026-11-24，来源 api-docs.deepseek.com/quick_start/pricing）
# 只列仍可用的。弃用名（deepseek-chat / deepseek-reasoner / deepseek-v4-flash）不再出现在这里。
MODEL_PRESETS = [
    {"id": "deepseek-flash", "label": "deepseek-flash",
     "note": "V4.1 Flash｜1M 上下文｜快且便宜｜支持图片"},
    {"id": "deepseek-v4-pro", "label": "deepseek-v4-pro",
     "note": "V4 Pro｜1M 上下文｜最强推理｜约 4 倍价"},
]

MODEL_NAMES = {m["id"] for m in MODEL_PRESETS}

# 单次回复的输出上限。不暴露给用户：它只是安全阀，调小了会截断 JSON，
# 调大不影响计费（按实际生成量计费），所以统一给一个够用的值。
DEFAULT_MAX_TOKENS = 8000

TASK_NAMES = {
    "title_gen": "标题生成",
    "draft_write": "写稿",
    "page_plan": "页序方案",
    "cover_plan": "封面方案",
    "comment_insight": "评论洞察",
    "review": "内容复盘",
    "profile_classify": "档案·分内容类型",
    "profile_rules": "档案·提炼写法",
    "profile_memory": "档案·记住你是谁",
    "profile_creator": "档案·运营规划",
    "profile_selfcheck": "档案·试写验收",
}


# ================================================================== 上下文组装

def build_vars(task: str, ctx: dict) -> dict:
    acc = _account(ctx.get("accountId"))
    th = store.settings().get("thresholds", {})
    v: dict = {
        "today": datetime.now().strftime("%Y-%m-%d"),
        "platform": paths.PLATFORM_NAMES.get(acc.get("platform") or "xhs", "小红书"),
        "positioning": acc.get("positioning") or "（未填，请按 MBTI 内容理解）",
        "audience": acc.get("audience") or "（未填）",
        "contentPillars": acc.get("contentPillars") or [],
        "mustNotDrift": (acc.get("boundaries") or {}).get("mustNotDrift") or [],
        "forbiddenTopics": (acc.get("boundaries") or {}).get("forbiddenTopics") or [],
        "bannedTone": (acc.get("boundaries") or {}).get("bannedTone") or [],
        "businessGoal": (acc.get("businessGoal") or {}).get("detail") or "",
        "tCollect": th.get("collect", 0.5),
        "tComment": th.get("comment", 0.04),
        "tShare": th.get("share", 0.08),
        "tShareCollectMax": th.get("shareCollectMax", 0.3),
    }

    if task.startswith("profile_"):
        # 档案台的变量由 core/profile.py 组装好，这里只做透传
        v.update(ctx.get("vars") or {})
        return v

    if task == "title_gen":
        t = _topic(ctx.get("topicId")) or {}
        d = _draft(ctx.get("draftId")) or {}
        v.update({
            "topicTitle": t.get("title") or d.get("publishedTitle") or "（未填）",
            "topicReader": t.get("reader") or "（未填）",
            "topicAngle": _label(t.get("angle"), "angle"),
            "topicGap": _label(t.get("gap"), "gap"),
            "draftBody": (d.get("body") or ctx.get("body") or "（还没有正文）")[:1200],
            "count": ctx.get("count") or 10,
            "formulas": _formula_sample(12),
            "existingTitles": [c.get("text") for c in (d.get("titleCandidates") or [])][:10],
            **_intent_vars(t, d),
        })

    elif task == "draft_write":
        t = _topic(ctx.get("topicId")) or {}
        d = _draft(ctx.get("draftId")) or {}
        v.update({
            "topicTitle": t.get("title") or "（未填）",
            "topicReader": t.get("reader") or "（未填）",
            "topicGap": _label(t.get("gap"), "gap"),
            "topicAngle": _label(t.get("angle"), "angle"),
            "evidence": _evidence_text(t.get("evidence") or []),
            "contentForm": d.get("contentForm") or t.get("contentForm") or "opinion_short",
            "maxChars": 1000,
            "material": ctx.get("material") or _evidence_material(t),
            "pagePlanText": _page_plan_text(d),
            **_intent_vars(t, d),
        })

    elif task == "page_plan":
        d = _draft(ctx.get("draftId")) or {}
        t = _topic(d.get("topicId")) or {}
        v.update({
            "topicTitle": t.get("title") or "（未填）",
            "contentForm": d.get("contentForm") or ctx.get("contentForm") or "opinion_short",
            "material": ctx.get("material") or "（未填）",
            **_intent_vars(t, d),
        })

    elif task == "cover_plan":
        d = _draft(ctx.get("draftId")) or {}
        t = _topic(d.get("topicId")) or {}
        v.update({
            "topicTitle": d.get("publishedTitle") or t.get("title") or "（未填）",
            "contentForm": d.get("contentForm") or "opinion_short",
            "material": ctx.get("material") or "（未填）",
            "platformName": paths.PLATFORM_NAMES.get(d.get("platform") or "xhs", "小红书"),
            **_intent_vars(t, d),
        })

    elif task == "comment_insight":
        key = ctx.get("contentKey") or ""
        rec = _content_by_key(key) or {}
        bucket = _comments(key)
        v.update({
            "noteTitle": rec.get("title") or "（未填）",
            "noteBody": (rec.get("body") or "")[:800],
            "commentsText": _comments_text(bucket),
            "commentCount": len(bucket),
        })

    elif task == "review":
        r = _review(ctx.get("reviewId")) or {}
        v.update({
            "publishTitle": r.get("title") or "（未填）",
            "publishedAt": r.get("publishedAt") or "",
            "metricsText": _metrics_text(r),
            "baselineText": _baseline_text(r),
            "commentGroupsText": _groups_text(r),
        })

    return v


def prepare(task: str, ctx: dict) -> dict:
    # 选题不再走「渲染一大段提示词 + 一次性出 JSON」这条路，改成选题台里的对话。
    # topic_judge.md 还留着，因为对话要把里面的判断规则切出来临时加载。
    if task == "topic_judge":
        return {"ok": False, "code": "MOVED_TO_CHAT",
                "message": "选题生成已经并进选题台的对话（/api/chat/stream）。"
                           "这条一次性通道已停用，规则仍从 prompts/topic_judge.md 读取。"}

    meta, body = prompts.load(task)
    if not body:
        return {"ok": False, "code": "TEMPLATE_MISSING",
                "message": f"模板 {task}.md 不存在"}

    v = build_vars(task, ctx)
    user_prompt = prompts.render(body, v)
    system_prompt = (
        f"你是「{meta.get('title', TASK_NAMES.get(task, task))}」环节的执行者。"
        f"严格按下方规则输出，不要解释你的过程，不要输出 JSON 之外的任何文字。"
        if meta.get("output", TASK_OUTPUT.get(task, "json")) == "json"
        else f"你是「{meta.get('title', TASK_NAMES.get(task, task))}」环节的执行者。"
             f"严格按下方规则输出成稿，不要解释过程。"
    )
    total = len(system_prompt) + len(user_prompt)
    return {
        "ok": True,
        "task": task,
        "taskName": TASK_NAMES.get(task, task),
        "output": meta.get("output", TASK_OUTPUT.get(task, "json")),
        "systemPrompt": system_prompt,
        "userPrompt": user_prompt,
        "chars": total,
        "estTokens": prompts.est_tokens(total),
        "materialMode": v.get("_materialMode"),
        "materialCount": v.get("n"),
    }


# ================================================================== 调用

def run(task: str, ctx: dict) -> dict:
    prep = prepare(task, ctx)
    if not prep.get("ok"):
        return prep

    s = store.settings()["ai"]
    key = store.secret()
    if not key:
        return {"ok": False, "code": "NO_API_KEY",
                "message": "还没配置 API Key，已准备好提示词，可复制到任意 AI 使用",
                "fallbackPrompt": {"systemPrompt": prep["systemPrompt"],
                                   "userPrompt": prep["userPrompt"]}}

    base = str(s.get("baseUrl") or "").rstrip("/")
    if not base or not str(s.get("model") or "").strip():
        return {"ok": False, "code": "NO_ENDPOINT",
                "message": "Base URL 或模型还没填，已准备好提示词，可复制到任意 AI 使用",
                "fallbackPrompt": {"systemPrompt": prep["systemPrompt"],
                                   "userPrompt": prep["userPrompt"]}}
    thinking_on = bool(s.get("thinking"))
    max_tokens = DEFAULT_MAX_TOKENS
    if thinking_on:
        # 思考模式下推理 token 计入输出额度，额度不够会导致正文为空
        max_tokens = max(max_tokens, 16000)

    payload = {
        "model": s["model"],
        "messages": [
            {"role": "system", "content": prep["systemPrompt"]},
            {"role": "user", "content": prep["userPrompt"]},
        ],
        "temperature": float(s.get("temperature", 0.6)),
        "max_tokens": max_tokens,
    }
    if s.get("jsonMode") and prep.get("output") == "json":
        payload["response_format"] = {"type": "json_object"}
    # thinking 是 DeepSeek V4 系列专有字段，换别家会被拒，按域名判断
    if "deepseek" in base.lower():
        payload["thinking"] = {"type": "enabled" if thinking_on else "disabled"}

    t0 = time.time()
    try:
        with httpx.Client(timeout=float(s.get("timeoutSec", 120))) as c:
            r = c.post(f"{base}/chat/completions",
                       headers={"Authorization": f"Bearer {key}"}, json=payload)
        ms = int((time.time() - t0) * 1000)
        if r.status_code >= 400:
            _log_usage(task, s["model"], 0, ms, False)
            return {"ok": False, "code": f"HTTP_{r.status_code}",
                    "message": f"{r.status_code}：{r.text[:200]}",
                    "fallbackPrompt": {"systemPrompt": prep["systemPrompt"],
                                       "userPrompt": prep["userPrompt"]}}
        data = r.json()
        msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
        text = msg.get("content", "") or ""
        usage = data.get("usage") or {}
        _log_usage(task, data.get("model") or s["model"],
                   usage.get("total_tokens") or 0, ms, True)
        if not text.strip():
            # 思考模式下推理 token 吃满额度时，正文会是空的
            if msg.get("reasoning_content"):
                return {"ok": False, "code": "EMPTY_CONTENT",
                        "message": "模型只输出了思考过程、没给出正文。"
                                   "关掉思考模式重试一次。",
                        "fallbackPrompt": {"systemPrompt": prep["systemPrompt"],
                                           "userPrompt": prep["userPrompt"]}}
            return {"ok": False, "code": "EMPTY_CONTENT",
                    "message": "接口返回了空内容，请检查模型名是否正确。",
                    "fallbackPrompt": {"systemPrompt": prep["systemPrompt"],
                                       "userPrompt": prep["userPrompt"]}}
    except Exception as e:
        _log_usage(task, s["model"], 0, int((time.time() - t0) * 1000), False)
        return {"ok": False, "code": "API_ERROR", "message": f"{type(e).__name__}: {e}",
                "fallbackPrompt": {"systemPrompt": prep["systemPrompt"],
                                   "userPrompt": prep["userPrompt"]}}

    out = {"ok": True, "mode": "api", "text": text, "ms": ms,
           "model": data.get("model"), "usage": usage}

    if prep.get("output") == "json":
        parsed = parse_json_loose(text)
        if parsed is None:
            out.update({"ok": False, "code": "PARSE_FAILED",
                        "message": "模型返回的不是合法 JSON，可直接在下方编辑后导入"})
        else:
            out["json"] = parsed
    return out


def parse_json_loose(text: str):
    """三级容错：去围栏 → 直接 loads → 括号配平截取。"""
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    i = s.find("{")
    if i < 0:
        i = s.find("[")
        if i < 0:
            return None
    depth = 0
    open_ch = s[i]
    close_ch = "}" if open_ch == "{" else "]"
    for j in range(i, len(s)):
        if s[j] == open_ch:
            depth += 1
        elif s[j] == close_ch:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[i:j + 1])
                except Exception:
                    return None
    return None


def parse_md_table(text: str) -> list[dict]:
    """把 markdown 表格解析成行字典（复制模式的兜底解析）。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip().startswith("|")]
    if len(lines) < 2:
        return []
    def cells(ln: str) -> list[str]:
        return [c.strip() for c in ln.strip().strip("|").split("|")]
    header = cells(lines[0])
    rows = []
    for ln in lines[1:]:
        if set(ln.replace("|", "").replace("-", "").replace(":", "").strip()) == set():
            continue
        vals = cells(ln)
        if len(vals) < 2:
            continue
        rows.append({header[i] if i < len(header) else f"c{i}": v
                     for i, v in enumerate(vals)})
    return rows


# ================================================================== 入库

TOPIC_KEYS = {
    "title": ["title", "标题"],
    "reader": ["reader", "target_reader", "读者", "目标读者"],
    "gap": ["gap", "缺口"],
    "angle": ["angle", "角度"],
    "method": ["method", "方法"],
    "canDeliver": ["can_deliver", "canDeliver", "讲得出来", "可兑现"],
    "evidence": ["evidence", "证据"],
    "evidenceRef": ["evidenceRef", "evidence_ref", "素材ID", "ref"],
    "userProblem": ["userProblem", "user_problem", "读者问题"],
    "contentDirection": ["contentDirection", "content_direction", "内容方向"],
    "contentValue": ["contentValue", "content_value", "内容价值"],
    "fitReason": ["fitReason", "fit_reason", "适配理由"],
}


def ingest(task: str, raw_text: str, target_id: str = "",
           account_id: str = "") -> dict:
    """把复制模式的结果解析并入库。返回入库结果摘要。"""
    parsed = parse_json_loose(raw_text)
    applied = {"task": task, "created": 0, "kind": ""}

    if task == "title_gen":
        draft = _draft(target_id)
        if not draft:
            return {"ok": False, "message": "没指定稿件"}
        items = _extract_titles(parsed, raw_text)
        if not items:
            return {"ok": False, "code": "NO_TITLES", "message": "没解析出标题"}
        bucket = draft.get("titleCandidates") or []
        bucket.extend(items)
        store.upsert_item("drafts", {**draft, "titleCandidates": bucket,
                                     "updatedAt": store.now_iso()}, note="ingest titles")
        applied.update({"created": len(items), "kind": "titles", "items": items})
        return {"ok": True, **applied}

    if task == "draft_write":
        draft = _draft(target_id)
        if not draft:
            return {"ok": False, "message": "没指定稿件"}
        body = _extract_body(raw_text)
        cap, img = _extract_split(raw_text)
        from api.studio_api import self_check

        item = {**draft, "body": body,
                "bodyChars": len(re.sub(r"\s", "", body)),
                "updatedAt": store.now_iso()}
        if cap or img:
            item["captionPlan"] = cap
            item["imagePlan"] = img
        item["selfCheck"] = self_check(item)
        store.upsert_item("drafts", item, note="ingest draft body")
        applied.update({"created": 1, "kind": "draft", "items": [item]})
        return {"ok": True, **applied}

    if task in ("page_plan", "cover_plan"):
        draft = _draft(target_id)
        if not draft:
            return {"ok": False, "message": "没指定稿件"}
        if task == "page_plan":
            pages = (parsed or {}).get("pages") if isinstance(parsed, dict) else None
            if not pages:
                return {"ok": False, "code": "NO_PAGES", "message": "没解析出页序"}
            plan = {"countRange": (parsed or {}).get("countRange") or [],
                    "pages": pages, "source": "ai"}
            store.upsert_item("drafts", {**draft, "pagePlan": plan,
                                         "updatedAt": store.now_iso()}, note="ingest pages")
            applied.update({"created": len(pages), "kind": "pages", "items": pages})
        else:
            cover = parsed if isinstance(parsed, dict) else {}
            store.upsert_item("drafts", {**draft, "cover": {**draft.get("cover", {}), **cover},
                                         "updatedAt": store.now_iso()}, note="ingest cover")
            applied.update({"created": 1, "kind": "cover", "items": [cover]})
        return {"ok": True, **applied}

    if task == "comment_insight":
        if not isinstance(parsed, dict) or not (parsed.get("topic") or {}).get("title"):
            return {"ok": False, "code": "NO_TOPIC", "message": "没解析出选题"}
        t = parsed["topic"]
        frame = parsed.get("direction_frame") or {}
        from api.studio_api import run_checks

        item = {
            "id": store.new_id("t"),
            "title": str(t.get("title"))[:40],
            "reader": frame.get("target_reader") or "",
            "gap": "premise",
            "gapNote": frame.get("core_tension") or "",
            "angle": "scenario_resonance",
            "evidence": [{"quote": c} for c in (parsed.get("thinking_process") or [])][:3],
            "canDeliver": "评论区有真实诉求，能写",
            "status": "candidate",
            "platform": "xhs",
            "accountId": account_id,
            "source": "comment_insight",
            "contentForm": None,
            "layer": None,
            "structure": None,
            "createdAt": store.now_iso(),
            "updatedAt": store.now_iso(),
        }
        item["qualityCheck"] = run_checks(item)
        store.upsert_item("topics", item, note="ingest comment insight")
        applied.update({"created": 1, "kind": "topics", "items": [item]})
        return {"ok": True, **applied}

    if task == "review":
        review = _review(target_id)
        if not review:
            return {"ok": False, "message": "没指定复盘"}
        if not isinstance(parsed, dict):
            return {"ok": False, "code": "NO_JSON", "message": "没解析出复盘结论"}
        conclusion = parsed.get("conclusion") or parsed
        suggestions = parsed.get("suggestions") or []
        norm_sug = [{"text": s.get("text") or s.get("建议") or str(s),
                     "layer": s.get("layer") or "expression",
                     "basis": s.get("basis") or "", "status": "pending"}
                    for s in suggestions[:3]]
        item = {**review, "conclusion": conclusion, "suggestions": norm_sug,
                "status": "analyzed", "updatedAt": store.now_iso()}
        store.upsert_item("reviews", item, note="ingest review")
        applied.update({"created": 1, "kind": "reviews", "items": [item]})
        return {"ok": True, **applied}

    return {"ok": False, "message": f"未知任务：{task}"}


def _extract_memo(parsed) -> dict | None:
    """研究小抄。原版 §5.5 ResearchMemo，本地化成七格 + 两句总结。"""
    if not isinstance(parsed, dict):
        return None
    raw = (parsed.get("researchMemo") or parsed.get("research_memo")
           or parsed.get("研究小抄"))
    if not isinstance(raw, dict):
        return None
    keys = ("confirmed", "assumptions", "conflicts", "quotes", "gaps",
            "canDeliver", "cannotDeliver")
    out: dict = {}
    for k in keys:
        v = raw.get(k)
        if isinstance(v, list):
            out[k] = [str(x).strip() for x in v if str(x).strip()][:12]
        elif v:
            out[k] = [str(v).strip()]
        else:
            out[k] = []
    out["dataOverview"] = str(parsed.get("dataOverview") or "").strip()
    out["whyThese"] = str(parsed.get("whyThese") or "").strip()
    if not any(out[k] for k in keys):
        return None
    return out


def _extract_topics(parsed, raw_text: str) -> list[dict]:
    if isinstance(parsed, dict):
        for k in ("topics", "items", "选题", "data"):
            if isinstance(parsed.get(k), list):
                return [x for x in parsed[k] if isinstance(x, dict)]
        if parsed.get("title"):
            return [parsed]
    if isinstance(parsed, list):
        return [x for x in parsed if isinstance(x, dict)]
    rows = parse_md_table(raw_text)
    return rows


def _row_to_topic(row: dict, account_id: str) -> dict:
    def pick(field: str) -> str:
        for k in TOPIC_KEYS[field]:
            if row.get(k):
                return str(row[k]).strip()
        return ""

    gap_raw = pick("gap")
    angle_raw = pick("angle")
    method_raw = pick("method")
    ev = pick("evidence")
    ref = pick("evidenceRef")

    from api.studio_api import (ANGLES, ANGLE_NAMES, GAPS, GAP_NAMES,
                                METHODS, METHOD_NAMES)

    gap = _match_enum(gap_raw, GAPS, GAP_NAMES)
    angle = _match_enum(angle_raw, ANGLES, ANGLE_NAMES)
    method = _match_enum(method_raw, METHODS, METHOD_NAMES)

    # 证据引用必须真能读回那条笔记，读不回来就当没挂（原版 sourceRefs 不得伪造）
    if ref and not _content_by_key(ref):
        ref = ""
    evidence = []
    if ev or ref:
        evidence.append({"quote": ev, "ref": ref})

    ei_raw = row.get("editorialIntent") or row.get("editorial_intent") or {}
    if not isinstance(ei_raw, dict):
        ei_raw = {}
    editorial = {
        "genre": _match_genre(str(ei_raw.get("genre") or "")),
        "readerGain": str(ei_raw.get("readerGain") or ei_raw.get("reader_gain") or "").strip(),
        "creatorGoal": str(ei_raw.get("creatorGoal") or ei_raw.get("creator_goal") or "").strip(),
        "mustNotDrift": str(ei_raw.get("mustNotDrift") or ei_raw.get("must_not_drift") or "").strip(),
    }
    if not any(editorial.values()):
        editorial = None

    pri = row.get("priority")
    try:
        pri = int(pri)
    except (TypeError, ValueError):
        pri = None

    return {
        "id": store.new_id("t"),
        "title": pick("title")[:40],
        "reader": pick("reader"),
        "userProblem": pick("userProblem"),
        "contentDirection": pick("contentDirection"),
        "contentValue": pick("contentValue"),
        "fitReason": pick("fitReason"),
        "gap": gap,
        "gapNote": gap_raw if not gap else "",
        "angle": angle,
        "method": method,
        "methodNote": method_raw if not method else "",
        "editorialIntent": editorial,
        "evidence": evidence,
        "canDeliver": pick("canDeliver"),
        "priority": pri,
        "status": "candidate",
        "platform": row.get("platform") or "xhs",
        "accountId": account_id,
        "source": "ai",
        "contentForm": row.get("contentForm") or None,
        "layer": row.get("layer") or None,
        "structure": None,
        "createdAt": store.now_iso(),
        "updatedAt": store.now_iso(),
    }


def _match_genre(raw: str) -> str:
    from api.studio_api import GENRES

    if not raw:
        return ""
    for g in GENRES:
        if g == raw:
            return g
    for g in GENRES:
        if g in raw or raw in g:
            return g
    return ""


def _match_enum(raw: str, values: list[str], names: dict) -> str:
    if not raw:
        return ""
    for v in values:
        if raw == v or raw.lower() == v.lower():
            return v
    for v, name in names.items():
        if name in raw:
            return v
    return ""


def _extract_titles(parsed, raw_text: str) -> list[dict]:
    rows: list[dict] = []
    if isinstance(parsed, dict):
        lst = parsed.get("titles") or parsed.get("items") or parsed.get("标题")
        if isinstance(lst, list):
            for x in lst:
                if isinstance(x, str):
                    rows.append({"text": x})
                elif isinstance(x, dict):
                    rows.append({
                        "text": x.get("text") or x.get("title") or "",
                        "formulaId": x.get("formulaId") or x.get("formula_id"),
                        "formulaName": x.get("formulaName") or x.get("formula_name") or "",
                        "reason": x.get("reason") or "",
                    })
    elif isinstance(parsed, list):
        for x in parsed:
            rows.append({"text": x if isinstance(x, str) else str(x.get("text", ""))})
    else:
        for r in parse_md_table(raw_text):
            txt = r.get("标题") or r.get("title") or ""
            if txt:
                rows.append({"text": txt,
                             "formulaId": r.get("公式") or r.get("formula"),
                             "formulaName": r.get("公式名") or ""})

    out = []
    for r in rows:
        txt = str(r.get("text") or "").strip().strip('"').strip("「」")
        if not txt:
            continue
        out.append({
            "text": txt,
            "formulaId": r.get("formulaId"),
            "formulaName": r.get("formulaName") or "",
            "reason": r.get("reason") or "",
            "chars": len(re.sub(r"\s", "", txt)),
            "selected": False,
        })
    return out


def _extract_body(raw: str) -> str:
    s = (raw or "").strip()
    m = re.search(r"##\s*正文\s*\n(.*?)(?=\n##\s|\Z)", s, re.S)
    if m:
        return m.group(1).strip()
    s = re.sub(r"^```[a-z]*\s*|\s*```$", "", s)
    return s.strip()


def _extract_split(raw: str) -> tuple[list[str], list[str]]:
    """拆「图文分工」两栏。原版 ③ 要求这两份清单分开写，不能逐段复制。

    以前这段生成完就随着 markdown 一起丢了，稿子的 captionPlan / imagePlan
    永远是空的——等于图文分工这环没落过地。
    """
    m = re.search(r"##\s*图文分工\s*\n(.*?)(?=\n##\s|\Z)", str(raw or ""), re.S)
    if not m:
        return [], []
    cap: list[str] = []
    img: list[str] = []
    cur: list[str] | None = None
    for ln in m.group(1).splitlines():
        s = ln.strip().lstrip("-·*● ").strip()
        if not s:
            continue
        if s.startswith("只有正文"):
            cur = cap
            s = re.split(r"[：:]", s, 1)[-1]
        elif s.startswith("只有图"):
            cur = img
            s = re.split(r"[：:]", s, 1)[-1]
        if cur is None:
            continue
        cur.extend(x.strip() for x in re.split(r"[；;]", s) if x.strip())
    return cap, img


# ================================================================== 数据读取

def _account(account_id: str | None) -> dict:
    """账号档案。档案台建过就用它产出的定位与受众（新权威），没建过才用旧的手填项。"""
    cre = store.read("profile/creator") or {}
    f = cre.get("fields") or {}
    if f.get("positioning") or f.get("audience"):
        owner = store.read("profile/owner") or {}
        acc = owner.get("account") or {}
        return {
            "id": "profile",
            "platform": acc.get("platform") or "xhs",
            "displayName": acc.get("nickName") or "",
            "positioning": f.get("positioning") or "",
            "audience": f.get("audience") or "",
            "contentPillars": f.get("pillars") or [],
            "boundaries": {},
            "businessGoal": {"type": "涨粉", "detail": f.get("goal") or ""},
        }
    items = store.list_items("account")
    if account_id:
        for a in items:
            if a.get("id") == account_id:
                return a
    return items[0] if items else {}


def _published_ts(rec: dict) -> float | None:
    """发布时间统一成秒级时间戳，解析不出返回 None。"""
    v = rec.get("publishedAt")
    if v in (None, "", 0):
        return None
    if isinstance(v, (int, float)):
        n = float(v)
        return n / 1000 if n > 1e11 else n
    s = str(v).strip()
    if s.isdigit():
        n = float(s)
        return n / 1000 if n > 1e11 else n
    try:
        return datetime.fromisoformat(s[:19]).timestamp()
    except Exception:
        return None


def _age_days(rec: dict) -> int | None:
    ts = _published_ts(rec)
    if ts is None:
        return None
    return max(0, int((time.time() - ts) // 86400))


def _age_label(rec: dict) -> str:
    d = _age_days(rec)
    if d is None:
        return "未知"
    if d <= 1:
        return "今天"
    if d < 30:
        return f"{d}天前"
    if d < 365:
        return f"{d // 30}个月前"
    return f"{d // 365}年前"


def _contents(keys: list[str]) -> list[dict]:
    out = []
    for p in paths.PLATFORMS:
        items = store.read(f"contents/{p}").get("items", {})
        for k in keys:
            if k in items:
                out.append(items[k])
    for p in paths.PLATFORMS:
        if len(out) >= len(keys):
            break
    return out


# ---- 候选池：不勾选时由工作台自动挑（〔我方〕为适配本地库设计，原版靠 AI 广搜）
POOL_LIMIT = 20          # 自筛模式最多喂几条（列表 20 条，深读前 10 条，AI 有得淘汰）
DEEP_LIMIT = 10          # 深读上限：勾选超过这个数就只给前 10 条正文
BODY_CHARS = 400         # 每条正文截断
COMMENT_TOP = 3          # 每条带几条高赞评论
# 原版 §5.3 明文：互动数据只用于同平台内相对排序，**不能当准入门槛**。
# 所以这里不设点赞下限，只要求这条笔记有东西可判断（有标题或正文）。


def _pick_pool(range_days: int, platform: str = "", limit: int = POOL_LIMIT) -> list[dict]:
    """按「时效 + 有内容」预筛候选池，供 AI 自筛。

    排序：按发布时间新→旧（原版 §5.3：在相近发布时间内做相对排序）。
    **不按点赞砍人**——数字只交给模型做相对排序，不当门票。
    """
    rows: list[dict] = []
    ps = [platform] if platform in paths.PLATFORMS else list(paths.PLATFORMS)
    for p in ps:
        rows.extend(store.read(f"contents/{p}").get("items", {}).values())

    def ok(r: dict) -> bool:
        if not (r.get("title") or (r.get("body") or "").strip()):
            return False
        if range_days > 0:
            d = _age_days(r)
            if d is not None and d > range_days:
                return False
        return True

    pool = [r for r in rows if ok(r)]
    pool.sort(key=lambda r: (-(_published_ts(r) or 0), -((r.get("metrics") or {}).get("like") or 0)))
    return pool[:limit]


def _deep_read(rows: list[dict]) -> str:
    """深读块：正文 + 高赞评论。这是原版「深读 3-5 条全文+评论」的本地实现。"""
    if not rows:
        return "（本轮没有深读素材，只能依据上表的标题和数据判断。evidence 必须写成「第 N 条：标题原句」，不许编造正文内容。）"
    blocks = []
    for i, r in enumerate(rows[:DEEP_LIMIT], 1):
        m = r.get("metrics") or {}
        body = (r.get("body") or "").strip()
        body = " ".join(body.split())
        if len(body) > BODY_CHARS:
            body = body[:BODY_CHARS] + "…"
        lines = [f"### 素材 {i}｜ID {r.get('key') or ''}｜{_clip(r.get('title'), 40)}",
                 f"- 发布：{_age_label(r)}｜点赞 {m.get('like') if m.get('like') is not None else '未知'}"
                 f"｜收藏率 {_pct(m.get('collectRate'))}｜评论率 {_pct(m.get('commentRate'))}"]
        lines.append(f"- 正文：{body or '（无正文，只有图片/视频）'}")
        cs = _comments(r.get("key") or "")[:COMMENT_TOP]
        if cs:
            lines.append("- 高赞评论：")
            for c in cs:
                lines.append(f"  · {_clip(c.get('content'), 90)}（{c.get('like')}赞）")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _pct(v) -> str:
    return "未知" if v is None else f"{v * 100:.1f}%"



def _recent_topics(limit: int = 20) -> str:
    """原版 §3.2：列出近期选题，用来了解已经覆盖过什么（不是禁止再做）。"""
    rows = [r for r in store.list_items("topics")
            if r.get("status") in ("candidate", "adopted")]
    rows.sort(key=lambda r: r.get("updatedAt") or "", reverse=True)
    rows = rows[:limit]
    if not rows:
        return "（还没有历史选题）"
    names = {"candidate": "待定", "adopted": "已采纳"}
    return "\n".join(f"- {_clip(r.get('title'), 30)}"
                     f"（{names.get(r.get('status'), r.get('status') or '')}）"
                     for r in rows)


def _experiences(limit: int = 15) -> str:
    """原版 §3.3 / memory.search：把复盘攒下的经验喂进来。没有就明说没有。"""
    rows = store.list_items("insights")
    if not rows:
        return ("（还没有复盘经验。按账号档案和本轮素材判断即可，"
                "**不要编造经验，也不要说「根据以往经验」**。）")
    rows.sort(key=lambda r: (bool(r.get("verified")), r.get("verifiedCount") or 0),
              reverse=True)
    lines = []
    for r in rows[:limit]:
        tag = "已验证" if r.get("verified") else "待验证"
        lines.append(f"- [{r.get('layer') or '未分层'}] "
                     f"{_clip(r.get('text'), 80)}（{tag}）")
    return "\n".join(lines)


def _content_by_key(key: str) -> dict | None:
    if not key or ":" not in key:
        return None
    p = key.split(":", 1)[0]
    return store.read(f"contents/{p}").get("items", {}).get(key)


def _comments(key: str) -> list[dict]:
    if not key or ":" not in key:
        return []
    p = key.split(":", 1)[0]
    bucket = store.read(f"comments/{p}").get("items", {}).get(key)
    if not bucket:
        return []
    items = list(bucket.get("items") or [])
    items.sort(key=lambda c: (-1 if c.get("like") is None else c["like"]), reverse=True)
    return items


def _topic(topic_id: str | None) -> dict | None:
    if not topic_id:
        return None
    return next((t for t in store.list_items("topics") if t.get("id") == topic_id), None)


def _draft(draft_id: str | None) -> dict | None:
    if not draft_id:
        return None
    return next((d for d in store.list_items("drafts") if d.get("id") == draft_id), None)


def _review(review_id: str | None) -> dict | None:
    if not review_id:
        return None
    return next((r for r in store.list_items("reviews") if r.get("id") == review_id), None)


def _label(v: str | None, kind: str) -> str:
    from api.studio_api import ANGLE_NAMES, GAP_NAMES

    if not v:
        return "（未填）"
    return (GAP_NAMES if kind == "gap" else ANGLE_NAMES).get(v, v)


def _table(rows: list[dict]) -> str:
    if not rows:
        return "（本轮没有候选素材）"
    # 素材ID 列是为了让模型能回填 evidenceRef，证据才可回读（原版 sourceRefs）
    head = ("| # | 素材ID | 平台 | 标题 | 发布 | 点赞 | 收藏 | 评论 | 分享 | 收藏率 | 评论率 | 分享率 | 结构 | 关键词 |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    lines = [head]
    for i, r in enumerate(rows[:60], 1):
        m = r.get("metrics") or {}
        def f(k, pct=False):
            v = m.get(k)
            if v is None:
                return "未知"
            return f"{v * 100:.1f}%" if pct else str(v)
        st = (r.get("classification") or {}).get("structure") or "unknown"
        lines.append(
            f"| {i} | {r.get('key') or ''} "
            f"| {paths.PLATFORM_NAMES.get(r.get('platform'), r.get('platform'))} "
            f"| {_clip(r.get('title'), 40)} | {_age_label(r)} | {f('like')} | {f('collect')} "
            f"| {f('comment')} | {f('share')} | {f('collectRate', True)} | {f('commentRate', True)} "
            f"| {f('shareRate', True)} | {normalize.STRUCTURE_NAMES.get(st, st)} "
            f"| {(r.get('source') or {}).get('keyword') or ''} |"
        )
    return "\n".join(lines)


def _clip(s, n: int) -> str:
    s = str(s or "").replace("\n", " ").replace("|", "／")
    return s if len(s) <= n else s[:n] + "…"


def _formula_sample(n: int) -> str:
    p = paths.PROMPTS_DIR / "formula_library.json"
    if not p.exists():
        return "（公式库未生成，按标题铁律自由发挥，但必须保证可搜索、有张力）"
    try:
        items = json.loads(p.read_text(encoding="utf-8")).get("items", [])
    except Exception:
        return "（公式库读取失败）"
    sample = random.sample(items, min(n, len(items)))
    return "\n".join(
        f"- #{x.get('id')} [{x.get('category')}] {x.get('template')}"
        for x in sample
    )


def _evidence_text(evidence: list) -> str:
    if not evidence:
        return "（没有挂证据，写的时候不要编造具体数据）"
    out = []
    for e in evidence[:6]:
        if isinstance(e, str):
            out.append(f"- {e}")
        else:
            q = e.get("quote") or ""
            ck = e.get("contentKey") or e.get("ref") or ""
            out.append(f"- {q}" + (f"（来源 {ck}）" if ck else ""))
    return "\n".join(out)


# ---- 选题定型：采纳时锁死，下游三个环节都必须继承（原版 §6 editorialIntent）
def _intent_of(topic: dict, draft: dict | None = None) -> dict:
    ei = (topic or {}).get("editorialIntent") or {}
    snap = (draft or {}).get("intent") or {}
    # 稿件快照优先：上游选题被改过，这篇稿子仍按当初锁定的约束写
    pick = lambda k: snap.get(k) or ei.get(k) or ""
    return {
        "genre": pick("genre"),
        "creatorGoal": pick("creatorGoal"),
        "mustNotDrift": pick("mustNotDrift"),
        "readerGain": pick("readerGain"),
    }


def _intent_vars(topic: dict, draft: dict | None = None) -> dict:
    it = _intent_of(topic, draft)
    return {
        "intentGenre": it["genre"] or "（未定，按材料能支撑的写）",
        "intentGoal": it["creatorGoal"] or "（未填，按表达/记录理解，不推销）",
        "intentMustNotDrift": it["mustNotDrift"] or "（未定，但不能变成教程或科普）",
        "readerGain": it["readerGain"] or "（未填）",
    }


def _evidence_material(topic: dict) -> str:
    """把选题挂的证据素材真读进来：正文 + 高赞评论 + 已缓存的图上文字。

    不给这个，写稿环节手上只有一句 quote，剩下的只能靠常识编——
    原版的材料边界是「没有的写短」，不是「按主题合理想象」。
    """
    refs = []
    for e in (topic or {}).get("evidence") or []:
        if isinstance(e, dict):
            r = e.get("ref") or e.get("contentKey")
            if r:
                refs.append(str(r))
    blocks = []
    for ref in refs[:2]:
        rec = _content_by_key(ref) or {}
        if not rec:
            continue
        m = rec.get("metrics") or {}
        lines = [f"### 素材：{_clip(rec.get('title'), 40)}",
                 f"- 互动：赞 {_val(m.get('like'))}｜收藏率 {_pct(m.get('collectRate'))}"
                 f"｜评论率 {_pct(m.get('commentRate'))}"]
        body = " ".join((rec.get("body") or "").split())
        lines.append(f"- 正文原文：{body[:600] or '（无正文，内容在图里）'}")
        if rec.get("imageText"):
            lines.append(f"- 图上的字（模型读图，可能有误）：{str(rec['imageText'])[:600]}")
        cs = _comments(ref)[:3]
        if cs:
            lines.append("- 高赞评论：" + "；".join(
                f"{_clip(c.get('content'), 60)}（{c.get('like')}赞）" for c in cs))
        blocks.append("\n".join(lines))
    if not blocks:
        return ("（这条选题没挂可读素材。只能靠下面列的证据原句和你确定知道的东西写，"
                "没有的部分就写短，不许编具体数据、数字和别人的经历）")
    return "\n\n".join(blocks)


def _page_plan_text(draft: dict) -> str:
    pages = ((draft.get("pagePlan") or {}).get("pages")) or []
    if not pages:
        return "（还没有页序方案，按内容形式自行安排）"
    return "\n".join(
        f"- 第{p.get('index')}页 [{p.get('role')}] {p.get('text') or ''}"
        for p in pages
    )


def _comments_text(items: list[dict]) -> str:
    if not items:
        return "（没有评论数据）"
    return "\n".join(
        f"- [{c.get('like') or 0} 赞]{'（回复）' if c.get('parentId') else ''} {c.get('content')}"
        for c in items[:60]
    )


def _val(x) -> str:
    """缺失写「未知」，不写 0（原版：缺的数字是未知，不是 0）。"""
    return "未知" if x is None else str(x)


def _metrics_text(r: dict) -> str:
    rows = []
    b = r.get("backend") or {}
    if b:
        d = b.get("daysSincePublish")
        when = f"发布后第 {d} 天" if isinstance(d, int) else "最近一次观测"
        # 逐篇接口只给观看/赞藏评转，不给曝光和涨粉（那是账号级指标），
        # 没有的就不提，别写成「未知」占位
        parts = []
        if b.get("impressions") is not None:
            parts.append(f"曝光 {b['impressions']}")
        parts += [f"观看 {_val(b.get('views'))}", f"点赞 {_val(b.get('likes'))}",
                  f"收藏 {_val(b.get('collects'))}", f"评论 {_val(b.get('comments'))}",
                  f"分享 {_val(b.get('shares'))}"]
        if b.get("fansGained") is not None:
            parts.append(f"涨粉 {b['fansGained']}")
        rows.append(f"- {when}（创作者后台）：" + "，".join(parts))
    for m in (r.get("metrics") or []):
        if (m.get("like") is None and m.get("views") is None
                and m.get("impressions") is None):
            continue
        rows.append(f"- D{m.get('day')}：点赞 {_val(m.get('like'))}，"
                    f"收藏 {_val(m.get('collect'))}，"
                    f"评论 {_val(m.get('comment'))}，"
                    f"分享 {_val(m.get('share'))}，"
                    f"播放 {_val(m.get('views'))}，"
                    f"曝光 {_val(m.get('impressions'))}，"
                    f"涨粉 {_val(m.get('fansGained'))}")
    return "\n".join(rows) or "（还没有录入数据）"


def _baseline_text(r: dict) -> str:
    b = r.get("baseline") or {}
    med = b.get("median") or {}
    if not med:
        return "（没有基线，先多录几条数据）"
    parts = [f"样本 {b.get('sampleSize')} 条",
             f"点赞中位数 {_val(med.get('like'))}",
             f"收藏 {_val(med.get('collect'))}",
             f"评论 {_val(med.get('comment'))}",
             f"收藏率 {_val(med.get('collectRate'))}"]
    if med.get("impressions") is not None:
        parts.append(f"曝光中位数 {med['impressions']}")
    if med.get("views") is not None:
        parts.append(f"观看中位数 {med['views']}")
    return "；".join(parts) + "（这是这个账号自己的基线，不是行业平均）"


def _groups_text(r: dict) -> str:
    g = r.get("commentGroups") or {}
    if not g:
        return "（没有评论分组数据）"
    names = {"ask": "追问", "disagree": "反驳", "add": "补充",
             "emotion": "情绪", "action": "行动意图", "author": "作者互动"}
    return "，".join(f"{names.get(k, k)} {v}" for k, v in g.items())


def _log_usage(task: str, model: str, tokens: int, ms: int, ok: bool) -> None:
    try:
        rec = {"at": store.now_iso(), "task": task, "model": model,
               "tokens": tokens, "ms": ms, "ok": ok}
        with (paths.LOGS_DIR / "ai-usage.ndjson").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
