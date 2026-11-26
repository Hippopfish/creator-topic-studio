"""对话式选题助手：带工具的多轮 agent，流式输出。

三个设计决定：

1. **必须流式**。一次调用 15–40 秒，不流式用户会以为程序卡死。
2. **工具结果不落盘**。正文动辄几千字，存进会话会把 chats.json 撑爆。
   会话里只留「这一轮用了什么工具、查到几条」的简报，下一轮拼回上下文。
3. **循环上限 6 轮**。模型偶尔会反复查同一个词，没有上限就是在烧钱。

工具返回的一律是本库真实数据，模型编不出来。但它仍可能「假装读过」，
所以提示词第一条铁律钉死「引用前必须先读」，返回里也一律带 ref，方便界面回链。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import collect_queue, paths, prompts, store, vision

MAX_ROUNDS = 8          # 工具循环上限（最后一轮强制收尾，不再放工具）
HISTORY_TURNS = 20      # 带进上下文的历史轮数
BODY_CLIP = 1800        # 读笔记时正文截断
COMMENT_TOP = 8         # 读笔记时带几条评论
SEARCH_LIMIT = 20       # 查素材池单次上限

_TZ = timezone(timedelta(hours=8))


# ================================================================== 选题规则

def topic_rules() -> str:
    """从 topic_judge.md 切出判断规则，给对话临时加载。

    规则只此一份，不另写一份——两份必然走样。这里只做三件事：
    删掉表单专用段（账号档案 / SeedBrief / 候选样本表格 / 第一步写 SeedBrief）、
    删掉 JSON 输出格式段（对话用工具调用，不回 JSON）、把 {{变量}} 换成实际值。
    判断规则本身一个字没动。
    """
    _meta, body = prompts.load("topic_judge")
    if not body:
        return ""

    th = store.settings().get("thresholds") or {}

    def pct(key: str, default: float) -> str:
        try:
            return f"{float(th.get(key, default)) * 100:.0f}%"
        except (TypeError, ValueError):
            return f"{default * 100:.0f}%"

    s = body
    s = re.sub(r"【本轮账号档案】.*?(?=【近期已有选题)", "", s, flags=re.S)
    s = re.sub(r"【候选样本.*?(?=【互动结构阈值】)", "", s, flags=re.S)
    s = re.sub(r"【第一步.*?(?=【第二步)", "", s, flags=re.S)
    s = re.sub(r"【输出格式.*\Z", "", s, flags=re.S)
    s = (s.replace("{{tCollect}}", pct("collect", 0.5))
          .replace("{{tComment}}", pct("comment", 0.04))
          .replace("{{tShare}}", pct("share", 0.08))
          .replace("{{tShareCollectMax}}", pct("shareCollectMax", 0.3)))
    # 这两格在表单里是预填好的文本，对话里得现场查，不能用「条数」糊过去
    s = (s.replace("{{recentTopics}}", "（用 read_topics 查，不要假设）")
          .replace("{{experiences}}", "（用 search_insights 查，空库就跳过这一条）"))
    s = re.sub(r"\{\{[^}]+\}\}", "本轮要出的条数", s)

    head = ("以下是本工作台的选题判断规则。原文来自 prompts/topic_judge.md，"
            "只删掉了表单专用的取值段和 JSON 输出格式段，判断规则未改一字。\n"
            "原文说「上表」＝ search_material 的返回；说「深读素材」＝ read_note 的返回。\n"
            "原文的【本轮账号档案】已经在最前面的 system 里给过，不用问用户要。\n"
            "你在对话里用工具调用出选题，不要回 JSON。\n\n")
    return head + s.strip()


# ================================================================== 工具声明

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_material",
            "description": (
                "在本地采集库里找笔记，返回标题、发布时间、互动数据和标签。"
                "不返回正文——要引用正文必须再用 read_note 读。"
                "找不到就返回空列表，此时直接告诉用户库里没有，不要编。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string",
                                "description": "关键词，多个词用空格分开（全部命中才算），如「INTP 结婚」"},
                    "days": {"type": "integer",
                             "description": "只看最近 N 天发布的；0 或不填表示不限"},
                    "min_like": {"type": "integer",
                                 "description": "最少点赞数，默认 0 不筛（点赞只做相对参考）"},
                    "limit": {"type": "integer",
                              "description": f"返回几条，默认 10，最多 {SEARCH_LIMIT}"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_note",
            "description": (
                "读一篇笔记的正文和高赞评论。ref 来自 search_material 或 read_topics 返回的 ref。"
                "要引用正文原话、要分析评论区，必须先调用这个。"
                "正文很薄（信息多半在图片里）时会自动读图，返回的 imageText 就是图上抄下来的字。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string",
                            "description": "笔记标识，形如 xhs:6b01b79b000000002903eb96"},
                    "images": {"type": "string",
                               "enum": ["auto", "on", "off"],
                               "description": "要不要读图。auto=正文薄才读（默认）；on=强制读；off=不读"},
                },
                "required": ["ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_topics",
            "description": "读已有选题，用来查哪些角度做过、避免撞车。",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string",
                               "description": "candidate 待定 / adopted 已采纳 / rejected 已驳回，不填为全部"},
                    "limit": {"type": "integer", "description": "返回几条，默认 20"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_topics",
            "description": (
                "把选题写进选题库。**只有在用户明确同意后才能调用。** "
                "标题必须 ≤20 字，标题里不许出现「不是…而是」「你以为…其实」这类句式。"
                "返回里的 created 是真正入库的条数：created=0 就是一条没存上，必须如实说，不许说「存了」。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topics": {
                        "type": "array",
                        "description": "要存的选题，一次可传多条",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "标题，≤20 字"},
                                "reader": {"type": "string", "description": "谁在什么时刻会点开"},
                                "gap": {"type": "string",
                                        "description": "缺口类型：premise 前提 / boundary 边界 / next_step 下一步 / counterexample 反例 / wrong_audience 错人群"},
                                "angle": {"type": "string",
                                          "description": "角度：direct_value / curiosity_gap / counterintuitive / scenario_resonance / decision_help / case_story / timely_interpretation"},
                                "method": {"type": "string",
                                           "description": "方法：knowledge_adaptation / audience_problem / experience_case / content_gap / trend_event / cross_source_synthesis"},
                                "genre": {"type": "string",
                                          "description": "体裁：观察 / 体验 / 证据测评 / 故事 / 比较 / 观点 / 商业证明 / 操作指引"},
                                "mustNotDrift": {"type": "string",
                                                 "description": "绝不能被改写成什么"},
                                "readerGain": {"type": "string", "description": "读者看完能得到什么"},
                                "canDeliver": {"type": "string", "description": "我们凭什么讲得出来"},
                                "evidenceQuote": {"type": "string", "description": "支撑这条选题的原话"},
                                "evidenceRef": {"type": "string",
                                                "description": "原话出自哪篇笔记的 ref，必须真读过"},
                            },
                            "required": ["title"],
                        },
                    },
                },
                "required": ["topics"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_insights",
            "description": "查复盘攒下的经验。空库时返回空列表，说明还没跑过复盘，不要说「根据以往经验」。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "description": "层面：选题 / 账号 / 创作，不填为全部"},
                    "q": {"type": "string", "description": "关键词，可选"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_topic_rules",
            "description": (
                "读本工作台的选题判断规则：四道硬门槛、研究小抄七格、角度/方法/体裁、"
                "标题 11 条硬规则、同批去重。"
                "**用户要你出选题、判断某条能不能做、或准备调 save_topics 之前，必须先调这个。**"
                "无参数。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_collect",
            "description": (
                "把关键词交给采集后台排队采集。**这是唯一能往库里加料的工具，也是一次花钱花时间的动作。**\n"
                "必须先把关键词和代价讲给用户，等他说「采」才能调。绝不自己先斩后奏。\n"
                "一次最多 3 个词。采完自动入库，结果会回到这个对话里。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "1–3 个搜索词。要具体，别用「MBTI」这种大词——大词采回来的都是热门废话。",
                    },
                    "reason": {
                        "type": "string",
                        "description": "一句话：补哪个缺口、为什么现有素材不够。会显示在采集批次上。",
                    },
                },
                "required": ["keywords", "reason"],
            },
        },
    },
]

_NAMES = {t["function"]["name"] for t in TOOLS}


# ================================================================== 工具实现

def _all_contents() -> list[dict]:
    rows: list[dict] = []
    for p in paths.PLATFORMS:
        rows.extend((store.read(f"contents/{p}").get("items") or {}).values())
    return rows


def _pub_ts(r: dict) -> float | None:
    v = r.get("publishedAt") or r.get("published_at") or r.get("time")
    if isinstance(v, (int, float)) and v:
        return v / 1000 if v > 1e12 else float(v)
    if isinstance(v, str) and v[:4].isdigit():
        try:
            return datetime.strptime(v[:10], "%Y-%m-%d").replace(tzinfo=_TZ).timestamp()
        except ValueError:
            return None
    return None


def _pct(a, b) -> str:
    try:
        return f"{float(a) / float(b) * 100:.1f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        return "—"


_TAG_RE = re.compile(r"#[^#\s]*\[话题\]#|#\S+")


def _net_chars(text) -> int:
    """正文去掉话题标签后还剩多少字。

    小红书很多笔记正文就是一串标签，真正的信息全在图里。按原串长度算会把
    「只有标签」的图集误判成「有正文」，所以这里必须先剥标签再数字数。
    """
    s = _TAG_RE.sub("", str(text or ""))
    return len(re.sub(r"\s+", "", s))


def _image_count(rec: dict) -> int:
    v = rec.get("images")
    if isinstance(v, str):
        return len([x for x in v.split(",") if x.strip()])
    return len(v or [])


def _image_hint(rec: dict) -> dict:
    """告诉模型这篇是不是「字在图里」。图里的字我们没采，不能让它假装读过。"""
    n = _image_count(rec)
    net = _net_chars(rec.get("body"))
    # 单图也要管：很多笔记正文只挂一串话题标签，一张图就是全部内容
    if n < 1 or net >= 60:
        return {}
    return {
        "imageCount": n,
        "bodyChars": net,
        "imageHint": (f"正文只有 {net} 字、图 {n} 张，核心信息很可能在图里。"
                      f"图里的文字**没有采集**，评论区只能算旁证。"
                      f"可以拿它证明「有人这样表达/这个问题真实存在」，"
                      f"但不许替它补图里的内容，也不许写成「这篇讲了…」。"
                      f"要用就得让用户自己看图确认。"),
    }


def _fmt_day(r: dict) -> str:
    """发布日期有两种形态：毫秒时间戳、'2023-11-01 01:30' 字符串。"""
    v = r.get("publishedAt") or r.get("published_at") or r.get("time")
    if isinstance(v, (int, float)) and v:
        ts = v / 1000 if v > 1e12 else float(v)
        try:
            return datetime.fromtimestamp(ts, _TZ).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""
    s = str(v or "")
    return s[:10] if s[:4].isdigit() else ""


def tool_search_material(args: dict) -> dict:
    kw = str(args.get("keyword") or "").strip().lower()
    try:
        days = int(args.get("days") or 0)
    except (TypeError, ValueError):
        days = 0
    try:
        min_like = int(args.get("min_like") or 0)
    except (TypeError, ValueError):
        min_like = 0
    try:
        limit = max(1, min(int(args.get("limit") or 10), SEARCH_LIMIT))
    except (TypeError, ValueError):
        limit = 10

    rows = _all_contents()
    total = len(rows)
    # 关键词按空格拆词，要求全部命中（AND）。否则「INTP 结婚」这种组合词一条都搜不到，
    # 模型得自己拆词重试，白白多烧一轮。
    words = [w for w in kw.replace("，", " ").replace(",", " ").split() if w]
    for w in words:
        rows = [r for r in rows
                if w in str(r.get("title") or "").lower()
                or w in str(r.get("body") or "").lower()
                or any(w in str(t).lower() for t in (r.get("tags") or []))]
    if min_like:
        # 括号不能省：`x or 0 >= min_like` 会先算比较，等于把筛选整个废掉
        rows = [r for r in rows if ((r.get("metrics") or {}).get("like") or 0) >= min_like]
    if days > 0:
        cutoff = time.time() - days * 86400
        rows = [r for r in rows if (ts := _pub_ts(r)) is None or ts >= cutoff]

    def sort_key(r: dict):
        ts = _pub_ts(r)
        return (ts is not None, ts or 0, (r.get("metrics") or {}).get("like") or 0)

    rows.sort(key=sort_key, reverse=True)
    hit = rows[:limit]

    out = []
    for r in hit:
        m = r.get("metrics") or {}
        body = str(r.get("body") or "").strip()
        row = {
            "ref": r.get("key") or "",
            "title": r.get("title") or "",
            "publish": _fmt_day(r),
            "like": m.get("like"),
            "collect": m.get("collect"),
            "comment": m.get("comment"),
            "share": m.get("share"),
            "collectRate": _pct(m.get("collect"), m.get("like")),
            "commentRate": _pct(m.get("comment"), m.get("like")),
            "tags": (r.get("tags") or [])[:6],
            "bodyChars": _net_chars(body),
            # 只给开头一小段，够判断相关性；引用仍须 read_note（否则「必须先读」的纪律形同虚设）
            "head": body[:40],
        }
        row.update(_image_hint(r))
        out.append(row)

    return {"库内总数": total, "命中": len(rows), "返回": len(out),
            "note": ("head 只是开头片段，不能直接引用；要引用请先 read_note。"
                     "bodyChars 是去掉话题标签后的正文字数，很小说明字在图里。"),
            "items": out}


def tool_read_note(args: dict) -> dict:
    ref = str(args.get("ref") or "").strip()
    if not ref:
        return {"ok": False, "error": "缺少 ref"}
    plat, _, key = ref.partition(":")
    if not key:
        plat, key = "xhs", ref
        ref = f"xhs:{key}"
    # 存储里的键就是完整的 "xhs:xxxx"，不是裸 id
    item = (store.read(f"contents/{plat}").get("items") or {}).get(ref)
    if not item:
        return {"ok": False, "error": f"库里没有这篇（{ref}）。可能是 ref 写错，或这篇还没采集到。"}

    m = item.get("metrics") or {}
    body = str(item.get("body") or "").strip()

    block = (store.read(f"comments/{plat}").get("items") or {}).get(ref) or {}
    cs = list(block.get("items") or [])
    cs.sort(key=lambda c: c.get("like") or 0, reverse=True)
    top = [{"like": c.get("like"), "text": str(c.get("content") or "")[:240],
            "replies": c.get("subCount") or 0} for c in cs[:COMMENT_TOP]]

    res = {
        "ok": True,
        "ref": ref,
        "title": item.get("title") or "",
        "body": body[:BODY_CLIP],
        "publish": _fmt_day(item),
        "bodyTruncated": len(body) > BODY_CLIP,
        "bodyChars": _net_chars(body),
        "tags": item.get("tags") or [],
        "metrics": {"like": m.get("like"), "collect": m.get("collect"),
                    "comment": m.get("comment"), "share": m.get("share")},
        "commentTotal": (block.get("stats") or {}).get("total") or len(cs),
        "topComments": top,
        "note": "评论为高赞优先的前几条，不是全部。" if cs else "这篇没有采集到评论。",
    }
    # 读图：按需（对齐原版「先存下来，要用才看」），不是采集时就预读。
    # 正文够厚就只看正文，读图这一下没必要花；正文薄才把图递给模型。
    # 读到了就当证据用，读不到就明说没图，不许拿封面标题冒充「图里写了」。
    res.update(_image_hint(item))
    mode = str(args.get("images") or "auto").lower()
    if mode != "off" and (mode == "on" or _net_chars(body) < 60) and _image_count(item) >= 1:
        vr = vision.read(item, force=str(args.get("refresh") or "") == "1")
        res.update(vr)
        if vr.get("imageText"):
            res.pop("imageHint", None)
    return res


def tool_read_topics(args: dict) -> dict:
    status = str(args.get("status") or "").strip()
    try:
        limit = max(1, min(int(args.get("limit") or 20), 50))
    except (TypeError, ValueError):
        limit = 20

    rows = store.list_items("topics")
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows = rows[:limit]
    return {
        "总数": len(store.list_items("topics")),
        "items": [{
            "id": t.get("id"),
            "title": t.get("title"),
            "reader": t.get("reader"),
            "gap": t.get("gap"),
            "angle": t.get("angle"),
            "method": t.get("method"),
            "genre": (t.get("editorialIntent") or {}).get("genre"),
            "status": t.get("status"),
            "ref": ((t.get("evidence") or [{}])[0] or {}).get("ref"),
        } for t in rows],
    }


def tool_save_topics(args: dict, ctx: dict | None = None) -> dict:
    raw = args.get("topics")
    if not isinstance(raw, list) or not raw:
        return {"ok": False, "error": "topics 必须是数组且不能为空"}

    from .ai import _row_to_topic           # 延迟导入，避免循环
    from api.studio_api import run_checks   # 同一套质检，不另造一份

    account_id = str((ctx or {}).get("accountId") or "")

    created, rejected = [], []
    for row in raw[:10]:
        if not isinstance(row, dict):
            continue
        flat = dict(row)
        # 把 chat 形式的 genre / mustNotDrift 折成 editorialIntent
        ei = {}
        for k_src, k_dst in (("genre", "genre"), ("mustNotDrift", "mustNotDrift"),
                             ("readerGain", "readerGain")):
            if flat.get(k_src):
                ei[k_dst] = flat[k_src]
        if ei:
            flat["editorialIntent"] = {**(flat.get("editorialIntent") or {}), **ei}
        if flat.get("evidenceQuote"):
            flat["evidence"] = flat["evidenceQuote"]
        if flat.get("evidenceRef"):
            flat["evidenceRef"] = flat["evidenceRef"]

        item = _row_to_topic(flat, account_id)
        if not item.get("title"):
            rejected.append({"title": row.get("title"), "reason": "标题为空"})
            continue
        item["status"] = "candidate"
        item["source"] = "chat"
        if account_id:
            item["accountId"] = account_id
        item["qualityCheck"] = run_checks(item)
        store.upsert_item("topics", item, note="chat save topic")
        created.append({"id": item["id"], "title": item["title"],
                        "check": f"{(item['qualityCheck'] or {}).get('passCount')}"
                                 f"/{(item['qualityCheck'] or {}).get('total')}",
                        "failed": [i["label"] for i in (item["qualityCheck"] or {}).get("items") or []
                                   if not i["pass"]]})

    no_ref = [c["title"] for c in created
              if any("回链" in f for f in (c.get("failed") or []))]
    return {"ok": True, "created": len(created), "items": created,
            "rejected": rejected,
            "noRef": no_ref,
            "note": "已存为「待定」，用户在选题卡里可以采纳或驳回。"
                    + (f" 其中 {len(no_ref)} 条没挂素材ID（evidenceRef），"
                       f"质检会掉一条；如果是从读过的笔记来的，下一轮补上 ref 重存一次。"
                       if no_ref else "")}


def tool_search_insights(args: dict) -> dict:
    scope = str(args.get("scope") or "").strip()
    kw = str(args.get("q") or "").strip().lower()
    rows = store.list_items("insights")
    total = len(rows)
    if scope:
        rows = [r for r in rows if scope in str(r.get("layer") or "") or scope in str(r.get("scope") or "")]
    if kw:
        rows = [r for r in rows
                if kw in str(r.get("text") or r.get("content") or "").lower()]
    # 已验证的排前面
    rows.sort(key=lambda r: (bool(r.get("verified")), r.get("verifiedCount") or 0), reverse=True)
    return {
        "总数": total,
        "items": [{"text": r.get("text") or r.get("content") or "",
                   "layer": r.get("layer") or r.get("scope") or "",
                   "verified": bool(r.get("verified")),
                   "verifiedCount": r.get("verifiedCount") or 0} for r in rows[:15]],
        "note": "空列表表示还没攒下经验，不要编造，也不要写「根据以往经验」。" if not rows else "",
    }


def tool_read_topic_rules(_args: dict) -> dict:
    rules = topic_rules()
    if not rules:
        return {"ok": False, "error": "prompts/topic_judge.md 读不到，规则文件可能被删了"}
    return {"ok": True, "rules": rules}


def tool_request_collect(args: dict, ctx: dict | None = None) -> dict:
    ctx = ctx or {}
    kws = args.get("keywords")
    if isinstance(kws, str):
        kws = [k for k in re.split(r"[,，、\s]+", kws) if k]
    res = collect_queue.enqueue(
        keywords=kws or [],
        reason=str(args.get("reason") or ""),
        session_id=ctx.get("sessionId") or "",
        account_id=ctx.get("accountId") or "",
    )
    if res.get("ok"):
        res["note"] = (
            f"已排队第 {res.get('position')} 位，预计 {res.get('etaMinutes')}。"
            f"{res.get('note') or ''}"
            f"采完会自动入库并回来告诉你。**现在不要假装已经采到了**——"
            f"库里还是原来那些，这一轮要出的选题只能用现有素材。"
        )
    return res


_DISPATCH = {
    "search_material": tool_search_material,
    "read_note": tool_read_note,
    "read_topics": tool_read_topics,
    "save_topics": tool_save_topics,
    "search_insights": tool_search_insights,
    "read_topic_rules": tool_read_topic_rules,
    "request_collect": tool_request_collect,
}

# 这两个要把结果记到「哪个账号 / 哪个会话」上，必须拿到显式 ctx
_NEED_CTX = {"save_topics", "request_collect"}


def _brief(name: str, args: dict, res: dict) -> str:
    """给界面和下一轮上下文用的一句话简报。"""
    if name == "search_material":
        return f"查素材「{args.get('keyword') or '不限'}」→ 命中 {res.get('命中')} 条，返回 {res.get('返回')}"
    if name == "read_note":
        if not res.get("ok"):
            return f"读笔记失败：{res.get('error')}"
        base = (f"读《{str(res.get('title'))[:16]}》→ 正文 {len(res.get('body') or '')} 字、"
                f"评论 {res.get('commentTotal')} 条")
        if res.get("imageText"):
            tag = "缓存" if res.get("imageTextCached") else f"{res.get('imageCount') or ''} 张"
            return base + f"、读图 {tag}（{len(res.get('imageText'))} 字）"
        if res.get("imageReadError"):
            return base + "、读图失败"
        return base
    if name == "read_topics":
        return f"读选题 → {len(res.get('items') or [])} 条（库内共 {res.get('总数')}）"
    if name == "save_topics":
        if res.get("ok") is False:
            return f"存选题失败：{res.get('error') or '未知原因'}"
        n = int(res.get("created") or 0)
        return f"存选题 → 成功 {n} 条" if n else "存选题 → 0 条（没入库）"
    if name == "search_insights":
        return f"查经验 → {len(res.get('items') or [])} 条"
    if name == "read_topic_rules":
        return f"读选题规则 → {len(res.get('rules') or '')} 字"
    if name == "request_collect":
        if not res.get("ok"):
            return f"提交采集失败：{res.get('message')}"
        return (f"提交采集「{'、'.join(res.get('keywords') or [])}」→ "
                f"排第 {res.get('position')} 位({res.get('etaMinutes')})")
    return name


def run_tool(name: str, args: dict, ctx: dict | None = None) -> dict:
    fn = _DISPATCH.get(name)
    if not fn:
        return {"ok": False, "error": f"没有这个工具：{name}"}
    try:
        # 需要账号/会话的工具显式传 ctx。
        # 不要用 contextvars 兜底：SSE 生成器每 yield 一次就会被任一线程池
        # 重新调度，线程上下文是拷进去的，set 完再 yield 就丢了
        # （踩过：request_collect 存进去的 sessionId 是空，采完没人被通知）。
        if name in _NEED_CTX:
            return fn(args or {}, ctx or {})
        return fn(args or {})
    except Exception as e:                      # 工具炸了要让模型知道，不能静默
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ================================================================== 会话存储

def _chats() -> dict:
    return store.read("chats")


def list_sessions() -> list[dict]:
    rows = _chats().get("items") or []
    return sorted(rows, key=lambda s: s.get("updatedAt") or "", reverse=True)


def get_session(sid: str) -> dict | None:
    for s in _chats().get("items") or []:
        if s.get("id") == sid:
            return s
    return None


def new_session(title: str = "新对话") -> dict:
    now = store.now_iso()
    s = {"id": store.new_id("c"), "title": title, "messages": [],
         "createdAt": now, "updatedAt": now}
    store.upsert_item("chats", s, note="new chat")
    return s


def delete_session(sid: str) -> bool:
    return store.remove_item("chats", sid, note="delete chat")


def add_notice(sid: str, notice: dict) -> None:
    """采集结果写回会话，给界面看。

    单独存 notices，不混进 messages：messages 每一轮都要拼进模型上下文，
    把「采集完成」这种东西塞进去等于每轮白烧 token，而且模型会去回答它。
    """
    if not sid:
        return

    def _fn(data: dict) -> dict:
        for s in data.get("items") or []:
            if s.get("id") == sid:
                ns = list(s.get("notices") or [])
                ns.append({**notice, "at": store.now_iso()})
                s["notices"] = ns[-20:]
                s["updatedAt"] = store.now_iso()
                break
        return data

    store.update("chats", _fn, note="chat notice")


def mark_notices_seen(sid: str) -> bool:
    """用户看过这个会话的通知了。导航上的红点靠它清掉。"""
    if not sid:
        return False

    def _fn(data: dict) -> dict:
        for s in data.get("items") or []:
            if s.get("id") == sid:
                s["noticesSeenAt"] = store.now_iso()
                break
        return data

    store.update("chats", _fn, note="notices seen")
    return True


def pending_notices() -> list[dict]:
    """哪些会话有没看过的采集结果。只给导航红点用，不带正文。"""
    out: list[dict] = []
    for s in list_sessions():
        seen = str(s.get("noticesSeenAt") or "")
        ns = [n for n in (s.get("notices") or []) if str(n.get("at") or "") > seen]
        if ns:
            out.append({"id": s["id"], "title": s.get("title") or "新对话",
                        "count": len(ns), "last": ns[-1].get("text") or ""})
    return out


def _append(sid: str, msg: dict) -> None:
    def _fn(data: dict) -> dict:
        for s in data.get("items") or []:
            if s.get("id") == sid:
                s.setdefault("messages", []).append(msg)
                s["updatedAt"] = store.now_iso()
                # 首条用户消息自动当标题
                if s.get("title") in ("新对话", "", None) and msg.get("role") == "user":
                    s["title"] = str(msg.get("content") or "")[:24] or "新对话"
                break
        return data

    store.update("chats", _fn, note="append chat msg")


def _set_ctx(sid: str, ctx: dict) -> None:
    """选区每轮覆盖，只留最新一条。

    如果每轮都追加，聊十轮就有十份旧选区堆在上下文里，模型会拿第一份的素材出选题。
    """
    if not ctx:
        return

    def _fn(data: dict) -> dict:
        for s in data.get("items") or []:
            if s.get("id") == sid:
                msgs = [m for m in (s.get("messages") or []) if m.get("role") != "ctx"]
                msgs.insert(0, {"role": "ctx", "ctx": ctx, "at": store.now_iso()})
                s["messages"] = msgs
                break
        return data

    store.update("chats", _fn, note="set chat ctx")


def _account_name(aid: str) -> str:
    if not aid:
        return "默认账号"
    for a in store.list_items("accounts"):
        if a.get("id") == aid:
            return str(a.get("displayName") or a.get("nickname") or "未命名")
    return "默认账号"


def _account_archive(account_id: str = "") -> str:
    """账号档案段。原版 topic_judge 的表单里有这一段，对话版早先漏了。

    字段不在这里另写一套，直接借 build_vars——那份是表单版唯一取值处，
    否则两边各写各的，迟早说的不是同一个号。
    """
    try:
        from .ai import build_vars           # 延迟导入，ai.py 引 chat 会成环
        v = build_vars("topic_judge", {"accountId": account_id})
    except Exception:
        return ""

    def clean(x) -> str:
        if isinstance(x, (list, tuple)):
            return "、".join(str(i) for i in x if str(i).strip())
        return str(x or "").strip()

    rows = [
        ("平台", clean(v.get("platform"))),
        ("定位", clean(v.get("positioning"))),
        ("目标读者", clean(v.get("audience"))),
        ("内容支柱", clean(v.get("contentPillars"))),
        ("绝不能被带偏的", clean(v.get("mustNotDrift"))),
        ("不碰的话题", clean(v.get("forbiddenTopics"))),
        ("商业目标", clean(v.get("businessGoal"))),
    ]
    rows = [(k, s) for k, s in rows if s and not s.startswith("（未填")]
    if not rows:
        return ""
    return ("【账号档案】本工作台里填的，出选题必须贴这个号，不是通用 MBTI 号。\n"
            + "\n".join(f"{k}：{s}" for k, s in rows))


def _ctx_text(ctx: dict) -> str:
    """把选区拼成一段 system 文本。数字和 ref 都来自界面，模型编不出来。"""
    lines = ["【本轮选区】用户在选题台里设的，按这个来，不要自己另起炉灶。"]
    lines.append(f"账号：{_account_name(str(ctx.get('accountId') or ''))}")

    keys = [str(k) for k in (ctx.get("contentKeys") or []) if k]
    if keys:
        lines.append(f"指定深读清单：{len(keys)} 条。这是用户勾的，出选题前必须每条都 "
                     f"read_note 读完（正文 + 评论），不许只看标题就写：")
        lines.extend(f"  - {k}" for k in keys[:10])
    else:
        lines.append("指定深读清单：空。自己去 search_material 找，别反问用户要关键词。")

    if ctx.get("seed"):
        lines.append(f"seed（用户给的方向）：{ctx['seed']}")
    if ctx.get("count"):
        lines.append(f"要几条：{ctx['count']}（是上限不是配额，凑不满就少给）")
    rng = str(ctx.get("candidateRange") or "")
    if rng:
        lines.append(f"候选范围：{rng}")

    sb = ctx.get("seedBrief") or {}
    sb_txt = [f"{k}={v}" for k, v in sb.items() if v]
    if sb_txt:
        lines.append("SeedBrief：" + "；".join(sb_txt))

    return "\n".join(lines)


# ================================================================== 调用

def _cfg() -> tuple[dict, str, str]:
    s = store.settings()["ai"]
    return s, store.secret(), str(s.get("baseUrl") or "").rstrip("/")


def _sys_prompt() -> str:
    meta, body = prompts.load("chat")
    return body or "你是选题助手。"


def _pool_text(history: list[dict] | None = None) -> str:
    """素材库实时现状，每轮拼进 system。

    为什么必须后端算、不能让模型自己判断：2026-11-26 实测过一轮——用户问「挑 3 条
    这周能发的」，从提问到答完只有 8 秒，工具只调了「读规则」「读选题」，一条素材都
    没查，回复里却写着「我先看库有没有变」。库当时确实没变，但那是猜的，不是查的。
    真要是中间有采集入库，它就会拿旧印象答，用户还看不出来。

    顺带把「跨轮不等于读过」讲清楚：历史里只留了「读过《X》→ 正文 629 字」这种摘要，
    正文和评论并没有带进上下文。所以上一轮读过、这一轮还想引原文，必须重新读。
    """
    try:
        items = (store.read("contents/xhs").get("items") or {})
    except Exception:
        return ""
    if not items:
        return "【素材库现状】空的，一条都没有。要出选题得先采。"

    vals = list(items.values())
    readable = sum(1 for v in vals if _net_chars(v.get("body")) >= 60)
    withimg = sum(1 for v in vals if (v.get("media") or {}).get("count"))
    last = max((str(v.get("lastSeenAt") or "") for v in vals), default="")
    last_hm = last[11:16] if len(last) >= 16 else "—"

    prev_at = ""
    for m in reversed(history or []):
        if m.get("role") == "assistant" and m.get("at"):
            prev_at = str(m["at"])
            break

    head = (f"【素材库现状·系统实时算的，不是你的印象】\n"
            f"{len(vals)} 条；正文够读（≥60 字，能直接引用）{readable} 条；"
            f"带本地图 {withimg} 条；最近入库 {last_hm}。")
    lines = [head]
    if prev_at:
        new = sum(1 for v in vals if str(v.get("firstSeenAt") or "") > prev_at)
        if new:
            lines.append(f"上一轮（{prev_at[11:16]}）之后新入库 {new} 条 —— **库变了**。"
                         f"接着用旧结论之前，先 search_material 重查一次。")
        else:
            lines.append(f"上一轮（{prev_at[11:16]}）之后新入库 0 条，库没变。"
                         f"上一轮的结论可以沿用，但下面的引用要求不变。")
    lines.append("跨轮记忆里只有「读过哪几篇、多少字」的摘要，正文和评论**不在上下文里**。"
                 "这一轮要引用具体原话、要写评论区的说法，必须本轮 read_note 重读——"
                 "复述自己上一轮写过的句子不算读过。")
    return "\n".join(lines)


def _collect_status_text(sid: str = "") -> str:
    """本会话的采集任务状态，拼进 system。

    模型看不到后台在跑什么，不告诉它就会：① 假装已经采到了；② 说「库里没有，我等你采」，
    而其实十分钟前已经采完入库了。
    """
    if not sid:
        return ""
    rows = collect_queue.for_session(sid, limit=6)
    live = [r for r in rows if r.get("status") in ("queued", "running", "stopping")]
    done = [r for r in rows
            if r.get("status") in ("done", "empty", "failed", "cancelled", "stalled")]
    if not live and not done:
        return ""

    _label = {"queued": "排队中", "running": "采集中", "stopping": "正在停",
              "done": "已完成", "empty": "一条没采到", "failed": "失败",
              "cancelled": "已取消", "stalled": "等太久已停"}
    lines = ["【本会话提交的采集任务】"]
    for r in live:
        lines.append(f"- 「{'、'.join(r.get('keywords') or [])}」"
                     f"{_label.get(r.get('status'), r.get('status'))}"
                     f"（提交于 {str(r.get('createdAt') or '')[11:16]}）"
                     f"：**还没采回来，现在不能用，也不许假装有**")
    for r in done[-2:]:
        st = r.get("importStats") or {}
        tail = f"，{r.get('note')}" if r.get("note") else ""
        lines.append(f"- 「{'、'.join(r.get('keywords') or [])}」"
                     f"{_label.get(r.get('status'), r.get('status'))}"
                     f"，新增 {int(st.get('added') or 0)} 条{tail}")
    if done:
        lines.append("有采集刚结束时，重新 search_material 查一次再下结论——"
                     "库已经变了，别拿旧印象说话。")
    return "\n".join(lines)


def _build_messages(history: list[dict], sid: str = "", account_id: str = "") -> list[dict]:
    msgs = [{"role": "system", "content": _sys_prompt()}]
    acc = _account_archive(account_id)
    if acc:
        msgs.append({"role": "system", "content": acc})
    pool = _pool_text(history)
    if pool:
        msgs.append({"role": "system", "content": pool})
    status = _collect_status_text(sid)
    if status:
        msgs.append({"role": "system", "content": status})
    for m in history[-HISTORY_TURNS:]:
        role = m.get("role")
        if role == "ctx":
            # 选区每轮都要生效，所以走 system；它不是对话历史，不占 user/assistant 的位
            msgs.append({"role": "system", "content": _ctx_text(m.get("ctx") or {})})
            continue
        if role == "user":
            msgs.append({"role": "user", "content": m.get("content") or ""})
        elif role == "assistant":
            text = m.get("content") or ""
            tools = m.get("tools") or []
            if tools:
                # 只留摘要，正文不带进上下文（一篇几百到几千字，几篇就淹了）。
                # 所以这里必须写明「不是读过」，否则模型会把上轮自己的转述当原文复述。
                text += ("\n\n（上一轮查过，只是摘要、正文并没有带到这一轮："
                         + "；".join(t.get("brief") or "" for t in tools) + "）")
            if text.strip():
                msgs.append({"role": "assistant", "content": text})
    return msgs


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


# 「存了」有肯定和否定两种口吻，否定的不能触发补存，否则会硬塞一条脏数据
# deepseek 在没有工具可调时，会把想调的工具写成正文吐出来：
#   <｜｜DSML｜｜ calls> <｜｜DSML｜｜ invoke name="read_note">...
# 这段是无效输出，而且会顶掉真正的答案，必须切掉。
_DSML = re.compile(r"<[|｜]{2}DSML[|｜]{2}.*", re.S)


def _has_markup(t: str) -> bool:
    return "DSML" in str(t or "")


def _strip_markup(t: str) -> str:
    return _DSML.sub("", str(t or "")).strip()


def _finalize(messages: list[dict], s: dict, key: str, base: str,
              temperature: float) -> str:
    """收尾：工具关掉，直接要结论。

    走到这里说明工具轮数用完、或它把工具调用写成了正文。这时上下文里已经有
    这一轮读到的全部结果，只差一段人话。非流式——这种情况不多，值得多等两秒
    换确定性，也免得再把工具标记混进流里。
    """
    nudge = {"role": "system", "content": (
        "工具已经关闭，上面这些就是你这一轮查到的全部内容。现在直接写最终回答："
        "结论先行，该给几条就给几条，每条说清为什么。"
        "不许再说「我再查一下」「接下来我会读」这类过程话，"
        "也不许输出任何工具调用标记（<｜｜DSML｜｜、invoke、parameter）。")}
    payload = {"model": s["model"], "messages": list(messages) + [nudge],
               "stream": False, "temperature": temperature, "max_tokens": 4096}
    if "deepseek" in base.lower():
        payload["thinking"] = {"type": "disabled"}
    try:
        with httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0)) as c:
            r = c.post(f"{base}/chat/completions",
                       headers={"Authorization": f"Bearer {key}"}, json=payload)
            if r.status_code >= 400:
                return ""
            ch = ((r.json().get("choices") or [{}])[0] or {})
            return _strip_markup((ch.get("message") or {}).get("content") or "")
    except Exception:
        return ""


_SAVE_YES = re.compile(r"(?<![要想该])存了|存好|已存|已入库|已加入选题库|已经存")
_SAVE_NO = re.compile(r"(没存|存不上|存不了|存不进去|没有存|并未存)")
# 「存了」的宾语可能是读者行为，不是写库：2026-11-26 实测踩到
# 「说明大家把『不消耗是结婚前提』当干货存了」，被当成"我存了选题"，凭空补存了一条。
_SAVE_OTHER = re.compile(r"(干货|收藏|大家|读者|别人|他们|网友|评论区|用户)")
# 用户这一轮有没有让存。没让存就根本不该进补存流程——
# 铁律是「用户没让存就不要存」，服务端兜底不能反过来替他做决定。
_USER_SAVE = re.compile(r"(存一下|存下|保存|入库|写进去|写进库|记下来|存吧|存这|存第|存起来)")


def _user_wants_save(text: str) -> bool:
    return bool(_USER_SAVE.search(str(text or "")))


def _claims_saved(text: str) -> bool:
    t = str(text or "")
    if _SAVE_NO.search(t):
        return False
    for m in _SAVE_YES.finditer(t):
        # 看「存了」前面的几个字，是"当干货存了/大家存了"就不算
        if not _SAVE_OTHER.search(t[max(0, m.start() - 8): m.start()]):
            return True
    return False


def _repair_save(messages: list[dict], s: dict, key: str, base: str,
                 tctx: dict) -> dict:
    """补一轮：明确点名让它调 save_topics，不是普通重试。

    用非流式（这种情况很少，值得多等两秒换确定性）；tool_choice 仍用 auto，
    不硬指定函数——部分 OpenAI 兼容网关不认 function 级的 tool_choice，会直接 400。
    """
    nudge = {
        "role": "system",
        "content": (
            "你上一条回复对用户说已经存了，但这一轮并没有调用 save_topics，库里根本没有这条。"
            "现在立刻调用 save_topics，把用户刚才同意的那一条原样写进去（保留标题、reader、gap、"
            "angle、method、genre、mustNotDrift、evidence、evidenceRef）。"
            "只能写用户同意的那一条，不要顺手多存。"
            "如果确实没有可存的内容，就只回一句「刚才没存成」，不要编。"
        ),
    }
    payload = {
        "model": s["model"],
        "messages": messages + [nudge],
        "stream": False,
        "temperature": 0.2,
        "max_tokens": 2000,
        "tools": TOOLS,
    }
    # 光靠 auto 不行：实测它会跑去 search_material 又查一遍库，就是不存。
    # 指定函数能成，但要求关掉思考（网关原话：Thinking mode does not support this tool_choice）。
    # 所以按「指定函数 → required → auto」三级降级，哪级通就用哪级。
    if "deepseek" in base.lower():
        payload["thinking"] = {"type": "disabled"}
    choices = [{"type": "function", "function": {"name": "save_topics"}}, "required", "auto"]

    events: list[dict] = []
    tools: list[dict] = []
    msg: dict = {}
    err = ""
    for tc_choice in choices:
        payload["tool_choice"] = tc_choice
        try:
            with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)) as c:
                r = c.post(f"{base}/chat/completions",
                           headers={"Authorization": f"Bearer {key}"}, json=payload)
                if r.status_code >= 400:
                    err = f"HTTP {r.status_code}"
                    continue
                msg = ((r.json().get("choices") or [{}])[0] or {}).get("message") or {}
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            continue
        if msg.get("tool_calls"):
            got = [((t.get("function") or {}).get("name") or "")
                   for t in msg["tool_calls"]]
            if "save_topics" in got:
                break
            err = "模型调了别的工具（" + "、".join(got) + "），不是存库"
    if "save_topics" not in [((t.get("function") or {}).get("name") or "")
                             for t in (msg.get("tool_calls") or [])]:
        return {"created": 0, "events": [
            {"type": "warn", "text": f"补存失败：{err or '模型这次没调存库'}"}], "tools": []}

    created = 0
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        name = fn.get("name") or ""
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {}
        if name not in _NAMES:
            continue
        events.append({"type": "tool", "name": name, "args": args})
        res = run_tool(name, args, tctx)
        brief = _brief(name, args, res)
        events.append({"type": "tool_end", "name": name, "brief": brief,
                       "ok": res.get("ok", True)})
        tools.append({"name": name, "brief": brief,
                      "ref": str(args.get("ref") or "") if name == "read_note" else ""})
        if name == "save_topics" and res.get("ok"):
            created += int(res.get("created") or 0)
    return {"created": created, "events": events, "tools": tools}


def stream_chat(session_id: str, user_text: str, ctx: dict | None = None):
    """产出 SSE 文本块。不抛异常，出错也以 event 形式告诉前端。

    ctx 是选题台传来的本轮选区（勾选的素材/seed/条数/账号）。
    """
    s, key, base = _cfg()
    if not key:
        yield _sse({"type": "error", "code": "NO_API_KEY",
                    "message": "还没配 API Key。去设置里填一下再回来。"})
        return
    if not base or not str(s.get("model") or "").strip():
        yield _sse({"type": "error", "code": "NO_ENDPOINT",
                    "message": "Base URL 或模型没填，去设置里补上。"})
        return

    sess = get_session(session_id)
    if not sess:
        yield _sse({"type": "error", "code": "NO_SESSION", "message": "会话不存在"})
        return

    history = [m for m in (sess.get("messages") or []) if m.get("role") != "ctx"]
    if ctx:
        _set_ctx(session_id, ctx)
        history.insert(0, {"role": "ctx", "ctx": ctx})
    _append(session_id, {"role": "user", "content": user_text, "at": store.now_iso()})

    # 工具函数（save_topics / request_collect）拿到的是这个显式字典，
    # 不靠 contextvars——SSE 生成器每 yield 一次就换线程上下文，set 完就丢
    tctx = {"accountId": str((ctx or {}).get("accountId") or ""),
            "contentKeys": list((ctx or {}).get("contentKeys") or []),
            "sessionId": session_id}

    messages = _build_messages(history + [{"role": "user", "content": user_text}],
                               session_id, str((ctx or {}).get("accountId") or ""))

    temperature = float(s.get("temperature", 0.6))
    thinking = bool(s.get("thinking"))
    used_tools: list[dict] = []
    saved_created = 0
    t0 = time.time()
    text_all: list[str] = []
    tokens = 0

    yield _sse({"type": "start", "sessionId": session_id})

    over_cap = False
    for rnd in range(MAX_ROUNDS):
        # 轮数预算：最后两轮不再给工具，逼它用已经读到的东西下结论。
        # 不给工具是「整段不发 tools」，不是 tool_choice=none——实测后者在 deepseek 上
        # 会把工具调用写成正文吐出来（<｜｜DSML｜｜ calls> 这种），用户看到一屏乱码。
        last_round = rnd == MAX_ROUNDS - 1
        near_end = rnd >= MAX_ROUNDS - 2
        msgs_now = messages
        if near_end and not last_round:
            msgs_now = messages + [{"role": "system", "content": (
                f"【轮次预算】这是第 {rnd + 1} 轮，最多 {MAX_ROUNDS} 轮，还剩 "
                f"{MAX_ROUNDS - rnd} 轮。够用的料就够用了：别再扩大检索，"
                f"下一轮开始必须动笔。")}]
        payload = {
            "model": s["model"],
            "messages": msgs_now,
            "stream": True,
            "temperature": temperature,
            "max_tokens": 4096,
        }
        if not last_round:
            payload["tools"] = TOOLS
            payload["tool_choice"] = "auto"
        else:
            msgs_now = list(msgs_now) + [{"role": "system", "content": (
                "【工具已关闭】这一轮不能再调用任何工具，也不会再返回工具结果。"
                "直接用已经读到的素材写结论。不要输出任何工具调用标记"
                "（形如 <｜｜DSML｜｜ calls>、invoke、parameter 的片段），那是无效输出。")}]
            payload["messages"] = msgs_now
        if "deepseek" in base.lower():
            # 对话里开思考会让首字延迟十几秒，收益不大；收尾轮必须关。
            payload["thinking"] = {
                "type": "enabled" if (thinking and not near_end) else "disabled"}

        calls: dict[int, dict] = {}
        round_text: list[str] = []
        finish = ""

        try:
            with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)) as c:
                with c.stream("POST", f"{base}/chat/completions",
                              headers={"Authorization": f"Bearer {key}"},
                              json=payload) as r:
                    if r.status_code >= 400:
                        body = r.read().decode("utf-8", "ignore")[:300]
                        yield _sse({"type": "error", "code": f"HTTP_{r.status_code}",
                                    "message": f"{r.status_code}：{body}"})
                        _append(session_id, {"role": "assistant",
                                             "content": "（这一轮调用失败了）",
                                             "at": store.now_iso()})
                        return
                    for line in r.iter_lines():
                        if not line:
                            continue
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except Exception:
                            continue
                        if obj.get("usage"):
                            tokens += int((obj["usage"] or {}).get("total_tokens") or 0)
                        ch = (obj.get("choices") or [{}])[0] or {}
                        finish = ch.get("finish_reason") or finish
                        delta = ch.get("delta") or {}
                        if delta.get("content"):
                            round_text.append(delta["content"])
                            yield _sse({"type": "delta", "text": delta["content"]})
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            slot = calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
        except Exception as e:
            yield _sse({"type": "error", "code": "API_ERROR",
                        "message": f"{type(e).__name__}: {e}"})
            break

        merged = "".join(round_text)
        if merged:
            text_all.append(merged)

        if not calls:
            break       # 没有工具调用 = 这一轮说完了

        if not merged:
            # 只调工具不说话的情形，给界面一点反馈
            yield _sse({"type": "step", "text": "正在查库…"})

        ordered = [calls[i] for i in sorted(calls)]
        messages.append({
            "role": "assistant",
            "content": merged or None,
            "tool_calls": [{"id": c["id"] or f"call_{i}", "type": "function",
                            "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                           for i, c in enumerate(ordered)],
        })

        for i, c in enumerate(ordered):
            name = c["name"]
            try:
                args = json.loads(c["args"] or "{}")
            except Exception:
                args = {}
            yield _sse({"type": "tool", "name": name, "args": args})
            res = run_tool(name, args, tctx)
            if name == "save_topics" and res.get("ok"):
                saved_created += int(res.get("created") or 0)
            brief = _brief(name, args, res)
            # ref 要存下来，否则刷新页面后历史里的「看原文」就没了
            used_tools.append({"name": name, "brief": brief,
                               "ref": str(args.get("ref") or "") if name == "read_note" else ""})
            yield _sse({"type": "tool_end", "name": name, "brief": brief,
                        "ok": res.get("ok", True)})
            messages.append({
                "role": "tool",
                "tool_call_id": c["id"] or f"call_{i}",
                "content": json.dumps(res, ensure_ascii=False)[:12000],
            })

        if finish == "length":
            yield _sse({"type": "warn", "text": "输出被长度上限截断了"})
            break
    else:
        over_cap = True

    raw_final = "".join(text_all)
    final = _strip_markup(raw_final)

    # 收尾兜底。两种情形都说明「它其实没答」：
    #   ① 轮数用完了还在调工具，最后只留一句「我先读规则，再查库」；
    #   ② 把工具调用写成正文吐出来（deepseek 在禁用工具时会这样），正文里带 DSML 标记。
    # 这时原来那段是过程叙述，对用户没价值，重问一次拿结论替换掉。
    if used_tools and (over_cap or _has_markup(raw_final) or len(final) < 80):
        yield _sse({"type": "step", "text": "正在收尾…"})
        extra = _finalize(messages, s, key, base, temperature)
        if extra:
            final = extra
        else:
            yield _sse({"type": "warn", "text": "收尾没成功，只保留上面查到的过程"})

    # 嘴上说「存了」但这一轮根本没调 save_topics——实测真发生过：
    # 用户回「存一下第一条吧」，它回了「存了…」，选题卡里却没有。
    # 这种不能只靠提示词，得在服务端兜：逼它补一次真写库，补不上就直说。
    if _user_wants_save(user_text) and _claims_saved(final) and saved_created == 0:
        yield _sse({"type": "warn",
                    "text": "刚才那句「存了」不算数——这一轮没真写库，我在补存"})
        fixed = _repair_save(messages, s, key, base, tctx)
        for ev in fixed["events"]:
            yield _sse(ev)
        used_tools.extend(fixed["tools"])
        if fixed["created"]:
            saved_created = fixed["created"]
            final += f"\n\n（补存 {fixed['created']} 条已入库，选题卡里现在有了。）"
        else:
            yield _sse({"type": "warn",
                        "text": "补存也没成功，选题卡里还是没有。你再说一次「存第几条」。"})
            final += "\n\n（这一条其实没存进选题库。要不要我重存一次？）"

    _append(session_id, {"role": "assistant", "content": final,
                         "tools": used_tools, "at": store.now_iso()})
    yield _sse({"type": "done", "ms": int((time.time() - t0) * 1000),
                "tokens": tokens, "tools": used_tools})
