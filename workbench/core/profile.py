"""档案台：拆解自己的账号，产出三份档案。

规则来源：Beav `account-learning`（原文见 选题工作流/beav-archive/builtin-skills/account-learning/）。
下面几条是硬约束，改代码前先读一遍：

- 产物只有三处：风格包 / 空间记忆 / CreatorProfile。**不新建其他档案文件。**
- 分类按**内容机制**（同一结构、同一开场承诺、同一信息顺序、同一形式），不按话题。
- 每类至少 2 篇深读证据才建模块；只有 1 篇的写进索引「候选」，不建规则文件。
- 每条规则、事实都要能指回帖子（note_id）和样本数；**只读到标题的帖子不能支撑正文写法结论。**
- 本人明说 / 样本归纳 / 推断三层分开写，推断标「待确认」；评论区只描述受众。
- 用户明确说过的要求优先于任何样本规律；新样本只能提出修改建议。
- 多篇共同出现才写成规则；单篇特例写成「可选结构 + 适用条件」；样本 <3 标「暂定（n=）」。
- 不为凑结构编造类型、事实或口头禅；没有证据的维度写「未知」。

执行方式：点一次「开始拆解」，后台串行跑完所有步骤，每步失败只影响那一步（照原版
「某一步不可用时，其余部分照常保存，并在结果底部列出未完成原因」）。
"""
from __future__ import annotations

import json
import re
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import ai, paths, store

_TZ = timezone(timedelta(hours=8))
_LOCK = threading.RLock()
_THREAD: dict = {"t": None}

# 步骤表：key / 中文名 / 是否调用模型
STEPS = [
    ("prepare", "整理目录", False),
    ("classify", "分内容类型", True),
    ("rules", "提炼每类写法", True),
    ("memory", "记住你是谁", True),
    ("creator", "写运营规划", True),
    ("selfcheck", "试写验收", True),
]

MEMORY_SLOTS = ("identity", "expertise", "stance", "story", "offering",
                "positioning", "audience", "voice", "content_pattern", "constraint")
SLOT_NAMES = {
    "identity": "身份与经历", "expertise": "专长与边界", "stance": "一贯观点",
    "story": "常讲的故事", "offering": "产品与服务", "positioning": "定位",
    "audience": "受众", "voice": "声音", "content_pattern": "内容规律",
    "constraint": "约束",
}


# ================================================================== 状态

def _owner() -> dict:
    return store.read("profile/owner")


def _style() -> dict:
    return store.read("profile/style")


def _memory() -> dict:
    return store.read("profile/memory")


def _creator() -> dict:
    return store.read("profile/creator")


def _job() -> dict | None:
    j = store.read("profile/job") or {}
    return j or None


def status() -> dict:
    owner = _owner()
    style = _style()
    mem = _memory()
    cre = _creator()
    job = _job()
    own = own_contents()
    running = bool(job and job.get("status") == "running" and _alive(job))
    if job and job.get("status") == "running" and not running:
        job = {**job, "status": "interrupted",
               "error": job.get("error") or "服务重启，任务中断了"}
    index = style.get("index") or []
    return {
        "account": owner.get("account") or {},
        "state": owner.get("state") or ("ready" if index else "empty"),
        "version": owner.get("version") or 0,
        "builtAt": owner.get("builtAt"),
        "lastLearnAt": owner.get("lastLearnAt"),
        "job": job if job else None,
        "running": running,
        "own": {
            "count": len(own),
            "firstAt": _day(own[-1].get("publishedAt")) if own else None,
            "latestAt": _day(own[0].get("publishedAt")) if own else None,
            "lastSyncAt": owner.get("lastSyncAt"),
            "lastSync": owner.get("lastSync") or None,
            "syncing": bool((owner.get("lastSync") or {}).get("status") == "running"),
        },
        "counts": {
            "catalog": (owner.get("counts") or {}).get("catalog") or 0,
            "deep": (owner.get("counts") or {}).get("deep") or 0,
            "types": len([x for x in index if x.get("status") == "active"]),
            "candidates": len([x for x in index if x.get("status") != "active"]),
            "memory": len([m for m in (mem.get("items") or []) if not m.get("dropped")]),
        },
        "catalogFacts": owner.get("catalogFacts") or {},
        "records": style.get("records") or owner.get("records") or [],
        "pending": owner.get("pending") or [],
    }


def _alive(job: dict) -> bool:
    t = _THREAD.get("t")
    return bool(t and t.is_alive() and (job.get("id") or "") == _THREAD.get("id"))


def product() -> dict:
    """三份产物 + 待确认项。前端只读这个。"""
    owner = _owner()
    style = _style()
    return {
        "account": owner.get("account") or {},
        "builtAt": owner.get("builtAt"),
        "lastLearnAt": owner.get("lastLearnAt"),
        "version": owner.get("version") or 0,
        "catalogFacts": owner.get("catalogFacts") or {},
        "style": {
            "index": style.get("index") or [],
            "common": style.get("common") or {},
            "modules": style.get("modules") or {},
            "records": style.get("records") or [],
        },
        "memory": {
            "items": [m for m in (_memory().get("items") or []) if not m.get("dropped")],
            "pending": [m for m in (_memory().get("items") or []) if m.get("status") == "pending"],
        },
        "creator": _creator(),
        "samples": owner.get("samples") or [],
    }


# ================================================================== 自己的内容

def own_contents(platform: str = "xhs") -> list[dict]:
    data = store.read(f"own/{platform}")
    items = list((data.get("items") or {}).values())
    items.sort(key=lambda r: _ts(r.get("publishedAt")) or 0, reverse=True)
    return items


def _ts(v) -> float | None:
    """把毫秒时间戳 / 秒 / ISO 字符串统一成 epoch 秒。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        n = float(v)
        return n / 1000.0 if n > 1e11 else n
    s = str(v).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{10,13}", s):
        n = float(s)
        return n / 1000.0 if n > 1e11 else n
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=_TZ).timestamp()
        except Exception:
            continue
    return None


def _day(v) -> str:
    t = _ts(v)
    if not t:
        return ""
    return datetime.fromtimestamp(t, _TZ).strftime("%Y-%m-%d")


def _form(rec: dict) -> str:
    if rec.get("videoUrl"):
        return "视频"
    if (rec.get("images") or []) and len(rec.get("images") or []) > 1:
        return "图文（多图）"
    if rec.get("images"):
        return "图文（单图）"
    return "纯文字"


def _med(vals: list) -> float | None:
    clean = [v for v in vals if isinstance(v, (int, float))]
    return round(statistics.median(clean), 1) if clean else None


def catalog_facts(contents: list[dict]) -> dict:
    """照原版 `catalogFacts`：宿主对目录做的计数和分布，用来分类型、看节奏和标题习惯。"""
    n = len(contents)
    if not n:
        return {"total": 0}
    days = [d for d in (_day(r.get("publishedAt")) for r in contents) if d]
    gaps = []
    if len(days) >= 2:
        ds = sorted(datetime.strptime(d, "%Y-%m-%d") for d in set(days))
        gaps = [(ds[i + 1] - ds[i]).days for i in range(len(ds) - 1)]
    titles = [str(r.get("title") or "") for r in contents]
    tl = [len(t) for t in titles if t]
    forms: dict[str, int] = {}
    for r in contents:
        forms[_form(r)] = forms.get(_form(r), 0) + 1
    tags: dict[str, int] = {}
    for r in contents:
        for t in (r.get("tags") or []):
            tags[str(t)] = tags.get(str(t), 0) + 1
    hours = [datetime.fromtimestamp(_ts(r.get("publishedAt")), _TZ).hour
             for r in contents if _ts(r.get("publishedAt"))]

    def rate(pred) -> float | None:
        vals = [bool(pred(t)) for t in titles if t]
        return round(sum(vals) / len(vals), 2) if vals else None

    by_form = {}
    for f in forms:
        sub = [r for r in contents if _form(r) == f]
        m = [r.get("metrics") or {} for r in sub]
        by_form[f] = {"n": len(sub), "like": _med([x.get("like") for x in m]),
                      "collect": _med([x.get("collect") for x in m]),
                      "comment": _med([x.get("comment") for x in m])}

    return {
        "total": n,
        "firstAt": min(days) if days else None,
        "lastAt": max(days) if days else None,
        "spanDays": ((datetime.strptime(max(days), "%Y-%m-%d")
                      - datetime.strptime(min(days), "%Y-%m-%d")).days if days else None),
        "forms": forms,
        "byForm": by_form,
        "gapMedianDays": _med(gaps),
        "perWeek": round(n / max(1, ((datetime.strptime(max(days), "%Y-%m-%d")
                                      - datetime.strptime(min(days), "%Y-%m-%d")).days / 7)
                                  if days else 1), 1) if days else None,
        "titleLen": {"median": _med(tl), "min": min(tl) if tl else None,
                     "max": max(tl) if tl else None},
        "titleWithNumber": rate(lambda t: any(c.isdigit() for c in t)),
        "titleWithQuestion": rate(lambda t: "?" in t or "？" in t),
        "titleWithExclaim": rate(lambda t: "!" in t or "！" in t),
        "medianLike": _med([(r.get("metrics") or {}).get("like") for r in contents]),
        "medianCollect": _med([(r.get("metrics") or {}).get("collect") for r in contents]),
        "medianComment": _med([(r.get("metrics") or {}).get("comment") for r in contents]),
        "topTags": sorted(tags.items(), key=lambda x: -x[1])[:8],
        "publishHours": sorted({datetime.fromtimestamp(t, _TZ).hour for t in hours}),
    }


def select_deep(contents: list[dict], limit: int = 12) -> list[dict]:
    """选深读样本，照原版三种 selectionReason：数据最好 / 最近发布 / 代表某种形式。"""
    picked: dict[str, dict] = {}

    def take(rec: dict, reason: str) -> None:
        key = rec.get("key") or ""
        if key and key not in picked and len(picked) < limit:
            picked[key] = {"rec": rec, "reason": reason}

    # 1) 每种形式先各取点赞最高的一条（保证形式覆盖）
    by_form: dict[str, list[dict]] = {}
    for r in contents:
        by_form.setdefault(_form(r), []).append(r)
    for form, group in by_form.items():
        group.sort(key=lambda r: -((r.get("metrics") or {}).get("like") or 0))
        if group:
            take(group[0], f"代表「{form}」这种形式")

    # 2) 剩下的按点赞从高到低补：互动数据最好
    rest = sorted(contents, key=lambda r: -((r.get("metrics") or {}).get("like") or 0))
    for r in rest:
        if len(picked) >= limit:
            break
        take(r, "互动数据最好")

    # 3) 还不够就按最近发布补
    for r in contents:
        if len(picked) >= limit:
            break
        take(r, "最近发布")

    return list(picked.values())


# ================================================================== 文本组装

def _row(i: int, rec: dict, deep_keys: set) -> str:
    m = rec.get("metrics") or {}
    mark = "★深读" if (rec.get("key") in deep_keys) else ""
    return (f"{i}. [{rec.get('id')}] {str(rec.get('title') or '（无标题）')[:60]}"
            f"｜{_form(rec)}｜赞{m.get('like')} 藏{m.get('collect')} 评{m.get('comment')}"
            f"｜{_day(rec.get('publishedAt')) or '日期未知'}｜正文{len(rec.get('body') or '')}字 {mark}")


def _catalog_table(contents: list[dict], deep_keys: set) -> str:
    return "\n".join(_row(i + 1, r, deep_keys) for i, r in enumerate(contents))


def _deep_block(idx: int, item: dict, body_limit: int = 1600) -> str:
    r = item["rec"]
    m = r.get("metrics") or {}
    body = str(r.get("body") or "")
    if len(body) > body_limit:
        body = body[:body_limit] + "…（后略）"
    tags = "、".join(r.get("tags") or []) or "无"
    return (
        f"【深读 {idx}】note_id={r.get('id')}｜{r.get('title') or '（无标题）'}\n"
        f"形式：{_form(r)}｜发布：{_day(r.get('publishedAt')) or '未知'}"
        f"｜赞{m.get('like')} 藏{m.get('collect')} 评{m.get('comment')} 转{m.get('share')}\n"
        f"标签：{tags}\n"
        f"入选原因：{item['reason']}\n"
        f"正文：\n{body or '（正文为空——这一篇不能用来判断正文怎么写）'}\n"
    )


def _deep_sources(items: list[dict], body_limit: int = 1600) -> str:
    return "\n".join(_deep_block(i + 1, it, body_limit) for i, it in enumerate(items))


def _facts_text(f: dict) -> str:
    if not f or not f.get("total"):
        return "（目录为空）"
    forms = "、".join(f"{k} {v} 篇" for k, v in (f.get("forms") or {}).items())
    tags = "、".join(f"{t}({c})" for t, c in (f.get("topTags") or [])) or "无"
    return (
        f"目录共 {f['total']} 篇，{f.get('firstAt') or '?'} 至 {f.get('lastAt') or '?'}"
        f"（跨 {f.get('spanDays')} 天），约每周 {f.get('perWeek')} 篇，"
        f"相邻发帖间隔中位数 {f.get('gapMedianDays')} 天。\n"
        f"形式分布：{forms}\n"
        f"标题长度：中位 {((f.get('titleLen') or {}).get('median'))} 字，"
        f"范围 {((f.get('titleLen') or {}).get('min'))}–{((f.get('titleLen') or {}).get('max'))} 字；"
        f"带数字 {f.get('titleWithNumber')}，带问号 {f.get('titleWithQuestion')}，"
        f"带感叹号 {f.get('titleWithExclaim')}（比例，样本 {f['total']}）。\n"
        f"互动中位数：赞 {f.get('medianLike')}｜藏 {f.get('medianCollect')}｜评 {f.get('medianComment')}。\n"
        f"各形式表现：" + "；".join(f"{k} 赞中位 {(v or {}).get('like')}"
                                    for k, v in (f.get("byForm") or {}).items()) + "。\n"
        f"高频标签：{tags}\n"
        f"发布时段（小时）：{f.get('publishHours')}"
    )


# ================================================================== 启动

def start_build(homepage_url: str = "", limit: int = 12, mode: str = "init") -> dict:
    if not store.secret():
        return {"ok": False, "code": "NO_API_KEY",
                "message": "档案台要调用模型来判断内容类型和写法，请先在设置里填 API Key。"}
    s = store.settings()["ai"]
    if not str(s.get("baseUrl") or "").strip() or not str(s.get("model") or "").strip():
        return {"ok": False, "code": "NO_ENDPOINT", "message": "设置里的 Base URL 或模型还没填。"}
    if _THREAD.get("t") and _THREAD["t"].is_alive():
        return {"ok": False, "code": "BUSY", "message": "已经在拆解了，等这一轮跑完。"}

    contents = own_contents()
    if not contents:
        return {"ok": False, "code": "NO_OWN_CONTENT",
                "message": "还没有你自己的帖子。先把主页链接采进来（拆解台顶部的输入框）。"}
    if mode == "init" and len(contents) < 3:
        return {"ok": False, "code": "TOO_FEW",
                "message": f"只有 {len(contents)} 篇，样本太少。原版要求每类至少 2 篇正文，"
                           f"少于 3 篇连一类都建不起来。先多采一些再来。"}

    job_id = store.new_id("pj")
    job = {
        "id": job_id, "mode": mode, "status": "running", "step": "", "stepIndex": 0,
        "total": len(STEPS), "startedAt": store.now_iso(), "endedAt": None,
        "error": None, "warnings": [], "usage": {"calls": 0, "tokens": 0},
        "steps": [{"key": k, "name": n, "ai": a, "status": "pending", "detail": ""}
                  for k, n, a in STEPS],
        "homepageUrl": (homepage_url or "").strip(),
    }
    store.write("profile/job", job, note="profile job start")

    def _run() -> None:
        try:
            _pipeline(job, contents, limit, mode)
        except Exception as e:
            job["status"] = "failed"
            job["error"] = f"{type(e).__name__}: {e}"
            job["endedAt"] = store.now_iso()
            store.write("profile/job", job, note="profile job failed")

    t = threading.Thread(target=_run, daemon=True)
    _THREAD["t"] = t
    _THREAD["id"] = job_id
    t.start()
    return {"ok": True, "jobId": job_id}


_PROFILE_PATH_RE = re.compile(r"/user/profile/([0-9a-zA-Z]+)")
_ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")


def resolve_homepage(raw: str) -> dict:
    """把各种形态的主页链接归一成 MediaCrawler 认的格式。

    它只认两种：`/user/profile/<id>` 完整链接，或纯 24 位 id。而 App 的「分享主页」
    给的是 `xhslink.cn/o/xxx` 短链，直接丢过去会报 Unable to parse creator info。
    这里把短链跟一次跳转，拿回真正的 user_id 和 xsec_token。
    """
    text = (raw or "").strip().strip("\"'“”")
    if not text:
        return {"ok": False, "message": "先填你的主页链接"}
    # 分享文案里常夹着一堆话，抠出第一个链接
    m = re.search(r"https?://[^\s\u4e00-\u9fa5\"'“”]+", text)
    if m:
        text = m.group(0)
    text = text.rstrip("，。；、）)")

    if _ID_RE.match(text):
        return {"ok": True, "url": text, "userId": text.lower(),
                "token": "", "resolved": False, "from": "id"}

    hit = _PROFILE_PATH_RE.search(text)
    if hit:
        return {"ok": True, "url": text, "userId": hit.group(1),
                "token": _qs(text, "xsec_token"), "resolved": False, "from": "profile-url"}

    if not re.match(r"^https?://", text):
        return {"ok": False,
                "message": "这不像主页链接。用 App 里「我 → 分享主页 → 复制链接」，或者直接填主页地址里的那串 id。"}

    # 短链：跟一次跳转
    try:
        import httpx
        with httpx.Client(timeout=20, follow_redirects=True,
                          headers={"User-Agent": _UA}) as c:
            r = c.get(text)
        final = str(r.url)
        hit = _PROFILE_PATH_RE.search(final)
        if hit:
            return {"ok": True, "url": final, "userId": hit.group(1),
                    "token": _qs(final, "xsec_token"), "resolved": True,
                    "from": "short-link", "hops": len(r.history)}
        return {"ok": False,
                "message": f"短链能打开，但里面没有主页地址（落到了 {final[:60]}）。"
                           f"可能是作品链接而不是主页链接，换个主页分享链接试试。"}
    except Exception as e:
        return {"ok": False,
                "message": f"短链解析失败（{type(e).__name__}：{e}）。可以直接填主页地址里的那串 id。"}


_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def _qs(url: str, key: str) -> str:
    try:
        from urllib.parse import parse_qs, urlparse
        return (parse_qs(urlparse(url).query).get(key) or [""])[0]
    except Exception:
        return ""


def crawl_own(homepage_url: str, max_notes: int = 20) -> dict:
    """按账号采自己的帖子。复用采集台的链路，只换 crawlerType=creator。"""
    from . import importer, jobs

    r = resolve_homepage(homepage_url)
    if not r.get("ok"):
        return r
    url = r["url"]
    if r.get("resolved"):
        print(f"[档案台] 短链已解析：{url[:80]}")

    res = jobs.start(platform="xhs", keywords=[], max_notes=int(max_notes),
                     with_comments=False, crawler_type="creator", creator_id=url,
                     role="own")
    if not res.get("ok"):
        return res

    job_id, batch_id = res["jobId"], res["batchId"]
    owner = _owner()
    store.write("profile/owner", {
        **owner,
        "account": {**(owner.get("account") or {}),
                    "platform": "xhs", "homepageUrl": url,
                    "userId": r.get("userId") or "",
                    "homepageInput": (homepage_url or "").strip()},
        "lastSync": {"jobId": job_id, "batchId": batch_id, "status": "running",
                     "at": store.now_iso()},
    }, note="profile sync start")

    def _watch() -> None:
        deadline = time.time() + 30 * 60
        j = {}
        while time.time() < deadline:
            time.sleep(2)
            j = jobs.get(job_id) or {}
            if j.get("status") != "running":
                break
        status = j.get("status") or "unknown"
        # 收尾时采集端还要补写 note（没采到的原因）和批次状态，等一拍再读，否则读到空
        time.sleep(2)
        j = jobs.get(job_id) or j
        note = j.get("note") or ""
        if not note:
            b = next((x for x in store.list_items("batches")
                      if x.get("id") == batch_id), None) or {}
            note = b.get("note") or ""
        out = {"jobId": job_id, "batchId": batch_id, "status": status,
               "at": store.now_iso(), "import": None, "saved": 0,
               "note": note}
        if status in ("done", "empty", "stopped"):
            try:
                imp = importer.import_batch(batch_id)
                out["import"] = imp
                out["saved"] = int(imp.get("added") or 0)
            except Exception as e:
                out["import"] = {"ok": False, "message": f"{type(e).__name__}: {e}"}
        o = _owner()
        store.write("profile/owner", {
            **o,
            "lastSyncAt": store.now_iso() if out["saved"] else o.get("lastSyncAt"),
            "lastSync": out,
            "account": {**(o.get("account") or {}),
                        "nickName": (o.get("account") or {}).get("nickName")
                                    or _guess_nick(own_contents())},
        }, note="profile sync end")

    threading.Thread(target=_watch, daemon=True).start()
    return {"ok": True, **res}


def _step(job: dict, key: str, status: str, detail: str = "") -> None:
    for s in job["steps"]:
        if s["key"] == key:
            s["status"] = status
            if detail:
                s["detail"] = detail
    job["step"] = key
    store.write("profile/job", job, note=f"profile step {key} {status}")


def _call(job: dict, task: str, variables: dict) -> dict:
    job["usage"]["calls"] = int(job["usage"].get("calls") or 0) + 1
    res = ai.run(task, {"vars": variables})
    usage = (res or {}).get("usage") or {}
    job["usage"]["tokens"] = int(job["usage"].get("tokens") or 0) + int(usage.get("total_tokens") or 0)
    return res or {}


# ================================================================== 流水线

def _pipeline(job: dict, contents: list[dict], limit: int, mode: str) -> None:
    warnings: list[str] = []
    platform = "xhs"
    deep = select_deep(contents, limit)
    deep_keys = {d["rec"].get("key") for d in deep}
    facts = catalog_facts(contents)

    # ---------------- 1 整理目录（纯代码）
    _step(job, "prepare", "running")
    t0 = time.time()
    if mode == "init":
        owner = _owner()
        acc = {**{
            "platform": platform,
            "homepageUrl": job.get("homepageUrl") or "",
            "nickName": _guess_nick(contents),
            "capturedAt": store.now_iso(),
        }, **(owner.get("account") or {})}
        if job.get("homepageUrl"):
            acc["homepageUrl"] = job["homepageUrl"]
        store.write("profile/owner", {
            **owner, "account": acc, "state": "building",
            "counts": {"catalog": len(contents), "deep": len(deep)},
            "pending": [],
        }, note="profile owner prepare")
    _step(job, "prepare", "done", f"目录 {len(contents)} 篇，深读 {len(deep)} 篇")

    table = _catalog_table(contents, deep_keys)
    facts_text = _facts_text(facts)
    sources = _deep_sources(deep)

    # ---------------- 2 分内容类型
    _step(job, "classify", "running")
    prev = _style()
    prev_index = prev.get("index") or []
    prev_hint = ""
    if prev_index:
        prev_hint = "\n".join(
            f"- {x.get('name')}（{x.get('id')}，样本 {x.get('deepCount')} 篇）" for x in prev_index
        )
    res = _call(job, "profile_classify", {
        "accountName": (_owner().get("account") or {}).get("nickName") or "（未知）",
        "platform": "小红书",
        "catalogFacts": facts_text,
        "catalogTable": table,
        "deepSamples": sources,
        "nCatalog": len(contents),
        "nDeep": len(deep),
        "existingTypes": prev_hint or "（第一次拆，没有已有类型）",
        "mode": "初始化" if mode == "init" else "补充学习",
        "today": store.now_iso()[:10],
    })
    if not res.get("ok") or not isinstance(res.get("json"), dict):
        _step(job, "classify", "failed", (res.get("message") or "模型没返回可用 JSON")[:120])
        job["status"] = "failed"
        job["error"] = "分类这一步失败了，没有类型就写不出规则。可以直接重试。"
        job["endedAt"] = store.now_iso()
        store.write("profile/job", job, note="profile job failed")
        return
    data = res["json"]
    types = [t for t in (data.get("types") or []) if isinstance(t, dict) and t.get("id")]
    candidates = [c for c in (data.get("candidates") or []) if isinstance(c, dict)]
    for t in types:
        t["id"] = _safe_id(t.get("id")) or _safe_id(t.get("name")) or "type"
    _step(job, "classify", "done",
          f"{len(types)} 类已建立，{len(candidates)} 个候选（只有 1 篇证据）")

    # ---------------- 3 每类提炼写法
    _step(job, "rules", "running")
    modules: dict[str, dict] = dict(prev.get("modules") or {})
    index: list[dict] = []
    by_id = {t["id"]: t for t in types}
    deep_by_key = {d["rec"].get("key"): d for d in deep}
    id_by_note = {str(r.get("id")): r.get("key") for r in contents}

    for i, t in enumerate(types):
        _step(job, "rules", "running", f"第 {i + 1}/{len(types)} 类：{t.get('name')}")
        members = []
        for pid in (t.get("postIds") or []):
            key = id_by_note.get(str(pid))
            if key and key in deep_by_key:
                members.append(deep_by_key[key])
        # 模型给的 postIds 指不到深读样本时，按目录里的同类顺序兜底
        if not members:
            members = deep[:2]
        bodies = _deep_sources(members[:3], body_limit=1800)
        catalog_rows = "\n".join(
            _row(j + 1, r, deep_keys) for j, r in enumerate(contents)
            if str(r.get("id")) in {str(p) for p in (t.get("postIds") or [])}
        ) or "（模型没有列出该类在目录里的其他条目）"
        old = modules.get(t["id"]) or {}
        r = _call(job, "profile_rules", {
            "typeName": t.get("name") or t["id"],
            "typeId": t["id"],
            "typeWhen": t.get("when") or "（未知）",
            "typeNotWhen": t.get("notWhen") or "（未知）",
            "nDeep": len(members),
            "nCatalog": len(t.get("postIds") or []),
            "catalogRows": catalog_rows,
            "deepBodies": bodies,
            "medianLike": facts.get("medianLike"),
            "today": store.now_iso()[:10],
            "existingRules": (old.get("rules") or "")[:2000] or "（没有，这次是新建）",
            "mode": "初始化" if mode == "init" else "补充学习",
        })
        if not r.get("ok") or not str(r.get("text") or "").strip():
            warnings.append(f"「{t.get('name')}」的规则没写成功：{(r.get('message') or '空回复')[:80]}")
            _step(job, "rules", "running", f"第 {i + 1}/{len(types)} 类失败，继续下一类")
            continue
        body = _strip_fence(str(r["text"]))
        modules[t["id"]] = {
            **old,
            "rules": body,
            "examples": old.get("examples") or "",
            "deepCount": len(members),
            "catalogCount": len(t.get("postIds") or []),
            "updatedAt": store.now_iso(),
        }
        index.append({
            "id": t["id"], "name": t.get("name") or t["id"], "status": "active",
            "when": t.get("when") or "", "notWhen": t.get("notWhen") or "",
            "rulesPath": f"rules/{t['id']}.md",
            "examplesPath": f"references/{t['id']}/examples.md",
            "deepCount": len(members), "catalogCount": len(t.get("postIds") or []),
            "evidence": t.get("postIds") or [],
            "updatedAt": store.now_iso(),
        })

    for c in candidates:
        cid = _safe_id(c.get("name")) or "candidate"
        index.append({
            "id": cid, "name": c.get("name") or cid, "status": "candidate",
            "when": c.get("reason") or "", "deepCount": c.get("deepCount") or 1,
            "catalogCount": c.get("catalogCount") or 1,
            "rulesPath": "", "updatedAt": store.now_iso(),
        })
    # 以前建过、这次没出现的类型：不删，标「近期未出现」（照原版）
    seen_ids = {x["id"] for x in index}
    for x in prev_index:
        if x.get("id") not in seen_ids and x.get("status") == "active":
            index.append({**x, "status": "recently-absent",
                          "note": "这轮样本里没再出现，保留原规则"})
            modules.setdefault(x["id"], (prev.get("modules") or {}).get(x["id"]) or {})

    ok_types = len([x for x in index if x.get("status") == "active"])
    _step(job, "rules", "done", f"{ok_types} 类规则已写")
    if not ok_types:
        job["status"] = "failed"
        job["error"] = "所有类型的规则都没写成功，看一下 AI 日志里的报错。"
        job["endedAt"] = store.now_iso()
        store.write("profile/job", job, note="profile job failed")
        return

    # 先落盘一次：后面几步失败也不至于白跑
    store.write("profile/style", {
        **prev, "index": index, "modules": modules,
        "common": prev.get("common") or {}, "records": prev.get("records") or [],
        "updatedAt": store.now_iso(),
    }, note="profile style modules")

    # ---------------- 4 空间记忆
    _step(job, "memory", "running")
    intro = _intro_text(contents)
    r = _call(job, "profile_memory", {
        "accountName": (_owner().get("account") or {}).get("nickName") or "（未知）",
        "intro": intro,
        "deepBodies": sources,
        "nDeep": len(deep),
        "today": store.now_iso()[:10],
        "existingMemory": _memory_text(),
    })
    if r.get("ok") and isinstance(r.get("json"), dict):
        items = _merge_memory(r["json"], prev=_memory_snapshot())
        store.write("profile/memory", {"schemaVersion": 1, "items": items,
                                       "updatedAt": store.now_iso()}, note="profile memory")
        pend = [m for m in items if m.get("status") == "pending"]
        _step(job, "memory", "done",
              f"记住 {len([m for m in items if m.get('status') == 'confirmed'])} 条"
              + (f"，{len(pend)} 条待你确认" if pend else ""))
    else:
        warnings.append(f"人物记忆没写成功：{(r.get('message') or '空回复')[:80]}")
        _step(job, "memory", "failed", "这一步失败，其余继续")

    # ---------------- 5 CreatorProfile
    _step(job, "creator", "running")
    r = _call(job, "profile_creator", {
        "accountName": (_owner().get("account") or {}).get("nickName") or "（未知）",
        "catalogFacts": facts_text,
        "catalogTable": table,
        "deepBodies": sources,
        "nCatalog": len(contents),
        "typeList": "\n".join(f"- {x['name']}（{x['id']}）样本 {x.get('deepCount')} 篇"
                              for x in index if x.get("status") == "active") or "（未分出类型）",
        "today": store.now_iso()[:10],
        "existing": (_creator().get("markdown") or "")[:3000] or "（第一次写）",
    })
    if r.get("ok") and isinstance(r.get("json"), dict):
        cre = r["json"]
        fields = cre.get("fields") or {}
        store.write("profile/creator", {
            "markdown": str(cre.get("markdown") or "").strip(),
            "fields": {
                "positioning": str(fields.get("positioning") or ""),
                "audience": str(fields.get("audience") or ""),
                "pillars": [str(x) for x in (fields.get("pillars") or [])],
                "cadence": str(fields.get("cadence") or ""),
                "goal": str(fields.get("goal") or ""),
                # 表现规律里的数字一律由代码算（模型会算错，这里不信它）
                "performance": _performance_lines(facts),
            },
            "unknown": [str(x) for x in (cre.get("unknown") or [])],
            "modelNotes": [str(x) for x in (fields.get("performance") or [])],
            "updatedAt": store.now_iso(),
        }, note="profile creator")
        _step(job, "creator", "done", "运营规划已写入")
    else:
        warnings.append(f"运营规划没写成功：{(r.get('message') or '空回复')[:80]}")
        _step(job, "creator", "failed", "这一步失败，其余继续")

    # ---------------- 6 试写验收
    _step(job, "selfcheck", "running")
    direction = ((_creator().get("fields") or {}).get("positioning")
                 or facts.get("topTags") and "、".join(t for t, _ in (facts.get("topTags") or [])[:3])
                 or "账号原本的内容方向")
    r = _call(job, "profile_selfcheck", {
        "typeList": "\n".join(
            f"- {x['name']}（{x['id']}）：{(modules.get(x['id']) or {}).get('rules', '')[:1200]}"
            for x in index if x.get("status") == "active"),
        "direction": str(direction)[:200],
        "today": store.now_iso()[:10],
    })
    samples = []
    if r.get("ok") and isinstance(r.get("json"), dict):
        samples = [s for s in (r["json"].get("samples") or []) if isinstance(s, dict)]
        _step(job, "selfcheck", "done", f"{len(samples)} 句试写，等你看哪句不像")
    else:
        warnings.append(f"试写没成功：{(r.get('message') or '空回复')[:80]}")
        _step(job, "selfcheck", "failed", "这一步失败，其余保持不变")

    # ---------------- 收尾：学习记录 + 状态
    version = int((_owner().get("version") or 0)) + (1 if mode == "init" else 0)
    if mode != "init":
        version = max(1, version)
    today = store.now_iso()[:10]
    record = (f"v{version} {today}：{'初始化' if mode == 'init' else '补充学习'}，"
              f"目录 {len(contents)} 篇（{facts.get('firstAt') or '?'} 至 {facts.get('lastAt') or '?'}），"
              f"深读 {len(deep)} 篇；建立 "
              + "、".join(x["name"] for x in index if x.get("status") == "active")
              + ("；候选 " + "、".join(x["name"] for x in index if x.get("status") != "active")
                 if any(x.get("status") != "active" for x in index) else "")
              + "。")

    st = _style()
    records = list(st.get("records") or [])
    records.append(record)
    common = st.get("common") or {}
    if not common:
        common = _common_placeholders(index, modules)
    store.write("profile/style", {**st, "index": index, "modules": modules,
                                  "common": common, "records": records,
                                  "updatedAt": store.now_iso()}, note="profile style done")

    owner = _owner()
    store.write("profile/owner", {
        **owner,
        "state": "ready" if not warnings else "partial",
        "version": version,
        "builtAt": owner.get("builtAt") or store.now_iso(),
        "lastLearnAt": store.now_iso(),
        "counts": {"catalog": len(contents), "deep": len(deep)},
        "catalogFacts": facts,
        "samples": samples,
        "warnings": warnings,
        "pending": [m for m in (_memory().get("items") or []) if m.get("status") == "pending"],
    }, note="profile owner done")

    job["status"] = "partial" if warnings else "done"
    job["warnings"] = warnings
    job["endedAt"] = store.now_iso()
    store.write("profile/job", job, note="profile job end")


# ================================================================== 小工具

def _safe_id(s) -> str:
    raw = str(s or "").strip().lower()
    raw = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return raw[:40]


def _strip_fence(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:markdown|md)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _guess_nick(contents: list[dict]) -> str:
    for r in contents:
        n = ((r.get("author") or {}).get("nicknameMasked") or "").strip()
        if n:
            return n
    return ""


def _intro_text(contents: list[dict]) -> str:
    """简介：小红书 creator 模式的原始数据里没有主页简介，只有正文。据实说明。"""
    return ("（同步回来的资料里没有主页简介这一项，只能从正文和标题看。"
            "没有证据的身份信息不要写进记忆。）")


def _memory_text() -> str:
    items = [m for m in (_memory().get("items") or []) if not m.get("dropped")]
    if not items:
        return "（还没有记忆，这是第一次）"
    return "\n".join(f"- [{m.get('slot')}] {m.get('content')}" for m in items[:30])


def _memory_snapshot() -> list[dict]:
    return [m for m in (_memory().get("items") or []) if not m.get("dropped")]


def _merge_memory(payload: dict, prev: list[dict]) -> list[dict]:
    """合并规则：同 slot 同内容不重复；推断标 pending；已有 confirmed 不被样本推翻。"""
    incoming = [c for c in (payload.get("candidates") or []) if isinstance(c, dict)]
    pending = [p for p in (payload.get("pending") or []) if isinstance(p, dict)]
    out = list(prev)
    by_slot: dict[str, list[dict]] = {}
    for m in out:
        by_slot.setdefault(str(m.get("slot") or ""), []).append(m)

    for c in incoming:
        slot = str(c.get("slot") or "").strip()
        if slot not in MEMORY_SLOTS:
            continue
        content = str(c.get("content") or "").strip()[:600]
        if not content:
            continue
        same = [m for m in by_slot.get(slot, []) if m.get("content") == content]
        if same:
            continue
        item = {
            "id": store.new_id("mem"),
            "slot": slot, "slotName": SLOT_NAMES.get(slot, slot),
            "content": content,
            "source": "stated" if c.get("type") == "stated" else "induced",
            "sourceName": "本人明说" if c.get("type") == "stated" else "多篇归纳",
            "evidence": [str(x) for x in (c.get("evidence") or [])],
            "evidenceCount": int(c.get("evidenceCount") or len(c.get("evidence") or [])),
            "confidence": str(c.get("confidence") or "medium"),
            "status": "confirmed",
            "updatedAt": store.now_iso(),
        }
        out.append(item)
        by_slot.setdefault(slot, []).append(item)

    for p in pending:
        content = str(p.get("content") or "").strip()[:600]
        if not content:
            continue
        if any(m.get("content") == content for m in out):
            continue
        out.append({
            "id": store.new_id("mem"),
            "slot": str(p.get("slot") or "identity"),
            "slotName": SLOT_NAMES.get(str(p.get("slot") or "identity"), "待确认"),
            "content": content, "source": "guess", "sourceName": "推断（待你确认）",
            "evidence": [str(x) for x in (p.get("evidence") or [])],
            "evidenceCount": int(p.get("evidenceCount") or 0),
            "confidence": "low", "status": "pending",
            "updatedAt": store.now_iso(),
        })
    return out


def _performance_lines(f: dict) -> list[str]:
    """表现规律：全部由目录统计直接算，不采用模型自己算的数字。

    原版要求「表现规律」来自 catalogFacts；模型复述时常把中位和单篇混起来，
    所以数字一律在这里生成，模型写的只放 modelNotes 供参考。
    """
    out: list[str] = []
    if not f or not f.get("total"):
        return out
    by_form = f.get("byForm") or {}
    for form, v in by_form.items():
        if (v or {}).get("n"):
            out.append(f"{form}：赞中位 {v.get('like')}、藏中位 {v.get('collect')}"
                       f"（{v['n']} 篇）")
    out.append(f"全目录赞中位 {f.get('medianLike')}、藏中位 {f.get('medianCollect')}、"
               f"评中位 {f.get('medianComment')}（{f['total']} 篇）")
    if f.get("gapMedianDays") is not None:
        out.append(f"相邻发帖间隔中位 {f.get('gapMedianDays')} 天，"
                   f"{f.get('firstAt')} 至 {f.get('lastAt')} 共 {f['total']} 篇"
                   f"（约每周 {f.get('perWeek')} 篇）")
    tl = f.get("titleLen") or {}
    if tl.get("median") is not None:
        out.append(f"标题中位 {tl.get('median')} 字（{tl.get('min')}–{tl.get('max')} 字）；"
                   f"带数字 {f.get('titleWithNumber')}、带问号 {f.get('titleWithQuestion')}、"
                   f"带感叹号 {f.get('titleWithExclaim')}（比例，样本 {f['total']}）")
    tags = f.get("topTags") or []
    if tags:
        out.append("高频标签：" + "、".join(f"{t}({c})" for t, c in tags[:5]))
    return out


def _common_placeholders(index: list[dict], modules: dict) -> dict:
    """根文件第二部分：跨类型共通章节。没有证据的维度写「未知」，不编。"""
    return {
        "titleOpening": "（跨类型共通的标题与开头习惯：等更多样本后再归纳，类型差异见各模块）",
        "structure": "（跨类型共通的结构：见各类型模块）",
        "rhythm": "（跨类型的句段节奏：见各类型模块）",
        "visual": "未知（尚未逐篇看图，配图习惯没有证据）",
        "voice": "（跨类型的语言气质：见各类型模块）",
        "conversion": "（互动与转化：见各类型模块）",
        "boundaries": "不可复制边界：范例只用来学结构和语气，写新内容时用当前事实和素材，"
                      "不搬旧帖里的数字、经历和效果。",
        "selfCheck": "发布前自检：事实能不能指回材料；标题和正文是否用上了对应类型的规则；"
                     "结尾没有账号风格要求的 CTA 就删掉。",
    }


# ================================================================== 纠正

def correct_sample(type_id: str, sample_index: int, note: str = "") -> dict:
    """用户指出「哪句不像你」：记进该类型模块，作为下次学习的最高优先级输入。"""
    st = _style()
    modules = st.get("modules") or {}
    mod = modules.get(type_id)
    if not mod:
        return {"ok": False, "message": "没有这个类型"}
    samples = _owner().get("samples") or []
    hit = None
    for s in samples:
        if str(s.get("typeId")) == str(type_id) and int(s.get("index", -1)) == int(sample_index):
            hit = s
    fix = {
        "at": store.now_iso(),
        "sample": (hit or {}).get("opening") or (hit or {}).get("title") or "",
        "note": (note or "").strip() or "用户指出这不像自己（未写原因）",
    }
    fixes = list(mod.get("fixes") or [])
    fixes.append(fix)
    modules[type_id] = {**mod, "fixes": fixes}
    store.write("profile/style", {**st, "modules": modules}, note="profile correction")
    return {"ok": True, "fix": fix, "count": len(fixes)}


def update_creator(markdown: str = "", fields: dict | None = None) -> dict:
    cur = _creator()
    out = {**cur}
    if markdown:
        out["markdown"] = markdown
    if isinstance(fields, dict):
        out["fields"] = {**(cur.get("fields") or {}), **fields}
    out["updatedAt"] = store.now_iso()
    store.write("profile/creator", out, note="profile creator edit")
    return {"ok": True, "creator": out}


def update_memory(item_id: str, action: str = "confirm", content: str = "") -> dict:
    items = list(_memory().get("items") or [])
    hit = None
    for m in items:
        if m.get("id") == item_id:
            hit = m
            break
    if not hit:
        return {"ok": False, "message": "没有这条记忆"}
    if action == "confirm":
        hit["status"] = "confirmed"
        hit["source"] = "stated"
        hit["sourceName"] = "本人确认"
    elif action == "drop":
        hit["dropped"] = True
    elif action == "edit" and content.strip():
        hit["content"] = content.strip()[:600]
        hit["status"] = "confirmed"
        hit["source"] = "stated"
        hit["sourceName"] = "本人纠正"
    hit["updatedAt"] = store.now_iso()
    store.write("profile/memory", {"schemaVersion": 1, "items": items}, note="profile memory edit")
    return {"ok": True, "item": hit}


def forget_own(platform: str = "xhs") -> dict:
    """清空自己账号的库（不碰赛道样本）。"""
    store.write(f"own/{platform}", {"schemaVersion": 1, "items": {},
                                    "clearedAt": store.now_iso()}, note="own cleared")
    return {"ok": True}
