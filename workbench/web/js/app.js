/* 路由与启动 */
(function () {
  "use strict";
  const { api, toast } = window.WB;
  const viewEl = document.getElementById("view");
  const navEl = document.getElementById("nav");

  let cleanup = null;
  let currentView = null;

  function parseHash() {
    const raw = location.hash.replace(/^#\/?/, "");
    const [name] = raw.split("?");
    return name || "collect";
  }

  async function route() {
    // 对话台已经并进选题台，旧书签 #/chat 直接转过去，别报「没有这个页面」
    const alias = { chat: "topics" };
    const name = alias[parseHash()] || parseHash();
    const fn = window.VIEWS && window.VIEWS[name];
    if (!fn) {
      viewEl.innerHTML = `<div class="empty">没有这个页面：${name}</div>`;
      return;
    }

    if (typeof cleanup === "function") {
      try { cleanup(); } catch (e) {}
      cleanup = null;
    }

    currentView = name;
    navEl.querySelectorAll("a").forEach((a) => {
      a.classList.toggle("active", a.dataset.view === name);
    });
    viewEl.innerHTML = `<div class="empty">加载中…</div>`;

    try {
      const ret = await fn(viewEl, {});
      cleanup = typeof ret === "function" ? ret : null;
    } catch (e) {
      console.error(e);
      viewEl.innerHTML = `<div class="banner err">页面加载失败：${window.WB.esc(e.message)}</div>`;
    }
  }

  // 侧栏状态
  async function pollHealth() {
    const box = document.getElementById("footStatus");
    if (!box) return;
    try {
      const h = await api.get("/api/health");
      box.innerHTML = `
        <span class="status-ok">● 服务正常</span><br>
        端口 ${h.port} · PID ${h.pid}<br>
        ${h.crawlerRunning ? '<span class="status-warn">采集进行中</span>' : "采集空闲"}`;
    } catch (e) {
      box.innerHTML = `<span class="status-err">● 服务已断开</span><br>重新双击桌面图标即可`;
    }
  }

  // 采集在后台跑，用户可能正待在采集台或创作台。
  // 光靠对话面板里那条绿条不够——面板没挂载就永远看不见，所以导航上点一个红点。
  async function pollNotices() {
    const link = navEl.querySelector('a[data-view="topics"]');
    if (!link) return;
    try {
      const r = await api.get("/api/chat/notices/pending");
      const n = (r.items || []).reduce((s, x) => s + (x.count || 0), 0);
      let dot = link.querySelector(".nav-dot");
      if (n && !dot) {
        link.insertAdjacentHTML("beforeend", `<i class="nav-dot"></i>`);
        dot = link.querySelector(".nav-dot");
      } else if (!n && dot) {
        dot.remove();
        dot = null;
      }
      if (n && dot) dot.title = `有 ${n} 条采集结果没看`;
    } catch (e) { /* 服务断了自己会显示 */ }
  }

  window.addEventListener("hashchange", route);
  if (!location.hash) location.hash = "#/collect";
  route();
  pollHealth();
  setInterval(pollHealth, 15000);
  pollNotices();
  setInterval(pollNotices, 30000);
  // 切页时立刻再看一眼，别等下一轮
  window.addEventListener("hashchange", pollNotices);
})();
