/* 选题搭子：嵌在选题台右栏，不是独立页面。
   外部调用 window.ChatPanel.mount(host, opts)

   opts = {
     getCtx():  返回本轮选区 {accountId, contentKeys, seed, count, candidateRange, seedBrief}
     openNote:  点「看原文」时怎么打开（选题台传自己的抽屉，避免两套）
     onSaved:   存完选题后回调（选题台用来刷选题卡）
     placeholder: 输入框提示
   }
*/
(function () {
  "use strict";
  const { api, toast, esc, fmtNum } = window.WB;

  const TOOL_NAMES = {
    read_topic_rules: "读选题规则",
    search_material: "查素材池",
    read_note: "读笔记",
    read_topics: "读选题",
    save_topics: "存选题",
    search_insights: "查经验",
    request_collect: "提交采集",
  };

  // 切走再回来还停在上次那个会话；但换页面不换数据，所以放模块级
  let lastSid = "";

  // ---------------------------------------------------------------- 极简 markdown
  // 只处理对话里真会出现的几种：粗体、行内码、有序/无序列表、空行。
  function md(src) {
    const lines = String(src || "").split("\n");
    const out = [];
    let list = null;
    const flush = () => { if (list) { out.push(`</${list}>`); list = null; } };

    const inline = (s) =>
      esc(s)
        .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
        .replace(/`([^`]+)`/g, "<code>$1</code>");

    for (const raw of lines) {
      const line = raw.replace(/\s+$/, "");
      const mUl = line.match(/^\s*[-*]\s+(.*)$/);
      const mOl = line.match(/^\s*(\d+)[.、]\s+(.*)$/);
      if (mUl) {
        if (list !== "ul") { flush(); out.push("<ul>"); list = "ul"; }
        out.push(`<li>${inline(mUl[1])}</li>`);
      } else if (mOl) {
        if (list !== "ol") { flush(); out.push("<ol>"); list = "ol"; }
        out.push(`<li>${inline(mOl[2])}</li>`);
      } else if (!line.trim()) {
        flush();
        out.push('<div class="gap"></div>');
      } else {
        flush();
        out.push(`<p>${inline(line)}</p>`);
      }
    }
    flush();
    return out.join("");
  }

  // ---------------------------------------------------------------- 兜底的原笔记抽屉
  // 选题台会传自己的 openNote；没传（或切到别处）时用这个。
  async function showNote(ref, fallback) {
    if (typeof fallback === "function") { fallback(ref); return; }
    const [plat, id, ...rest] = String(ref || "").split(":");
    const cid = rest.length ? rest.join(":") : id;
    if (!plat || !cid) return;
    let rec = null, cs = [];
    try {
      rec = await api.get(`/api/contents/${plat}/${cid}`);
    } catch (e) {
      toast("库里没找到这篇", "err");
      return;
    }
    try {
      const c = await api.get(`/api/contents/${plat}/${cid}/comments?limit=40`);
      cs = c.items || [];
    } catch (e) { cs = []; }

    const m = rec.metrics || {};
    const rows = [
      ["点赞", fmtNum(m.like)], ["收藏", fmtNum(m.collect)],
      ["评论", fmtNum(m.comment)], ["分享", fmtNum(m.share)],
    ].map(([k, v]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join("");

    window.WB.drawer(`原笔记 · ${String(rec.title || "").slice(0, 24)}`, `
      <div class="stat-row" style="margin-bottom:12px">${rows}</div>
      ${(rec.tags || []).length ? `<div class="hint" style="margin-bottom:8px">${esc((rec.tags || []).join(" "))}</div>` : ""}
      <div class="card" style="margin:0 0 12px">
        <h2>正文</h2>
        <div style="white-space:pre-wrap;line-height:1.75">${esc(rec.body || "（这篇没有采到正文，只有标题和标签）")}</div>
      </div>
      <div class="card" style="margin:0">
        <h2>评论 <span class="tag">${cs.length} 条${cs.length >= 40 ? "（只取前 40）" : ""}</span></h2>
        ${cs.length
          ? cs.map((c) => `<div style="padding:6px 0;border-bottom:1px solid var(--line-soft)">
              <div class="hint">赞 ${fmtNum(c.like)}${c.subCount ? ` · ${c.subCount} 条回复` : ""}</div>
              <div>${esc(c.content || "")}</div>
            </div>`).join("")
          : '<div class="hint">这篇没采到评论。想去补就回答复台跑一次该笔记的评论采集。</div>'}
      </div>
    `);
  }

  // ---------------------------------------------------------------- 消息渲染
  // 历史里的工具行只留最近 3 条，前面的收起来。不折叠的话，聊几轮工具行就把面板占满了。
  function toolLines(list) {
    const one = (t) =>
      `<div class="tool-line">· ${esc(t.brief || "")}`
      + (t.ref ? ` <a href="#" data-ref="${esc(t.ref)}">看原文</a>` : "")
      + `</div>`;
    if (list.length <= 4) return list.map(one).join("");
    const head = list.slice(0, -3);
    return `<details class="tool-more"><summary>前面 ${head.length} 步</summary>`
      + head.map(one).join("") + `</details>`
      + list.slice(-3).map(one).join("");
  }

  function msgHtml(m) {
    if (m.role === "user") {
      return `<div class="msg user"><div class="bubble">${esc(m.content || "")}</div></div>`;
    }
    const tools = toolLines(m.tools || []);
    return `<div class="msg ai">${tools}<div class="bubble">${md(m.content || "")}</div></div>`;
  }

  function nearBottom(el) {
    return el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  }

  // ---------------------------------------------------------------- 挂载
  async function mount(host, opts = {}) {
    if (!host) return null;
    const state = { sid: lastSid, busy: false, ctrl: null, poll: null, noticeCount: -1, live: 0 };

    host.innerHTML = `
      <div class="cp">
        <div class="cp-top">
          <select id="cpSess" title="历史对话"></select>
          <button class="sm" id="cpNew" title="开一个干净的对话">+ 新</button>
          <button class="sm" id="cpDel" title="删除当前对话">×</button>
        </div>
        <div class="cp-queue" id="cpQueue" style="display:none"></div>
        <div class="cp-msgs" id="cpMsgs"></div>
        <div class="cp-notices" id="cpNotices" style="display:none"></div>
        <div class="cp-quick" id="cpQuick">
          <button class="sm" data-q="帮我在库里挑 3 条这周能发的选题，先告诉我为什么是这三条">出一组选题</button>
          <button class="sm" data-q="库里现在哪些方向素材最多、哪些是空的？我想知道还能写什么">看库里有什么</button>
          <button class="sm" data-q="这周该写什么？只给我最推荐的一条，说清凭什么">这周写什么</button>
        </div>
        <div class="cp-bar">
          <textarea id="cpInput" rows="1"
            placeholder="${esc(opts.placeholder || "想问什么直接说，比如：这几条里哪个缺口最能做？")}"></textarea>
          <button class="primary sm" id="cpSend">发送</button>
        </div>
      </div>`;

    const msgsEl = host.querySelector("#cpMsgs");
    const inputEl = host.querySelector("#cpInput");
    const sendBtn = host.querySelector("#cpSend");
    const sessEl = host.querySelector("#cpSess");
    const queueEl = host.querySelector("#cpQueue");
    const noticeEl = host.querySelector("#cpNotices");
    const cpEl = host.querySelector(".cp");
    const quickEl = host.querySelector("#cpQuick");

    const scrollToEnd = (force) => {
      if (force || nearBottom(msgsEl)) msgsEl.scrollTop = msgsEl.scrollHeight;
    };

    const welcome = () => {
      msgsEl.innerHTML = `
        <div class="cp-empty">
          它会自己查库、读正文和评论，不瞎编。<br>
          <span class="hint">不知道说什么，就点输入框上方那三句，或者先勾左边素材再问「这几条里哪个缺口最能做」。</span>
        </div>`;
      paintQueue([]);
      paintNotices([]);
      if (state.poll) { clearTimeout(state.poll); state.poll = null; }
    };

    // 新手不用想措辞，点一句就发出去。
    // 挂在整块面板上，不是只挂消息区——快捷句常驻在输入框上方，不在消息流里。
    cpEl.addEventListener("click", (e) => {
      const b = e.target.closest("[data-q]");
      if (!b) return;
      e.preventDefault();
      if (state.busy) { toast("等这轮说完", ""); return; }
      send(b.dataset.q);
    });

    // ---- 采集队列：顶部状态条 + 底部结果通知
    // 采集是后台排队的，几十分钟才回来。这两块就是让用户「看得见它在动」，
    // 不然提交完一片安静，会以为没提交上。
    const ST_LABEL = { queued: "排队中", running: "采集中", stopping: "正在停" };

    function paintQueue(live) {
      state.live = live.length;
      cpEl.classList.toggle("has-bar", live.length > 0);
      if (!live.length) {
        queueEl.style.display = "none";
        queueEl.innerHTML = "";
        return;
      }
      queueEl.style.display = "";
      queueEl.innerHTML = live.map((r) => `<div class="q-row">
          <span class="q-dot ${esc(r.status)}"></span>
          <span class="q-text">${esc(ST_LABEL[r.status] || r.status)}：${esc((r.keywords || []).join("、"))}</span>
          <a href="#" class="q-x" data-cancel="${esc(r.id)}">取消</a>
        </div>`).join("");
    }

    function paintNotices(ns) {
      const prev = state.noticeCount;
      state.noticeCount = ns.length;
      cpEl.classList.toggle("has-notice", ns.length > 0);
      if (!ns.length) {
        noticeEl.style.display = "none";
        noticeEl.innerHTML = "";
        return;
      }
      noticeEl.style.display = "";
      // 只留最近 3 条，不然通知区会把消息区挤没
      noticeEl.innerHTML = ns.slice(-3).map((n) => {
        const cls = ["collect_failed", "collect_stalled"].includes(n.kind) ? "bad"
          : n.kind === "collect_blocked" ? "warn" : "ok";
        return `<div class="cp-notice ${cls}">${esc(n.text || "")}</div>`;
      }).join("");

      // 采集结果要进消息流，不能只挂在输入框上方那条小字里——
      // 进会话时（prev<0）也补上最近两条，否则用户重开对话会以为没发生过
      const fresh = prev < 0 ? ns.slice(-2) : ns.slice(Math.max(prev, 0));
      if (fresh.length) {
        fresh.forEach((n) => {
          msgsEl.insertAdjacentHTML("beforeend",
            `<div class="msg sys"><div class="bubble">${esc(n.text || "")}</div></div>`);
          // 实时到的才弹一下；翻旧账不弹
          if (prev >= 0) {
            toast(n.text || "采集结果回来了",
                  ["collect_failed", "collect_stalled"].includes(n.kind) ? "err" : "ok");
          }
        });
        scrollToEnd(true);
      }

      // 看过了就清掉导航上的红点
      if (state.sid) {
        api.post("/api/chat/notices/seen", { sessionId: state.sid }).catch(() => {});
      }
    }

    async function refreshQueue() {
      if (!state.sid) { paintQueue([]); paintNotices([]); return 0; }
      try {
        const r = await api.get(`/api/chat/collect?sessionId=${encodeURIComponent(state.sid)}`);
        const live = (r.items || []).filter(
          (x) => x.status === "queued" || x.status === "running" || x.status === "stopping");
        paintQueue(live);
        const ns = r.notices || [];
        if (ns.length !== state.noticeCount) paintNotices(ns);
        return live.length;
      } catch (e) {
        return state.live;
      }
    }

    function schedulePoll() {
      if (state.poll) { clearTimeout(state.poll); state.poll = null; }
      if (!state.live) return;
      state.poll = setTimeout(async () => {
        state.poll = null;
        state.live = await refreshQueue();
        schedulePoll();
      }, 8000);
    }

    queueEl.addEventListener("click", async (e) => {
      const a = e.target.closest("[data-cancel]");
      if (!a) return;
      e.preventDefault();
      a.textContent = "取消中…";
      try {
        await api.post(`/api/chat/collect/${a.dataset.cancel}/cancel`, {});
      } catch (err) {
        toast(err.message, "err");
      }
      state.live = await refreshQueue();
      schedulePoll();
    });

    // ---- 会话下拉
    async function loadList() {
      try {
        const r = await api.get("/api/chat/sessions");
        const rows = r.items || [];
        sessEl.innerHTML = `<option value="">新对话</option>`
          + rows.map((s) => `<option value="${esc(s.id)}">${esc((s.title || "新对话").slice(0, 18))}</option>`).join("");
        sessEl.value = state.sid || "";
      } catch (e) { /* 列表失败不影响对话 */ }
    }

    // ---- 打开会话
    async function openSession(sid) {
      if (state.busy) { toast("等这轮说完再切", ""); return; }
      if (!sid) {
        state.sid = ""; lastSid = ""; welcome(); loadList(); return;
      }
      const s = await api.get(`/api/chat/sessions/${sid}`);
      if (!s || s.ok === false) {
        // 会话被删了（或换机器了）：别卡在空壳上，退回新对话
        state.sid = ""; lastSid = "";
        welcome(); loadList();
        return;
      }
      state.sid = s.id;
      lastSid = s.id;
      const items = (s.messages || []).filter(
        (m) => (m.role === "user" || m.role === "assistant") && String(m.content || "").trim());
      msgsEl.innerHTML = items.length ? items.map(msgHtml).join("") : "";
      if (!items.length) welcome();
      scrollToEnd(true);
      loadList();
      // 进会话先拉一次队列：离开期间可能采完了
      state.noticeCount = -1;
      state.live = await refreshQueue();
      schedulePoll();
    }

    // ---- 流式发送
    async function send(text) {
      if (state.busy || !text.trim()) return;
      state.busy = true;
      if (quickEl) quickEl.style.display = "none";
      sendBtn.textContent = "停止";
      sendBtn.classList.add("danger");

      msgsEl.insertAdjacentHTML("beforeend",
        `<div class="msg user"><div class="bubble">${esc(text)}</div></div>`);

      const aiEl = document.createElement("div");
      aiEl.className = "msg ai";
      aiEl.innerHTML = `<div class="tools"></div>
        <div class="bubble"><span class="hint">正在想…</span></div>`;
      msgsEl.appendChild(aiEl);
      const toolsEl = aiEl.querySelector(".tools");
      const bubble = aiEl.querySelector(".bubble");
      const toolRows = [];   // {el, ref, name, done}，按出现顺序
      let acc = "";
      let gotFirst = false;
      let saved = false;
      let submitted = false;

      scrollToEnd(true);

      const paint = () => {
        bubble.innerHTML = md(acc);
        scrollToEnd();
      };

      const handle = (ev) => {
        const t = ev.type;
        if (t === "start") {
          state.sid = ev.sessionId;
          lastSid = ev.sessionId;
        } else if (t === "delta") {
          if (!gotFirst) { gotFirst = true; bubble.innerHTML = ""; }
          acc += ev.text;
          paint();
        } else if (t === "step") {
          if (!gotFirst) bubble.innerHTML = `<span class="hint">${esc(ev.text)}</span>`;
        } else if (t === "tool") {
          const row = document.createElement("div");
          row.className = "tool-line";
          const name = TOOL_NAMES[ev.name] || ev.name;
          const kw = ev.args && (ev.args.keyword || ev.args.ref || "");
          row.innerHTML = `<span class="dot-run"></span>${esc(name)}${kw ? " · " + esc(String(kw).slice(0, 24)) : ""}…`;
          toolsEl.appendChild(row);
          toolRows.push({ el: row, ref: (ev.args || {}).ref || "", name: ev.name, done: false });
          scrollToEnd();
        } else if (t === "tool_end") {
          // 同名工具可能连调几次，按顺序取最上面还没结束的那条
          const slot = toolRows.find((v) => v.name === ev.name && !v.done);
          if (slot) {
            slot.done = true;
            const ok = ev.ok !== false;
            slot.el.innerHTML = `<span class="${ok ? "dot-ok" : "dot-bad"}"></span>${esc(ev.brief || "")}`
              + (slot.ref ? ` <a href="#" data-ref="${esc(slot.ref)}">看原文</a>` : "");
            slot.el.classList.add(ok ? "done" : "fail");
          }
          if (ev.name === "save_topics" && ev.ok !== false) saved = true;
          if (ev.name === "request_collect" && ev.ok !== false) submitted = true;
          scrollToEnd();
        } else if (t === "warn") {
          toolsEl.insertAdjacentHTML("beforeend",
            `<div class="tool-line warn">⚠ ${esc(ev.text)}</div>`);
        } else if (t === "error") {
          bubble.innerHTML = `<div class="banner err">${esc(ev.message || "调用失败")}</div>`;
        } else if (t === "done") {
          aiEl.dataset.ms = ev.ms;
          if (!acc) bubble.innerHTML = `<span class="hint">（这轮没有输出文字）</span>`;
        }
      };

      try {
        const ctx = typeof opts.getCtx === "function" ? opts.getCtx() : {};
        state.ctrl = new AbortController();
        const r = await fetch("/api/chat/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sessionId: state.sid, text, ctx }),
          signal: state.ctrl.signal,
        });
        if (!r.ok || !r.body) throw new Error("HTTP " + r.status);

        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            const chunk = buf.slice(0, i);
            buf = buf.slice(i + 2);
            const line = chunk.split("\n").find((l) => l.startsWith("data:"));
            if (!line) continue;
            let ev;
            try { ev = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
            handle(ev);
          }
        }
      } catch (e) {
        if (e.name === "AbortError") {
          if (!acc) { acc = "（已停止）"; paint(); }
          else bubble.insertAdjacentHTML("beforeend", '<div class="hint">（已停止）</div>');
        } else {
          bubble.innerHTML = `<div class="banner err">连接断了：${esc(e.message)}</div>`;
        }
      } finally {
        state.busy = false;
        state.ctrl = null;
        if (quickEl) quickEl.style.display = "";
        sendBtn.textContent = "发送";
        sendBtn.classList.remove("danger");
        loadList();
        scrollToEnd();
        // 存了选题就让选题台刷新卡片，不然用户以为没存进去
        if (saved && typeof opts.onSaved === "function") {
          try { opts.onSaved(); } catch (e) {}
        }
        // 提交了采集：立刻拉一次队列，状态条和通知要马上出现
        if (submitted) {
          state.live = await refreshQueue();
          schedulePoll();
        }
      }
    }

    // ---- 事件
    host.querySelector("#cpNew").onclick = () => {
      if (state.busy) { toast("等这轮说完", ""); return; }
      state.sid = ""; lastSid = "";
      welcome();
      loadList();
      inputEl.focus();
    };

    host.querySelector("#cpDel").onclick = async () => {
      if (!state.sid) { toast("当前没有对话", ""); return; }
      if (!confirm("删掉这个对话？删了找不回来。")) return;
      await api.del(`/api/chat/sessions/${state.sid}`);
      state.sid = ""; lastSid = "";
      welcome(); loadList();
    };

    sessEl.onchange = () => openSession(sessEl.value);

    sendBtn.onclick = () => {
      if (state.busy) {
        if (state.ctrl) state.ctrl.abort();
        return;
      }
      const t = inputEl.value.trim();
      if (!t) return;
      inputEl.value = "";
      inputEl.style.height = "auto";
      send(t);
    };

    inputEl.addEventListener("input", () => {
      inputEl.style.height = "auto";
      inputEl.style.height = Math.min(inputEl.scrollHeight, 96) + "px";
    });

    inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        sendBtn.click();
      }
    });

    msgsEl.addEventListener("click", (e) => {
      const a = e.target.closest("[data-ref]");
      if (a) {
        e.preventDefault();
        showNote(a.dataset.ref, opts.openNote);
      }
    });

    // ---- 初始化：优先打开「有采集结果没看」的那个会话，
    // 其次接着上次，最后才退回新对话。不然采完了没人告诉你。
    await loadList();
    let unread = "";
    try {
      const p = await api.get("/api/chat/notices/pending");
      unread = ((p.items || [])[0] || {}).id || "";
    } catch (e) { /* 没拿到就按老逻辑 */ }
    const recent = sessEl.options.length > 1 ? sessEl.options[1].value : "";
    if (unread) await openSession(unread);
    else if (state.sid) await openSession(state.sid);
    else if (recent) await openSession(recent);
    else welcome();

    return {
      destroy() {
        if (state.ctrl) { try { state.ctrl.abort(); } catch (e) {} }
        if (state.poll) { clearTimeout(state.poll); state.poll = null; }
        state.busy = false;
        state.ctrl = null;
      },
      focus() { inputEl.focus(); },
      ask(text) {
        inputEl.value = text;
        inputEl.focus();
        sendBtn.click();
      },
    };
  }

  window.ChatPanel = { mount };
})();
