/* 核心工具：API 封装、格式化、通用组件（toast / 抽屉 / 弹窗 / 双模 AI 面板） */
(function () {
  "use strict";

  // ---------------------------------------------------------------- API
  async function req(method, url, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    const r = await fetch(url, opt);
    let data = null;
    const text = await r.text();
    if (text) {
      try { data = JSON.parse(text); } catch (e) { data = { raw: text }; }
    }
    if (!r.ok) {
      const msg = (data && (data.detail || data.message)) || `HTTP ${r.status}`;
      const err = new Error(msg);
      err.status = r.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  const api = {
    get: (u) => req("GET", u),
    post: (u, b) => req("POST", u, b === undefined ? {} : b),
    put: (u, b) => req("PUT", u, b || {}),
    patch: (u, b) => req("PATCH", u, b || {}),
    del: (u) => req("DELETE", u),
  };

  // ---------------------------------------------------------------- 工具
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function fmtNum(n) {
    if (n === null || n === undefined) return "—";
    if (typeof n !== "number") return esc(n);
    if (n >= 100000000) return (n / 100000000).toFixed(1) + "亿";
    if (n >= 10000) return (n / 10000).toFixed(1) + "万";
    return String(n);
  }

  function fmtPct(v) {
    if (v === null || v === undefined) return "—";
    return (v * 100).toFixed(1) + "%";
  }

  function fmtDate(v) {
    if (!v) return "—";
    if (typeof v === "number") {
      const ms = v > 1e12 ? v : v * 1000;
      const d = new Date(ms);
      if (isNaN(d)) return "—";
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    }
    return String(v).slice(0, 16).replace("T", " ");
  }

  function fmtSec(s) {
    if (!s && s !== 0) return "—";
    const m = Math.floor(s / 60);
    return m > 0 ? `${m}分${s % 60}秒` : `${s}秒`;
  }

  function relTime(iso) {
    if (!iso) return "";
    const t = new Date(iso).getTime();
    const diff = (Date.now() - t) / 1000;
    if (isNaN(diff)) return iso;
    if (diff < 60) return "刚刚";
    if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
    if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
    return Math.floor(diff / 86400) + " 天前";
  }

  const PLATFORM_NAMES = { xhs: "小红书", dy: "抖音", bili: "B站" };

  // ---------------------------------------------------------------- toast
  function toast(msg, kind) {
    const box = document.getElementById("toast");
    const el = document.createElement("div");
    el.className = "toast-item" + (kind ? " " + kind : "");
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 250); }, 3200);
  }

  // ---------------------------------------------------------------- 弹窗
  function modal(opts) {
    const wrap = document.createElement("div");
    wrap.className = "modal-wrap";
    wrap.innerHTML = `
      <div class="modal">
        <h3>${esc(opts.title || "")}</h3>
        <div class="modal-body"></div>
        <div class="actions"></div>
      </div>`;
    const body = wrap.querySelector(".modal-body");
    const actions = wrap.querySelector(".actions");

    (opts.buttons || [{ text: "关闭" }]).forEach((b) => {
      const btn = document.createElement("button");
      btn.textContent = b.text;
      if (b.kind) btn.className = b.kind;
      btn.onclick = async () => {
        if (b.onClick) {
          const keep = await b.onClick(body, wrap);
          if (keep === false) return;
        }
        wrap.remove();
      };
      actions.appendChild(btn);
    });

    if (opts.html) body.innerHTML = opts.html;
    if (opts.onMount) opts.onMount(body, wrap);
    wrap.addEventListener("click", (e) => { if (e.target === wrap && !opts.locked) wrap.remove(); });
    document.body.appendChild(wrap);
    return wrap;
  }

  // ---------------------------------------------------------------- 抽屉
  function drawer(title, html, onMount) {
    const wrap = document.createElement("div");
    wrap.className = "overlay";
    wrap.innerHTML = `<div class="drawer"><h3>${esc(title)}</h3><div class="drawer-body"></div></div>`;
    const body = wrap.querySelector(".drawer-body");
    body.innerHTML = html;
    wrap.addEventListener("click", (e) => { if (e.target === wrap) wrap.remove(); });
    document.body.appendChild(wrap);
    if (onMount) onMount(body, wrap);
    return wrap;
  }

  // ---------------------------------------------------------------- 双模 AI
  // 统一的入口：按设置里的模式决定是"一键直出"还是"出提示词让你复制"
  async function runAI(task, ctx, opts) {
    const o = opts || {};
    let st;
    try { st = await api.get("/api/settings"); }
    catch (e) { toast("读取设置失败：" + e.message, "err"); return; }

    const useApi = st.ai.mode === "api" && st.ai.apiKeySet;

    if (!useApi) {
      const prep = await api.post("/api/ai/prepare", { task, ctx });
      openPromptPanel(task, prep, o);
      return;
    }

    toast("正在调用模型…");
    const res = await api.post("/api/ai/run", { task, ctx });
    if (res.ok) {
      if (o.onApiResult) o.onApiResult(res);
      else if (o.onDone) o.onDone(res);
      return;
    }
    if (res.fallbackPrompt) {
      openPromptPanel(task, Object.assign({}, res.fallbackPrompt, { output: "json" }), o,
        `${res.message}｜已自动切换为复制模式`);
      return;
    }
    toast(res.message || "AI 调用失败", "err");
  }

  // 复制模式面板：上半是提示词（可复制），下半是粘贴结果（可导入）
  function openPromptPanel(task, prep, opts, warn) {
    const o = opts || {};
    const full = `【系统提示词】\n${prep.systemPrompt || ""}\n\n【任务】\n${prep.userPrompt || ""}`;
    const html = `
      ${warn ? `<div class="banner">${esc(warn)}</div>` : ""}
      <div class="banner">
        把下面的提示词整段复制，粘到任意 AI（WorkBuddy / DeepSeek / 豆包都行）。
        拿到结果后贴回下面的框，点「导入结果」。
      </div>
      <label class="field"><span>提示词（${prep.chars || full.length} 字，约 ${prep.estTokens || "?"} tokens）</span>
        <textarea class="code" id="promptBox" rows="12" readonly>${esc(full)}</textarea>
      </label>
      <div class="row" style="margin-bottom:12px">
        <button class="primary sm" id="copyBtn">复制提示词</button>
        <button class="sm" id="dlBtn">下载为 txt</button>
        <span class="hint" id="copyHint"></span>
      </div>
      <label class="field"><span>把 AI 的结果贴到这里</span>
        <textarea class="code" id="resultBox" rows="10" placeholder="粘贴返回的 JSON 或表格…"></textarea>
      </label>
      <div class="row">
        <button class="primary" id="ingestBtn">导入结果</button>
        <span class="hint">导入后会直接写进对应的库，不用再手动录。</span>
      </div>`;

    const wrap = modal({
      title: `复制模式 · ${prep.taskName || task}`,
      html,
      buttons: [{ text: "关掉" }],
      onMount(body) {
        body.querySelector("#copyBtn").onclick = async () => {
          const box = body.querySelector("#promptBox");
          try {
            await navigator.clipboard.writeText(box.value);
            body.querySelector("#copyHint").textContent = "已复制";
            toast("提示词已复制", "ok");
          } catch (e) {
            box.select();
            document.execCommand("copy");
            body.querySelector("#copyHint").textContent = "已复制（兼容模式）";
          }
        };
        body.querySelector("#dlBtn").onclick = () => {
          const blob = new Blob([full], { type: "text/plain;charset=utf-8" });
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = `${task}-提示词.txt`;
          a.click();
          URL.revokeObjectURL(a.href);
        };
        body.querySelector("#ingestBtn").onclick = async () => {
          const raw = body.querySelector("#resultBox").value.trim();
          if (!raw) { toast("还没贴结果", "err"); return; }
          const btn = body.querySelector("#ingestBtn");
          btn.disabled = true;
          btn.textContent = "导入中…";
          try {
            const res = await api.post("/api/ai/ingest", {
              task,
              rawText: raw,
              targetId: o.targetId || "",
              accountId: o.accountId || "",
            });
            if (!res.ok) { toast(res.message || "导入失败", "err"); return; }
            toast(`已导入 ${res.created} 条（${res.kind}）`, "ok");
            wrap.remove();
            if (o.onDone) o.onDone(res);
          } catch (e) {
            toast(e.message, "err");
          } finally {
            btn.disabled = false;
            btn.textContent = "导入结果";
          }
        };
      },
    });
  }

  // 确认框
  function confirmDialog(text, onYes) {
    modal({
      title: "确认",
      html: `<div class="hint">${esc(text)}</div>`,
      buttons: [
        { text: "取消" },
        { text: "确定", kind: "danger", onClick: () => { onYes(); } },
      ],
    });
  }

  window.WB = {
    api, esc, fmtNum, fmtPct, fmtDate, fmtSec, relTime, toast, modal, drawer,
    runAI, openPromptPanel, confirmDialog, PLATFORM_NAMES,
  };
})();
