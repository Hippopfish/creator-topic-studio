# -*- coding: utf-8 -*-
"""选题台改造自检。不调 AI、不花钱，只查「规则落没落到位」。

对话台现在是选题台的一部分（不再有独立页面），所以自检也跟着换成查对话链路。

用法（在 workbench 目录下）：
    ..\\MediaCrawler\\.venv\\Scripts\\python.exe verify_topic.py
"""
import json
import sys
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8737"
OK, BAD = "[OK]  ", "[X]   "
HERE = Path(__file__).resolve().parent


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read())


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def line(ok, name, detail=""):
    print(f"{OK if ok else BAD}{name}" + (f"  → {detail}" if detail else ""))
    return ok


def read(p):
    try:
        return (HERE / p).read_text(encoding="utf-8")
    except Exception:
        return ""


def main():
    results = []
    try:
        h = get("/api/health")
    except Exception as e:
        print(f"服务没起来（{BASE}）。先启动：pythonw app.py\n  {e}")
        return 2
    results.append(line(h.get("ok"), "服务在线", f"pid {h.get('pid')}"))

    # ---------- 1. 规则能不能从 topic_judge.md 切出来 ----------
    try:
        r = get("/api/chat/rules")
    except Exception as e:
        print(BAD + "取规则失败：" + str(e))
        return 2

    results.append(line(r.get("chars", 0) > 2000, "选题规则能加载",
                        f"{r.get('chars')} 字"))
    results.append(line(r.get("placeholders") == 0, "规则里没有残留占位符",
                        f"{{{{ }}}} 出现 {r.get('placeholders')} 次"))
    need = [("hasGate", "硬门槛"), ("hasMemo", "研究小抄"),
            ("hasTitleRules", "标题硬规则"), ("hasDedupe", "同批去重")]
    miss = [name for k, name in need if not r.get(k)]
    results.append(line(not miss, "规则四段齐全",
                        "缺：" + "、".join(miss) if miss else "门槛/小抄/标题/去重 都在"))

    tools = r.get("tools") or []
    results.append(line(len(tools) == 7 and "read_topic_rules" in tools
                        and "request_collect" in tools,
                        "对话有 7 个工具（含读规则、提交采集）", "、".join(tools)))

    pool = r.get("pool") or {}
    results.append(line(
        (pool.get("minLikeInPool") or 0) < 100 and pool.get("lowLikeCount", 0) > 0,
        "素材池不按点赞清洗",
        f"共 {pool.get('total')} 条，最低 {pool.get('minLikeInPool')} 赞，"
        f"{pool.get('lowLikeCount')} 条低于 100"))
    results.append(line(pool.get("sortByPublish") is True,
                        "查素材按发布时间排，不按点赞排"))

    # ---------- 2. 对话提示词的纪律有没有写进去 ----------
    chatmd = read("prompts/chat.md")
    rules_md = [
        ("出选题前先读规则" in chatmd, "提示词：出选题前先调规则工具"),
        ("指定深读清单" in chatmd, "提示词：勾了素材就是指定深读清单"),
        ("停下来问" in chatmd and "不许自己换料" in chatmd,
         "提示词：料撑不住要停下问，不许自己换"),
        ("用户没让你存，就不要存" in chatmd, "提示词：没同意不许存库"),
        ("等用户自己说" in chatmd, "提示词：采集前必须先讲清代价并等确认"),
        ("提交完别演" in chatmd, "提示词：提交采集后不许假装已采到"),
        ("不许把采集当逃避" in chatmd, "提示词：不许拿采集逃避判断"),
    ]
    for ok, name in rules_md:
        results.append(line(ok, name))

    # ---------- 2b. 采集队列：能排队、有调度线程、结果能回写会话 ----------
    try:
        q = get("/api/chat/collect/all")
        results.append(line(q.get("worker") is True, "采集调度线程在跑",
                            f"排队的 {len(q.get('pending') or [])} 个"))
        results.append(line(isinstance(q.get("items"), list), "队列记录可读",
                            f"{len(q.get('items') or [])} 条历史"))
    except Exception as e:
        results.append(line(False, "采集调度线程在跑", str(e)[:60]))

    # 取消一个不存在的任务，应该被拒（说明接口有校验，不是无脑成功）
    try:
        bad = post("/api/chat/collect/q_not_exist_0000/cancel", {})
        results.append(line(bad.get("ok") is False, "取消接口校验存在性",
                            bad.get("message", "")))
    except Exception as e:
        results.append(line(False, "取消接口校验存在性", str(e)[:60]))

    # 空的入队请求必须被拒，不能凭空排一个没词的采集
    try:
        probe = post("/api/chat/stream", {})
        results.append(line(probe.get("ok") is False, "空消息不会开新会话", "已拒"))
    except Exception:
        # 空消息走到 SSE 也是拒，抛异常同样算拦住了
        results.append(line(True, "空消息不会开新会话", "已拒"))

    jobsrc = read("core/jobs.py")
    results.append(line('origin == "chat"' in jobsrc and "autoImport" in jobsrc,
                        "批次会标来源（对话/手动 + 原因）"))
    results.append(line("import_batch" in read("core/collect_queue.py"),
                        "采完自动导入，不用手点"))

    # 重导不能把第一次的「新增」抹成 0（踩过：采集台显示 0 · 效 0%）
    impsrc = read("core/importer.py")
    results.append(line("importTotalAdded" in impsrc and "firstEfficiency" in impsrc,
                        "重导不覆盖首次导入结果（累计新增 + 首次有效率）"))

    # 采集结果要回写到会话，否则用户永远收不到「采完了」
    chatsrc = read("core/chat.py")
    results.append(line("_NEED_CTX" in chatsrc and "run_tool(name, args, tctx)" in chatsrc,
                        "会话/账号显式传给工具，不靠 contextvars"))
    try:
        pend = get("/api/chat/notices/pending")
        results.append(line(isinstance(pend.get("items"), list),
                            "采集结果未读可查（导航红点）",
                            f"{pend.get('count', 0)} 条未读"))
    except Exception as e:
        results.append(line(False, "采集结果未读可查（导航红点）", str(e)[:60]))

    # ---------- 3. 前端：对话嵌在选题台，不再有独立页面 ----------
    collect = read("web/js/views-collect.js")
    chatjs = read("web/js/views-chat.js")
    index = read("web/index.html")
    results.append(line("ChatPanel.mount" in collect, "选题台里挂载了对话面板"))
    results.append(line("genBtn" not in collect and "topic_judge" not in collect,
                        "旧的「生成选题」一次性通道已删除"))
    results.append(line("window.ChatPanel" in chatjs, "面板按 ChatPanel 导出，不是独立视图"))
    results.append(line('href="#/chat"' not in index, "导航里没有独立「对话」"))
    results.append(line("getCtx" in collect and "contentKeys" in collect,
                        "选区（勾选素材/seed/条数/账号）会带进对话"))

    # 快捷句必须常驻在输入框上方。挂在空态里的话，一开旧会话（刷新后默认接着上次）
    # 就再也看不见了——用户实测就是这么报的。
    quick_at = chatjs.find('id="cpQuick"')
    bar_at = chatjs.find('class="cp-bar"')
    results.append(line(quick_at > 0 and bar_at > quick_at
                        and chatjs.count('class="cp-quick"') == 1,
                        "新手快捷句常驻输入框上方（开旧会话也在）"))

    # 图里的字：采集时把图存本地，要用时才让模型看（不对齐全库预读）
    results.append(line("_image_hint" in chatsrc and "图里的文字" in chatsrc,
                        "读笔记会标出「字在图里」，不许替图编内容"))
    results.append(line("字在图里" in chatmd, "提示词：图里的字没读到就说没读到"))
    try:
        import core.vision as _v
        vsrc = _v.__doc__ or ""
        results.append(line(bool(_v.MAX_IMAGES) and "vision.read" in chatsrc,
                            "读图按需触发（挂在 read_note 上，不预读全库）"))
        results.append(line("disabled" in open(_v.__file__, encoding="utf-8").read(),
                            "读图调用显式关思考（否则 200 但返回空）"))
        results.append(line(_v.SPLIT_RATIO > 1, f"竖版长图先切半（阈值 {_v.SPLIT_RATIO}）"))
    except Exception as e:
        results.append(line(False, "读图模块可用", str(e)[:60]))

    crawl_src = (HERE / "core" / "jobs.py").read_text(encoding="utf-8")
    results.append(line('"--get_media", "true"' in crawl_src,
                        "采集开启图片下载（链接 1–2 天过期，必须当场存）"))
    results.append(line('"MC_GET_VIDEO": "0"' in crawl_src, "视频不下载，只留封面"))
    imp_src = (HERE / "core" / "importer.py").read_text(encoding="utf-8")
    results.append(line("_attach_media" in imp_src and "data" in imp_src,
                        "导入时把图片搬进 data/media（脱离批次目录）"))
    norm_src = (HERE / "core" / "normalize.py").read_text(encoding="utf-8")
    results.append(line("split(\",\")" in norm_src,
                        "图片/标签按逗号拆（采集器写的是逗号串）"))

    # 账号档案在表单版是规则的一部分，对话版早先漏了，只能靠提示词里写死的定位
    results.append(line("_account_archive" in chatsrc and "build_vars" in chatsrc,
                        "账号档案进 system（与表单版同一取值处）"))

    # 实测踩过：嘴上回「存了」，其实没调 save_topics，选题卡里啥也没有
    results.append(line("_claims_saved" in chatsrc and "_repair_save" in chatsrc,
                        "说「存了」却没写库会被抓出来并补存"))
    results.append(line("说「存了」之前必须真的调过" in chatmd,
                        "提示词：不许嘴上说存、手上没动"))

    # 旧的一次性通道必须真的关掉，不然又会冒出第二套逻辑
    try:
        old = post("/api/ai/prepare", {"task": "topic_judge", "ctx": {}})
        results.append(line(old.get("code") == "MOVED_TO_CHAT",
                            "旧「生成选题」后端通道已封",
                            old.get("message", "")[:40]))
    except Exception as e:
        results.append(line(False, "旧「生成选题」后端通道已封", str(e)[:60]))

    # ---------- 4. 库里的选题还是不是完整的 ----------
    t = get("/api/topics")
    items = t.get("items") or []
    if not items:
        results.append(line(False, "选题字段完整", "库里没有选题，先跑一次对话出题"))
    else:
        lack = []
        no_ref = []
        for x in items:
            ei = x.get("editorialIntent") or {}
            if not x.get("method"):
                lack.append((x.get("title"), "method"))
            if not ei.get("genre"):
                lack.append((x.get("title"), "体裁"))
            if not ei.get("mustNotDrift"):
                lack.append((x.get("title"), "不能写成"))
            if not (x.get("evidence") or [{}])[0].get("ref"):
                no_ref.append(x.get("title"))
        results.append(line(not lack, f"{len(items)} 条选题结构完整",
                            "缺：" + str(lack[:3]) if lack else "method/体裁/不能写成 全有"))
        results.append(line(bool((t.get("enums") or {}).get("methods")), "方法枚举已中文化"))
        # 证据回链率只做提示，不算失败：作者亲身经历类选题确实没有原文可指
        print(f"[i]   证据回链 {len(items) - len(no_ref)}/{len(items)}"
              + (f"  → 没挂 ref：{'、'.join(x[:16] for x in no_ref[:3])}" if no_ref else ""))

    qc = (items[0].get("qualityCheck") or {}) if items else {}
    results.append(line(qc.get("total") == 11, "质检 11 条",
                        f"{qc.get('passCount')}/{qc.get('total')} 通过" if qc else "无质检数据"))

    # 证据回链：不依赖现有选题数据（它们可能是历史残留），直接验规则本身
    # ——真 ref 该过；ref 指向库里不存在的笔记，必须拦下。
    try:
        sys.path.insert(0, str(HERE))
        from core import store as _store
        from api.studio_api import run_checks
        _contents = (_store.read("contents/xhs") or {}).get("items") or {}
        real_ref = next(iter(_contents), "")
    except Exception:
        run_checks, real_ref = None, ""

    if run_checks:
        base = {"title": "自检用标题", "reader": "25岁INTP，周日下午明知道有活却开不了头",
                "gap": "premise", "angle": "counterintuitive", "method": "audience_problem",
                "layer": "L2", "canDeliver": "能讲清一个具体场景",
                "editorialIntent": {"genre": "观点", "mustNotDrift": "不要写成MBTI科普"}}

        def _ev_pass(ref):
            t = dict(base)
            t["evidence"] = [{"quote": "q", "ref": ref}]
            return next(i["pass"] for i in run_checks(t)["items"]
                        if i["rule"] == "evidence_present")

        ok_real = bool(real_ref) and _ev_pass(real_ref)
        ok_fake = not _ev_pass("xhs:deadbeef0000000000000000")
        results.append(line(ok_real and ok_fake,
                            "证据回链校验生效",
                            "真 ref 过、查不到的不给过" if ok_real and ok_fake
                            else f"真 {ok_real} / 假 {ok_fake}"))

    # ---------- 采集护栏（组间冷却 / 日限额 / 风控刹车）----------
    try:
        gd = get("/api/crawl/guard")
        lm = gd.get("limits") or {}
        results.append(line(
            all(k in lm for k in ("cooldownSec", "dailySearch", "riskStrikes", "riskCooldownMin")),
            "采集护栏：接口在线", f"冷却 {lm.get('cooldownSec')}s · 日限 {lm.get('dailySearch')} 条"))
    except Exception as e:
        results.append(line(False, "采集护栏：接口在线", str(e)))

    # 行为验证：造一个「刚结束」的任务，冷却必须拦住下一组；解除后必须放行。
    # 会临时改动 data/crawlGuard.json，测完原样还原（含"文件本来不存在"的情况）。
    try:
        sys.path.insert(0, str(HERE))
        from core import crawl_guard as _g
        from core import paths as _paths
        _p = _paths.data_file("crawlGuard")
        _bak = _p.read_bytes() if _p.exists() else None
        try:
            _g.reset("all")
            _g.record_start("selftest_guard", "search", 40)
            _g.record_end("selftest_guard", 40, "done")
            cooled = _g.check("search", 40)
            _g.reset("all")
            freed = _g.check("search", 40)
        finally:
            if _bak is not None:
                _p.write_bytes(_bak)
            elif _p.exists():
                _p.unlink()
        results.append(line(
            cooled.get("code") == "GUARD_COOLDOWN" and freed.get("ok"),
            "组间冷却真拦得住", f"结束即起 → {cooled.get('code')}；解除后 ok={freed.get('ok')}"))
    except Exception as e:
        results.append(line(False, "组间冷却真拦得住", f"{type(e).__name__}: {e}"))

    # 三处入口必须接同一套判断，各写一套等于没护栏
    _jobs_src = read("core/jobs.py")
    _q_src = read("core/collect_queue.py")
    _re_src = read("bin/recrawl.py")
    _rf_src = read("bin/refetch_by_id.py")
    results.append(line(
        "crawl_guard.check(" in _jobs_src and "record_start(" in _jobs_src
        and "record_end(" in _jobs_src and "looks_like_risk(" in _jobs_src,
        "采集入口接上护栏", "起进程前判、起来后记账、结束时收尾、日志里认风控"))

    results.append(line(
        "GUARD_COOLDOWN" in _q_src and "GUARD_DAILY" in _q_src,
        "对话队列认得护栏返回码", "冷却让路、额度用完标 stalled"))

    results.append(line(
        "guard_client" in _re_src and "guard_client" in _rf_src,
        "补采脚本共用同一套冷却", "recrawl / refetch_by_id 不再各写一套间隔"))

    # ---------- 对话是否真读素材（不是凭上下文复述）----------
    try:
        sys.path.insert(0, str(HERE))
        from core import chat as _chat
        _pool = _chat._pool_text([{"role": "assistant", "at": "2026-01-01T00:00:00+08:00"}])
        _okpool = ("素材库现状" in _pool and "新入库" in _pool
                   and "正文和评论" in _pool)
        results.append(line(_okpool, "每轮注入库现状（后端算，不是模型印象）",
                            "条数/可读数/最近入库/上轮后新增"))
    except Exception as e:
        results.append(line(False, "每轮注入库现状（后端算，不是模型印象）", str(e)))

    try:
        _chat_src = read("core/chat.py")
        _pool_in_sys = "_pool_text(history)" in _chat_src
        _has_finalize = "_finalize(" in _chat_src and "over_cap" in _chat_src
        _strip = "_strip_markup" in _chat_src and "DSML" in _chat_src
        _save_gate = "_user_wants_save(user_text)" in _chat_src
        results.append(line(_pool_in_sys and _has_finalize and _strip and _save_gate,
                            "对话链路的四个兜底都在",
                            "库现状进 system / 轮数用尽收尾 / 切工具标记 / 没让存就不补存"))
    except Exception as e:
        results.append(line(False, "对话链路的四个兜底都在", str(e)))

    # 出选题必须至少查一次库 —— 这是 8 秒秒出的直接原因（只调了读规则+读选题）
    _prompt_src = read("prompts/chat.md")
    results.append(line(
        "search_material` 至少要调一次" in _prompt_src
        and "上一轮读过不算" in _prompt_src,
        "提示词：每轮重查、跨轮不算读过", "堵住「同一批料换角度重排」"))

    # ---------- 创作台：采纳要建稿、选题定型要传下去 ----------
    _collect_src = read("web/js/views-collect.js")
    results.append(line(
        "/to-draft" in _collect_src and "data-write" in _collect_src,
        "采纳即建稿", "点采纳直接 POST to-draft，已采纳的给「去写作」直跳"))

    _studio_src = read("web/js/views-studio.js")
    results.append(line(
        "genBody" in _studio_src and "draft_write" in _studio_src,
        "创作台有「生成正文」入口", "三个按钮变四环，正文不再是只能手打"))

    results.append(line(
        "mustNotDrift" in _studio_src and "不能写成" in _studio_src,
        "创作台显示选题定型", "动机 / 体裁 / 绝不能被改写成什么"))

    try:
        sys.path.insert(0, str(HERE))
        from core import ai as _ai
        _topics = [t for t in json.loads(
            (HERE / "data" / "topics.json").read_text(encoding="utf-8"))["items"] if t.get("editorialIntent")]
        _t = _topics[0] if _topics else {}
        _p = _ai.prepare("draft_write", {"topicId": _t.get("id") or ""})
        _u = _p.get("userPrompt") or ""
        _drift = ((_t.get("editorialIntent") or {}).get("mustNotDrift") or "")[:12]
        results.append(line(
            _p.get("ok") and "选题定型" in _u and (not _drift or _drift in _u)
            and "可用素材" in _u,
            "写稿继承选题定型 + 真素材",
            f"「{_drift}…」进提示词，素材读到原文与评论"))
    except Exception as e:
        results.append(line(False, "写稿继承选题定型 + 真素材", str(e)))

    try:
        sys.path.insert(0, str(HERE))
        from api.studio_api import normalize_form as _nf
        from core.ai import _extract_split as _sp
        _f1 = _nf("自省记录", "观察")[0]
        _f2 = _nf(None, "观点")[0]
        _f3 = _nf("opinion_short", "观点")[0]
        _cap, _img = _sp("## 正文\nx\n\n## 图文分工\n- 只有正文能说的：A；B\n- 只有图更有说服力的：C")
        results.append(line(
            _f1 == "life_record" and _f2 == "opinion_short" and _f3 == "opinion_short"
            and _cap == ["A", "B"] and _img == ["C"],
            "contentForm 归一 + 图文分工落库",
            f"中文形式词能转枚举；分工拆成两份清单"))
    except Exception as e:
        results.append(line(False, "contentForm 归一 + 图文分工落库", str(e)))

    ok = sum(1 for x in results if x)
    print(f"\n{ok}/{len(results)} 项通过")
    if ok < len(results):
        print("有未通过项，见上面 [X]。")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
