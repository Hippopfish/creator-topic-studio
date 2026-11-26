"""选题、稿件、复盘、经验库、标题公式库接口。"""
from __future__ import annotations

import json
import re
from datetime import date

from fastapi import APIRouter, Body, HTTPException

from core import baseline, datacenter, paths, store

studio_router = APIRouter(prefix="/api")

GAPS = ["premise", "boundary", "next_step", "counterexample", "wrong_audience"]
GAP_NAMES = {"premise": "前提", "boundary": "边界", "next_step": "下一步",
             "counterexample": "反例", "wrong_audience": "错人群"}
ANGLES = ["direct_value", "curiosity_gap", "counterintuitive",
          "scenario_resonance", "decision_help", "case_story", "timely_interpretation"]
ANGLE_NAMES = {"direct_value": "直接给价值", "curiosity_gap": "好奇缺口",
               "counterintuitive": "反常识", "scenario_resonance": "场景共鸣",
               "decision_help": "决策帮助", "case_story": "案例故事",
               "timely_interpretation": "时效解读"}
# 原版 §7 方法库六选一：同一个候选只记一个最主要的方法
METHODS = ["knowledge_adaptation", "audience_problem", "experience_case",
           "content_gap", "trend_event", "cross_source_synthesis"]
METHOD_NAMES = {"knowledge_adaptation": "改写已有素材",
                "audience_problem": "从真实困惑提炼",
                "experience_case": "以真实经历为主线",
                "content_gap": "补前提/边界/反例",
                "trend_event": "解释事件影响",
                "cross_source_synthesis": "多来源组合出新判断"}
# 原版 §6 editorialIntent.genre —— 这条内容更像哪一种，决定下游怎么写
GENRES = ["观察", "体验", "证据测评", "故事", "比较", "观点", "商业证明", "操作指引"]
LAYERS = ["L1", "L2", "L3", "L4", "L5"]
FORMS = ["opinion_short", "emotional_resonance", "evidence_review", "sensory_experience",
         "life_record", "narrative_sequence", "collection_list", "decision_comparison",
         "operational_guidance", "knowledge_explainer", "proof_led_commercial"]

# 选题里的 contentForm 有三种来源：早期 AI 写的中文自由词（"自省记录"/"对比观察"）、
# 人工选的英文枚举、空。下游 page_plan 只认 11 种枚举，中文词进去等于没给，
# 所以这里统一归一。体裁（genre）才是权威，contentForm 只决定"怎么呈现"。
FORM_ALIASES = {
    "观点": "opinion_short", "观点随笔": "opinion_short", "随笔": "opinion_short",
    "观察": "life_record", "观察随笔": "life_record", "自省记录": "life_record",
    "生活记录": "life_record", "日记": "life_record",
    "情绪": "emotional_resonance", "情绪共鸣": "emotional_resonance",
    "体验": "sensory_experience", "探店": "sensory_experience",
    "证据测评": "evidence_review", "测评": "evidence_review", "实测": "evidence_review",
    "故事": "narrative_sequence", "经历": "narrative_sequence",
    "比较": "decision_comparison", "对比": "decision_comparison", "对比观察": "decision_comparison",
    "合集": "collection_list", "清单": "collection_list",
    "操作指引": "operational_guidance", "教程": "operational_guidance", "攻略": "operational_guidance",
    "知识": "knowledge_explainer", "科普": "knowledge_explainer",
    "商业证明": "proof_led_commercial", "转化": "proof_led_commercial",
}
FORM_BY_GENRE = {
    "观点": "opinion_short", "观察": "life_record", "体验": "sensory_experience",
    "证据测评": "evidence_review", "故事": "narrative_sequence",
    "比较": "decision_comparison", "商业证明": "proof_led_commercial",
    "操作指引": "operational_guidance",
}


def normalize_form(form: str | None, genre: str | None = "") -> tuple[str, bool]:
    """返回 (合法 form, 是否由推断得来)。推断只用于下游提示词，别写回选题。"""
    f = str(form or "").strip()
    if f in FORMS:
        return f, False
    if f and f in FORM_ALIASES:
        return FORM_ALIASES[f], True
    g = str(genre or "").strip()
    if g in FORM_BY_GENRE:
        return FORM_BY_GENRE[g], True
    return "opinion_short", True

FORBIDDEN_PATTERNS = [
    (re.compile(r"不是.{1,14}[，,]\s*而是"), "「不是…而是」转折句式"),
    (re.compile(r"你以为.{1,14}[，,]\s*其实"), "「你以为…其实」转折句式"),
    (re.compile(r"与其.{1,14}[，,]\s*不如"), "「与其…不如」转折句式"),
]
AI_WORDS = ["底层逻辑", "闭环", "抓手", "颗粒度", "赋能", "破圈", "范式",
            "认知升级", "降维打击", "真正的", "本质上是"]
VAGUE_READERS = ["年轻人", "大家", "所有人", "女生们", "打工人"]
# 正文里出现这些，说明在向读者交代"我参考了什么"，而不是直接讲这件事本身
REFERENCE_LEAK = re.compile(
    r"(原视频|原文案|原文里|原文章|原片|参考片|参考的这[篇条]|这条视频里|"
    r"画面里能看到|原作者|博主说|上面那篇|素材里说)")


# ================================================================== 选题

@studio_router.get("/topics")
def list_topics(status: str = "", platform: str = "", angle: str = "",
                gap: str = "", layer: str = "", q: str = "") -> dict:
    rows = store.list_items("topics")
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if platform:
        rows = [r for r in rows if r.get("platform") == platform]
    if angle:
        rows = [r for r in rows if r.get("angle") == angle]
    if gap:
        rows = [r for r in rows if r.get("gap") == gap]
    if layer:
        rows = [r for r in rows if r.get("layer") == layer]
    if q:
        n = q.lower()
        rows = [r for r in rows if n in (r.get("title") or "").lower()
                or n in (r.get("reader") or "").lower()]
    return {
        "items": rows,
        "enums": {"gaps": GAP_NAMES, "angles": ANGLE_NAMES,
                  "methods": METHOD_NAMES, "genres": GENRES,
                  "layers": LAYERS, "forms": FORMS},
    }


@studio_router.post("/topics")
def create_topic(payload: dict = Body(...)) -> dict:
    item = {
        "title": "", "reader": "", "gap": "", "gapNote": "", "angle": "",
        "method": "", "methodNote": "", "editorialIntent": None,
        "userProblem": "", "contentDirection": "", "contentValue": "",
        "fitReason": "", "priority": None,
        "evidence": [], "canDeliver": "", "layer": None, "structure": None,
        "contentForm": None, "status": "candidate", "platform": "xhs",
        "accountId": "", "source": "manual", "seedBrief": None,
        "qualityCheck": None,
        **payload,
    }
    item["id"] = payload.get("id") or store.new_id("t")
    item["createdAt"] = store.now_iso()
    item["updatedAt"] = item["createdAt"]
    item["qualityCheck"] = run_checks(item)
    store.upsert_item("topics", item, note="create topic")
    return item


@studio_router.put("/topics/{topic_id}")
def update_topic(topic_id: str, payload: dict = Body(...)) -> dict:
    cur = next((t for t in store.list_items("topics") if t.get("id") == topic_id), None)
    if not cur:
        raise HTTPException(404, "选题不存在")
    item = {**cur, **payload, "id": topic_id, "updatedAt": store.now_iso()}
    item["qualityCheck"] = run_checks(item)
    store.upsert_item("topics", item, note="update topic")
    return item


@studio_router.delete("/topics/{topic_id}")
def delete_topic(topic_id: str) -> dict:
    return {"ok": store.remove_item("topics", topic_id, note="delete topic")}


@studio_router.post("/topics/{topic_id}/check")
def check_topic(topic_id: str) -> dict:
    cur = next((t for t in store.list_items("topics") if t.get("id") == topic_id), None)
    if not cur:
        raise HTTPException(404, "选题不存在")
    res = run_checks(cur)
    store.upsert_item("topics", {**cur, "qualityCheck": res})
    return res


def _content_exists(ref: str) -> bool:
    """ref 形如 xhs:6b01b79b…，去对应平台库里查还在不在。"""
    platform, _, cid = str(ref).partition(":")
    if not platform or not cid:
        return False
    try:
        return f"{platform}:{cid}" in ((store.read(f"contents/{platform}") or {}).get("items") or {})
    except Exception:
        return False


def run_checks(topic: dict) -> dict:
    """十一条质检。规则来源：标题 11 条铁律 + 六字段完整性（〔照搬〕）+ 方法/体裁/防跑偏（原版 §6 §7）。"""
    title = str(topic.get("title") or "")
    compact = re.sub(r"\s", "", title)
    items = []

    items.append({"rule": "title_le_20", "label": "标题 ≤20 字",
                  "pass": 0 < len(compact) <= 20,
                  "message": f"当前 {len(compact)} 字"})

    bad_phrase = ""
    for pat, name in FORBIDDEN_PATTERNS:
        if pat.search(title):
            bad_phrase = name
            break
    items.append({"rule": "no_forbidden_phrase", "label": "无转折句式",
                  "pass": not bad_phrase,
                  "message": bad_phrase or "通过"})

    ai_hit = next((w for w in AI_WORDS if w in title), "")
    items.append({"rule": "no_ai_tone", "label": "无 AI 腔",
                  "pass": not ai_hit, "message": f"出现「{ai_hit}」" if ai_hit else "通过"})

    items.append({"rule": "gap_valid", "label": "缺口属于五类",
                  "pass": topic.get("gap") in GAPS,
                  "message": GAP_NAMES.get(topic.get("gap") or "", "未选")})

    items.append({"rule": "angle_valid", "label": "角度属于七类",
                  "pass": topic.get("angle") in ANGLES,
                  "message": ANGLE_NAMES.get(topic.get("angle") or "", "未选")})

    items.append({"rule": "method_valid", "label": "方法属于六类",
                  "pass": topic.get("method") in METHODS,
                  "message": METHOD_NAMES.get(topic.get("method") or "", "未选")})

    ei = topic.get("editorialIntent") or {}
    items.append({"rule": "genre_valid", "label": "体裁说清了",
                  "pass": (ei.get("genre") or "") in GENRES,
                  "message": ei.get("genre") or "未定"})

    items.append({"rule": "no_drift", "label": "写了不能被改写成什么",
                  "pass": bool(str(ei.get("mustNotDrift") or "").strip()),
                  "message": "已写" if ei.get("mustNotDrift") else "未写"})

    reader = str(topic.get("reader") or "")
    vague = next((v for v in VAGUE_READERS if v in reader), "")
    items.append({"rule": "reader_specific", "label": "读者具体到场景",
                  "pass": len(reader) >= 6 and not vague,
                  "message": f"「{vague}」太泛" if vague else (f"{len(reader)} 字" if reader else "未填")})

    ev = topic.get("evidence") or []
    # 有 quote 不算数，得能指回原文：否则「证据」可以是模型自己写的漂亮话。
    # 光有 ref 字段也不算数——素材可能已经被归档或删掉，指过去是一片空白。
    refs = [str((e or {}).get("ref") or "").strip() for e in ev]
    refs = [r for r in refs if r]
    missing = [r for r in refs if not _content_exists(r)]
    if not refs:
        ev_pass, ev_msg = False, ("有证据但没挂素材ID" if ev else "未挂证据")
    elif missing:
        ev_pass, ev_msg = False, f"{len(missing)}/{len(refs)} 条查不到原文（已归档或删除）"
    else:
        ev_pass, ev_msg = True, f"{len(ev)} 条"
    items.append({"rule": "evidence_present", "label": "证据能回链原文",
                  "pass": ev_pass, "message": ev_msg})

    cd = str(topic.get("canDeliver") or "").strip()
    items.append({"rule": "can_deliver", "label": "讲得出来",
                  "pass": bool(cd), "message": cd[:24] if cd else "未填"})

    return {"pass": all(i["pass"] for i in items),
            "passCount": sum(1 for i in items if i["pass"]),
            "total": len(items), "items": items, "checkedAt": store.now_iso()}


@studio_router.get("/memos")
def list_memos(limit: int = 20) -> dict:
    rows = store.list_items("memos")
    rows.sort(key=lambda r: r.get("at") or "", reverse=True)
    return {"items": rows[:limit]}


@studio_router.get("/memos/{memo_id}")
def get_memo(memo_id: str) -> dict:
    m = next((x for x in store.list_items("memos") if x.get("id") == memo_id), None)
    if not m:
        raise HTTPException(404, "研究小抄不存在")
    return m


@studio_router.post("/topics/{topic_id}/to-draft")
def topic_to_draft(topic_id: str, payload: dict = Body(default={})) -> dict:
    topic = next((t for t in store.list_items("topics") if t.get("id") == topic_id), None)
    if not topic:
        raise HTTPException(404, "选题不存在")

    existing = next((d for d in store.list_items("drafts") if d.get("topicId") == topic_id), None)
    if existing:
        # 老稿件没有 intent 快照的，这次顺手补上，别让它在写稿时裸奔
        if not existing.get("intent"):
            ei0 = topic.get("editorialIntent") or {}
            existing = {**existing, "intent": {
                "genre": ei0.get("genre") or "",
                "readerGain": ei0.get("readerGain") or "",
                "creatorGoal": ei0.get("creatorGoal") or "",
                "mustNotDrift": ei0.get("mustNotDrift") or "",
            }, "updatedAt": store.now_iso()}
            store.upsert_item("drafts", existing, note="backfill draft intent")
        return existing

    ei = topic.get("editorialIntent") or {}
    # 选题定型在采纳这一刻就该锁死。上游改了选题，这篇稿子仍按当初的约束写，
    # 不然「不能写成什么」会在改稿过程中悄悄失效。
    form, inferred = normalize_form(payload.get("contentForm") or topic.get("contentForm"),
                                    ei.get("genre"))

    draft = {
        "id": store.new_id("d"),
        "topicId": topic_id,
        "accountId": topic.get("accountId") or "",
        "platform": payload.get("platform") or topic.get("platform") or "xhs",
        "contentForm": form,
        "formInferred": inferred,
        "formRaw": str(payload.get("contentForm") or topic.get("contentForm") or ""),
        "intent": {
            "genre": ei.get("genre") or "",
            "readerGain": ei.get("readerGain") or "",
            "creatorGoal": ei.get("creatorGoal") or "",
            "mustNotDrift": ei.get("mustNotDrift") or "",
        },
        "publishedTitle": "",
        "titleCandidates": [],
        "body": "",
        "bodyChars": 0,
        "pagePlan": {"countRange": [], "pages": []},
        "captionPlan": [],
        "imagePlan": [],
        "cover": {"pattern": "", "title": "", "lines": []},
        "selfCheck": None,
        "status": "draft",
        "aiTrace": [],
        "createdAt": store.now_iso(),
        "updatedAt": store.now_iso(),
    }
    store.upsert_item("drafts", draft, note="create draft from topic")
    store.upsert_item("topics", {**topic, "status": "adopted",
                                 "updatedAt": store.now_iso()})
    return draft


# ================================================================== 稿件

@studio_router.get("/drafts")
def list_drafts(status: str = "", platform: str = "", topicId: str = "") -> dict:
    rows = store.list_items("drafts")
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if platform:
        rows = [r for r in rows if r.get("platform") == platform]
    if topicId:
        rows = [r for r in rows if r.get("topicId") == topicId]
    topics = {t["id"]: t for t in store.list_items("topics") if t.get("id")}
    for r in rows:
        t = topics.get(r.get("topicId") or "") or {}
        r["topicTitle"] = t.get("title")
    return {"items": rows}


@studio_router.post("/drafts")
def create_draft(payload: dict = Body(...)) -> dict:
    item = {"status": "draft", "body": "", "titleCandidates": [],
            "pagePlan": {"countRange": [], "pages": []},
            "captionPlan": [], "imagePlan": [],
            "cover": {"pattern": "", "title": "", "lines": []},
            "createdAt": store.now_iso(), **payload}
    item["id"] = payload.get("id") or store.new_id("d")
    item["updatedAt"] = item["createdAt"]
    store.upsert_item("drafts", item, note="create draft")
    return item


@studio_router.put("/drafts/{draft_id}")
def update_draft(draft_id: str, payload: dict = Body(...)) -> dict:
    cur = next((d for d in store.list_items("drafts") if d.get("id") == draft_id), None)
    if not cur:
        raise HTTPException(404, "稿件不存在")
    item = {**cur, **payload, "id": draft_id, "updatedAt": store.now_iso()}
    item["bodyChars"] = len(re.sub(r"\s", "", str(item.get("body") or "")))
    item["selfCheck"] = self_check(item)
    store.upsert_item("drafts", item, note="update draft")
    return item


@studio_router.delete("/drafts/{draft_id}")
def delete_draft(draft_id: str) -> dict:
    return {"ok": store.remove_item("drafts", draft_id, note="delete draft")}


def self_check(draft: dict) -> dict:
    body = str(draft.get("body") or "")
    hits = []
    for pat, name in FORBIDDEN_PATTERNS:
        for m in pat.finditer(body):
            hits.append({"phrase": m.group(0), "name": name, "at": m.start()})
    ai_hits = [w for w in AI_WORDS if w in body]
    # 原版铁律：参考作品只当材料，不进正文。这几句一出现，读者就知道你在复述别人。
    leaks = [m.group(0) for m in REFERENCE_LEAK.finditer(body)]
    chars = len(re.sub(r"\s", "", body))
    return {
        "forbiddenPhrases": hits,
        "aiWords": ai_hits,
        "referenceLeaks": leaks,
        "chars": chars,
        "overLimit": chars > 1000,
        "ctaPresent": bool(re.search(r"(评论区|点个赞|关注我|收藏起来)", body)),
        "ready": not hits and not leaks and chars > 0,
        "checkedAt": store.now_iso(),
    }


@studio_router.post("/drafts/{draft_id}/publish")
def publish_draft(draft_id: str, payload: dict = Body(...)) -> dict:
    cur = next((d for d in store.list_items("drafts") if d.get("id") == draft_id), None)
    if not cur:
        raise HTTPException(404, "稿件不存在")

    draft = {**cur,
             "status": "published",
             "publishedAt": payload.get("publishedAt") or store.now_iso(),
             "publishedUrl": payload.get("url") or "",
             "publishedTitle": payload.get("title") or cur.get("publishedTitle") or "",
             "accountId": payload.get("accountId") or cur.get("accountId") or "",
             "updatedAt": store.now_iso()}
    store.upsert_item("drafts", draft, note="publish draft")

    existing = next((r for r in store.list_items("reviews") if r.get("draftId") == draft_id), None)
    if existing:
        return {"draft": draft, "review": existing}

    review = {
        "id": store.new_id("r"),
        "draftId": draft_id,
        "topicId": draft.get("topicId"),
        "accountId": draft.get("accountId") or "",
        "platform": draft.get("platform") or "xhs",
        "title": draft.get("publishedTitle") or "",
        "publishedAt": draft["publishedAt"],
        "url": draft["publishedUrl"],
        # 用 note_id 和自有库对齐：own/xhs.json 的 key 就是 xhs:{note_id}
        "contentKey": note_key(draft["publishedUrl"]),
        "metrics": [{"day": d, "at": None, "views": None, "like": None,
                     "collect": None, "comment": None, "share": None,
                     "impressions": None, "fansGained": None}
                    for d in (0, 1, 3, 7)],
        "backend": None,
        "baseline": None,
        "compare": None,
        "commentGroups": None,
        "conclusion": None,
        "suggestions": [],
        "status": "collecting",
        "createdAt": store.now_iso(),
        "updatedAt": store.now_iso(),
    }
    store.upsert_item("reviews", review, note="create review")
    return {"draft": draft, "review": review}


# ================================================================== 复盘

@studio_router.get("/reviews")
def list_reviews(status: str = "", platform: str = "") -> dict:
    rows = store.list_items("reviews")
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if platform:
        rows = [r for r in rows if r.get("platform") == platform]
    return {"items": rows}


@studio_router.post("/reviews")
def create_review(payload: dict = Body(...)) -> dict:
    item = {"status": "collecting", "metrics": [], "suggestions": [],
            "createdAt": store.now_iso(), **payload}
    item["id"] = payload.get("id") or store.new_id("r")
    item["updatedAt"] = item["createdAt"]
    store.upsert_item("reviews", item, note="create review")
    return item


@studio_router.delete("/reviews/{review_id}")
def delete_review(review_id: str) -> dict:
    return {"ok": store.remove_item("reviews", review_id, note="delete review")}


@studio_router.post("/reviews/{review_id}/metrics")
def save_metrics(review_id: str, payload: dict = Body(...)) -> dict:
    cur = next((r for r in store.list_items("reviews") if r.get("id") == review_id), None)
    if not cur:
        raise HTTPException(404, "复盘不存在")

    day = int(payload.get("day", 0))
    row = {
        "day": day,
        "at": store.now_iso(),
        "views": _num(payload.get("views")),
        "like": _num(payload.get("like")),
        "collect": _num(payload.get("collect")),
        "comment": _num(payload.get("comment")),
        "share": _num(payload.get("share")),
        # 后台才有的两项。手填留空就保持 None，不按点赞推算（原版明文禁止）
        "impressions": _num(payload.get("impressions")),
        "fansGained": _num(payload.get("fansGained")),
    }
    metrics = [m for m in (cur.get("metrics") or []) if m.get("day") != day]
    metrics.append(row)
    metrics.sort(key=lambda m: m.get("day", 0))

    latest = baseline.latest_of({**cur, "metrics": metrics, "backend": cur.get("backend")})
    like = latest.get("like")
    collect = latest.get("collect")
    comment = latest.get("comment")
    share = latest.get("share")

    def rate(a, b):
        if a is None or not b:
            return None
        return round(a / b, 4)

    merged = {**latest, "collectRate": rate(collect, like),
              "commentRate": rate(comment, like), "shareRate": rate(share, like)}

    base = _baseline_for(cur.get("accountId") or "")
    compare = _compare(merged, base)

    item = {**cur, "metrics": metrics, "baseline": base,
            "compare": compare or cur.get("compare"),
            "status": "analyzed" if (compare or cur.get("compare")) else cur.get("status", "collecting"),
            "updatedAt": store.now_iso()}
    store.upsert_item("reviews", item, note=f"metrics d{day}")
    return item


def _num(v):
    if v in (None, "", "-"):
        return None
    try:
        return int(float(v))
    except Exception:
        return None


# ---------------------------------------------------------- 后台数据对账

_BACKEND_FIELDS = ("views", "likes", "collects", "comments", "shares")


def _backend_fp(b: dict) -> str:
    """后台数据的业务指纹。

    只用数据本身，**不含 at**。at 是快照写入时间，每次同步都会变，
    拿它比对永远不相等，重复同步会全部算成「更新」。
    """
    return "|".join("" if b.get(f) is None else str(b.get(f))
                    for f in _BACKEND_FIELDS)


@studio_router.post("/reviews/sync-backend")
def sync_reviews_from_backend() -> dict:
    """把后台同步到的笔记补成复盘记录。

    原版 content-review:21 —— 复盘的数据来源是账号档案里的已发布内容，
    所以历史笔记本来就该能进复盘，不是只能复盘工作台发过的。
    已存在的按 contentKey 更新 backend，不碰手填的 metrics。
    """
    shots = datacenter.snapshots()
    if not shots:
        return {"ok": False,
                "message": "还没有后台数据。先点「同步后台数据」，再对账。"}

    latest: dict[str, dict] = {}
    for s in shots:            # snapshots() 已按 at 升序，后面的覆盖前面的
        k = s.get("key")
        if k:
            latest[k] = s

    data = store.read("reviews")
    items = data.get("items") or []
    by_key: dict[str, int] = {}
    for i, r in enumerate(items):
        k = r.get("contentKey") or note_key(r.get("url") or "")
        if k and k not in by_key:
            by_key[k] = i

    today_s = store.now_iso()[:10]
    now = store.now_iso()
    created = updated = skipped = 0
    dirty = False

    for k, s in latest.items():
        pub = s.get("publishedAt") or ""
        days = None
        if pub:
            try:
                days = (date.fromisoformat(today_s) - date.fromisoformat(pub)).days
            except ValueError:
                days = None
        backend = {
            "at": s.get("at"),
            "views": s.get("views"),
            "likes": s.get("likes"),
            "collects": s.get("collects"),
            "comments": s.get("comments"),
            "shares": s.get("shares"),
            "daysSincePublish": days,
            "source": "creator-center",
        }
        fp = _backend_fp(backend)
        backend["fp"] = fp
        if k in by_key:
            i = by_key[k]
            old = items[i].get("backend") or {}
            # 老记录没存 fp，就现算一份比对，避免第一次跑全被当成「更新」
            if (old.get("fp") or _backend_fp(old)) == fp:
                # 数据没变。「发布后第几天」是跟着今天走的，静默刷新，
                # 但不计入 updated —— 否则每天点一次都说更新了 59 条。
                if old.get("daysSincePublish") != days:
                    items[i] = {**items[i],
                                "backend": {**old, "daysSincePublish": days},
                                "updatedAt": now}
                    dirty = True
                skipped += 1
                continue
            items[i] = {**items[i], "contentKey": k, "backend": backend,
                        "updatedAt": now}
            updated += 1
            dirty = True
        else:
            items.append({
                "id": store.new_id("r"),
                "draftId": None, "topicId": None, "accountId": "",
                "platform": "xhs",
                "title": s.get("title") or "",
                "publishedAt": pub,
                "url": "",
                "contentKey": k,
                "metrics": [],
                "backend": backend,
                "baseline": None, "compare": None, "commentGroups": None,
                "conclusion": None, "suggestions": [],
                "status": "collected",
                "createdAt": now, "updatedAt": now,
            })
            by_key[k] = len(items) - 1
            created += 1
            dirty = True

    # 基线一次算完，再回填每条的倍数，避免逐条重算
    base = baseline.compute(items, "", n=20)
    base["computedAt"] = now
    for i, r in enumerate(items):
        m = baseline.latest_of(r)
        if not m:
            continue
        cmp_ = baseline.compare(m, base)
        if cmp_ != r.get("compare") or r.get("baseline") is None:
            items[i] = {**r, "baseline": base,
                        "compare": cmp_ or r.get("compare"),
                        "status": "analyzed" if (cmp_ or r.get("compare"))
                                  else r.get("status", "collecting")}
            dirty = True

    if dirty:
        store.write("reviews", {**data, "items": items},
                    note="sync reviews from backend")
    return {"ok": True, "created": created, "updated": updated,
            "skipped": skipped, "total": len(latest),
            "sampleSize": base.get("sampleSize")}


@studio_router.get("/reviews/{review_id}/stats")
def review_stats(review_id: str) -> dict:
    """这条复盘对应的笔记在后台的数据序列（原版 metricsRef 的形状）。"""
    r = next((x for x in store.list_items("reviews") if x.get("id") == review_id), None)
    if not r:
        raise HTTPException(404, "复盘不存在")
    key = r.get("contentKey") or note_key(r.get("url") or "")
    shots = datacenter.snapshots(key) if key else []
    return {"contentKey": key, "snapshots": shots, "latest": baseline.latest_of(r)}


def _baseline_for(account_id: str) -> dict:
    """基线统一走 core.baseline（原版：同账号最近约 20 条的中位数）。"""
    b = baseline.compute(store.list_items("reviews"), account_id, n=20)
    b["computedAt"] = store.now_iso()
    return b


def _compare(latest: dict, base: dict) -> dict | None:
    return baseline.compare(latest, base)


def note_key(url: str) -> str:
    """从小红书链接里取 note_id，拼成和自有库一致的 key。认不出返回空串。"""
    s = (url or "").strip()
    if not s:
        return ""
    m = re.search(r"/(?:explore|discovery/item|item|note)/([0-9a-fA-F]{20,26})", s)
    if m:
        return f"xhs:{m.group(1).lower()}"
    m = re.search(r"\b([0-9a-f]{20,26})\b", s)
    return f"xhs:{m.group(1)}" if m else ""


@studio_router.post("/reviews/{review_id}/accept-insight")
def accept_insight(review_id: str, payload: dict = Body(...)) -> dict:
    review = next((r for r in store.list_items("reviews") if r.get("id") == review_id), None)
    if not review:
        raise HTTPException(404, "复盘不存在")

    idx = int(payload.get("index", 0))
    suggestions = review.get("suggestions") or []
    if idx >= len(suggestions):
        raise HTTPException(400, "建议不存在")

    s = suggestions[idx]
    insight = {
        "id": store.new_id("i"),
        "text": s.get("text") or "",
        "layer": s.get("layer") or "expression",
        "basis": [review_id],
        "verified": False,
        "verifiedCount": 1,
        "tags": ["复盘经验", f"层面:{s.get('layer') or 'expression'}"],
        "createdAt": store.now_iso(),
        "updatedAt": store.now_iso(),
    }
    store.upsert_item("insights", insight, note="accept insight")

    suggestions[idx] = {**s, "status": "accepted", "insightId": insight["id"]}
    store.upsert_item("reviews", {**review, "suggestions": suggestions,
                                  "updatedAt": store.now_iso()})
    return {"insight": insight}


# ================================================================== 经验库

@studio_router.get("/insights")
def list_insights(layer: str = "", q: str = "") -> dict:
    rows = store.list_items("insights")
    if layer:
        rows = [r for r in rows if r.get("layer") == layer]
    if q:
        n = q.lower()
        rows = [r for r in rows if n in (r.get("text") or "").lower()]
    return {"items": rows}


@studio_router.post("/insights")
def create_insight(payload: dict = Body(...)) -> dict:
    item = {"verified": False, "verifiedCount": 1, "basis": [], "tags": [],
            "createdAt": store.now_iso(), **payload}
    item["id"] = payload.get("id") or store.new_id("i")
    item["updatedAt"] = item["createdAt"]
    store.upsert_item("insights", item, note="create insight")
    return item


@studio_router.delete("/insights/{insight_id}")
def delete_insight(insight_id: str) -> dict:
    return {"ok": store.remove_item("insights", insight_id, note="delete insight")}


# ================================================================== 公式库

@studio_router.get("/formulas")
def list_formulas(category: str = "", q: str = "") -> dict:
    p = paths.PROMPTS_DIR / "formula_library.json"
    if not p.exists():
        return {"items": [], "categories": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"items": [], "categories": []}
    items = data.get("items", [])
    if category:
        items = [x for x in items if x.get("category") == category]
    if q:
        n = q.lower()
        items = [x for x in items
                 if n in (x.get("template") or "").lower()
                 or n in (x.get("example") or "").lower()]
    return {"items": items, "categories": data.get("categories", []),
            "total": len(data.get("items", []))}
