/* 档案台：拆解自己的账号，三份产物给选题、创作、复盘共用。 */
(function () {
  "use strict";
  const { api, esc, toast, modal, confirmDialog, fmtDate } = window.WB;

  // ---------------------------------------------------------------- 轻量 markdown
  function inline(s) {
    return s
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/`([^`]+)`/g, '<code class="mono">$1</code>');
  }

  function md(src) {
    const lines = String(src || "").split(/\r?\n/);
    const out = [];
    let list = null;
    const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
    for (const raw of lines) {
      const line = raw.replace(/\s+$/, "");
      if (!line.trim()) { close(); continue; }
      let m;
      if ((m = line.match(/^(#{1,4})\s+(.+)$/))) {
        close();
        const size = [17, 15, 13.5, 13][m[1].length - 1];
        out.push(`<div style="font-weight:700;font-size:${size}px;margin:14px 0 6px;color:#0b3b32">${inline(m[2])}</div>`);
      } else if ((m = line.match(/^[-*]\s+(.+)$/))) {
        if (list !== "ul") { close(); out.push('<ul style="margin:6px 0 6px 18px;padding:0">'); list = "ul"; }
        out.push(`<li style="margin:3px 0">${inline(m[1])}</li>`);
      } else if ((m = line.match(/^\d+[.、]\s+(.+)$/))) {
        if (list !== "ol") { close(); out.push('<ol style="margin:6px 0 6px 20px;padding:0">'); list = "ol"; }
        out.push(`<li style="margin:3px 0">${inline(m[1])}</li>`);
      } else if (line.startsWith(">")) {
        close();
        out.push(`<div style="border-left:3px solid var(--line);padding:2px 0 2px 10px;color:var(--muted);margin:6px 0">${inline(line.replace(/^>\s?/, ""))}</div>`);
      } else {
        close();
        out.push(`<p style="margin:6px 0;line-height:1.8">${inline(line)}</p>`);
      }
    }
    close();
    return out.join("");
  }

  const stateTag = (s) => ({
    active: '<span class="status-ok">现行</span>',
    candidate: '<span class="status-warn">候选</span>',
    "recently-absent": '<span class="muted">近期未出现</span>',
  }[s] || `<span class="muted">${esc(s || "")}</span>`);

  // 记忆和试写里带换行，直接塞进 div 会挤成一行
  const nl2br = (s) => esc(s || "").replace(/\r?\n/g, "<br>");

  const srcTag = (m) => ({
    stated: '<span class="status-ok">本人明说</span>',
    induced: '<span class="muted">多篇归纳</span>',
    guess: '<span class="status-warn">推断·待确认</span>',
  }[m] || "");

  // ---------------------------------------------------------------- 主视图
  async function renderProfile(root) {
    let st = null;
    let prod = null;
    let timer = null;
    let tab = "creator";
    let openType = null;

    root.innerHTML = `<div class="page-head"><h1>档案台</h1>
      <span class="sub">把你的号拆成档案，选题、写稿、复盘都用同一份</span></div>
      <div id="pfBody"><div class="empty">加载中…</div></div>`;

    async function load() {
      try {
        [st, prod] = await Promise.all([api.get("/api/profile/status"), api.get("/api/profile")]);
      } catch (e) {
        root.querySelector("#pfBody").innerHTML =
          `<div class="banner err">${esc(e.message)}</div>`;
        return;
      }
      paint();
      syncPoll();
    }

    function syncPoll() {
      const jobOn = st && (st.running || (st.job && st.job.status === "running"));
      const crawlOn = st && st.own && st.own.syncing;
      const need = jobOn || crawlOn;
      if (need && !timer) {
        timer = setInterval(async () => {
          try {
            st = await api.get("/api/profile/status");
            const stillJob = st.running || (st.job && st.job.status === "running");
            const stillCrawl = st.own && st.own.syncing;
            if (!stillJob) {
              prod = await api.get("/api/profile");
            }
            if (!stillJob && !stillCrawl) { clearInterval(timer); timer = null; }
            paint();
            if (!stillJob && !stillCrawl) syncPoll();
          } catch (e) { /* 服务重启时静默 */ }
        }, 1800);
      }
    }

    function paint() {
      const body = root.querySelector("#pfBody");
      body.innerHTML = sourceCard() + buildCard() + (prod && hasProduct() ? productArea() : "");
      bind();
    }

    function hasProduct() {
      return (prod.style.index || []).length > 0 || (prod.creator.markdown || "").trim();
    }

    // ---------------------------------------------------------------- 账号来源
    function syncLine(own) {
      const ls = own.lastSync || {};
      if (own.syncing) {
        return `<div class="hint" style="color:#b4761a">● 正在拉取……跑在后台，可以关掉这个页面。</div>`;
      }
      if (ls.status === "empty") {
        return `<div class="banner err" style="margin-top:8px">上次一条都没采到：${esc(ls.note || "原因不明，看采集台的日志")}</div>`;
      }
      if (ls.status === "failed") {
        return `<div class="banner err" style="margin-top:8px">上次采集失败（退出码非 0），去采集台看日志。</div>`;
      }
      if (ls.status === "done" && ls.saved) {
        return `<div class="hint" style="color:var(--ok)">✓ 上次新增 ${ls.saved} 篇</div>`;
      }
      if (ls.status === "done" && !ls.saved) {
        return `<div class="hint">上次跑完了，但没有新帖（都是已有的）。</div>`;
      }
      return "";
    }

    function sourceCard() {
      const own = st.own || {};
      const acc = st.account || {};
      const cnt = own.count || 0;
      let line = "";
      if (cnt) {
        line = `已采 <b>${cnt}</b> 篇${own.firstAt ? `（${esc(own.firstAt)} 至 ${esc(own.latestAt || "")}）` : ""}`;
        if (own.lastSyncAt) line += ` · 上次同步 ${esc(fmtDate(own.lastSyncAt))}`;
      } else {
        line = "还没有你自己的帖子。贴主页链接，采一次，才有东西可拆。";
      }
      return `
      <div class="card">
        <h2>我的账号 <span class="tag">只采你自己，和赛道样本分开存</span></h2>
        <div class="row" style="gap:8px;flex-wrap:wrap">
          <input type="text" id="pfUrl" style="flex:1;min-width:280px" placeholder="小红书主页链接（App 里「分享主页」复制出来的那个，带 xsec_token 成功率最高）" value="${esc(acc.homepageInput || acc.homepageUrl || "")}">
          <button class="sm" id="pfCrawl" ${own.syncing ? "disabled" : ""}>${own.syncing ? "拉取中…" : "拉取我的帖子"}</button>
          ${cnt ? `<button class="sm" id="pfMore" ${own.syncing ? "disabled" : ""}>再拉一页</button>` : ""}
        </div>
        <div class="hint" style="margin-top:8px">${line}</div>
        ${cnt ? `<div class="hint">每页约 20 篇。要拆得细就得有量：每类至少 2 篇正文才建得起来。</div>` : ""}
        ${syncLine(own)}
      </div>`;
    }

    // ---------------------------------------------------------------- 拆解
    function buildCard() {
      const job = st.job || {};
      const state = st.state || "empty";
      const cnt = (st.own && st.own.count) || 0;
      const running = st.running || job.status === "running";

      if (running) {
        const steps = (job.steps || []).map((s) => {
          const icon = { done: "✓", running: "●", failed: "✕", pending: "○" }[s.status] || "○";
          const color = { done: "var(--ok)", running: "#b4761a", failed: "var(--err)", pending: "var(--muted)" }[s.status];
          return `<div class="row" style="gap:10px;margin:5px 0">
            <span style="color:${color};width:14px">${icon}</span>
            <span style="min-width:96px;color:${s.status === "pending" ? "var(--muted)" : "inherit"}">${esc(s.name)}</span>
            ${s.ai ? '<span class="hint">调模型</span>' : '<span class="hint">不花钱</span>'}
            <span class="hint" style="margin-left:auto">${esc(s.detail || "")}</span>
          </div>`;
        }).join("");
        const u = job.usage || {};
        return `<div class="card">
          <h2>正在拆解 <span class="tag">关掉页面也会继续跑</span></h2>
          ${steps}
          <div class="hint" style="margin-top:10px">已调用 ${u.calls || 0} 次模型${u.tokens ? `，约 ${u.tokens} tokens` : ""}。中途某一步失败不影响其他步骤，失败的会标出来。</div>
        </div>`;
      }

      const index = (prod && prod.style.index) || [];
      const active = index.filter((x) => x.status === "active");
      const records = (st.records || []).slice(-3).reverse();

      let head = "";
      if (state === "empty" || !active.length) {
        head = `<div class="banner">第一次拆，大概 5–8 次模型调用（一次几分钱）。会读你的目录和最多 12 篇正文，
          按内容机制分类型，再给每一类写写法规则、记下你是谁、写一版运营规划。</div>`;
      } else {
        head = `<div class="stat-row" style="margin-bottom:10px">
          <div class="stat"><b>v${st.version || 1}</b><span>档案版本</span></div>
          <div class="stat"><b>${active.length}</b><span>现行类型</span></div>
          <div class="stat"><b>${st.counts.catalog || 0}</b><span>目录篇数</span></div>
          <div class="stat"><b>${st.counts.deep || 0}</b><span>深读篇数</span></div>
          <div class="stat"><b>${st.counts.memory || 0}</b><span>记住的事</span></div>
        </div>`;
        if (records.length) {
          head += `<div class="hint" style="margin-bottom:10px">${records.map(esc).join("<br>")}</div>`;
        }
        if (job.warnings && job.warnings.length) {
          head += `<div class="banner">这次有几步没跑完：<br>${job.warnings.map(esc).join("<br>")}</div>`;
        }
      }

      const canBuild = cnt >= 3;
      return `<div class="card">
        <h2>拆解 <span class="tag">产物只有三处：风格包 / 空间记忆 / 运营规划</span></h2>
        ${head}
        <div class="row">
          <button class="primary sm" id="pfBuild" ${canBuild ? "" : "disabled"}>
            ${active.length ? "重新拆一遍" : "开始拆解"}</button>
          ${active.length ? '<button class="sm" id="pfLearn">补充学习（只学新的）</button>' : ""}
          <span class="hint" id="pfBuildHint">${canBuild ? "" : (cnt ? `只有 ${cnt} 篇，样本太少，至少 3 篇` : "先拉取你的帖子")}</span>
        </div>
      </div>`;
    }

    // ---------------------------------------------------------------- 三份产物
    function productArea() {
      const idx = prod.style.index || [];
      const active = idx.filter((x) => x.status === "active");
      const cand = idx.filter((x) => x.status !== "active");
      const mem = prod.memory.items || [];
      const pend = mem.filter((m) => m.status === "pending");
      const cre = prod.creator || {};
      const samples = prod.samples || [];

      const tabs = [
        ["creator", "运营规划"],
        ["style", `风格包${active.length ? `（${active.length}）` : ""}`],
        ["memory", `空间记忆${pend.length ? ` · ${pend.length} 待确认` : ""}`],
      ].map(([k, label]) =>
        `<button class="sm ${tab === k ? "primary" : ""}" data-ptab="${k}">${label}</button>`).join("");

      return `<div class="card" style="margin-top:14px">
        <div class="row" style="margin-bottom:12px">${tabs}</div>
        ${tab === "creator" ? creatorPane(cre) : ""}
        ${tab === "style" ? stylePane(active, cand, samples) : ""}
        ${tab === "memory" ? memoryPane(mem, pend) : ""}
      </div>`;
    }

    function creatorPane(cre) {
      const f = cre.fields || {};
      const unknown = cre.unknown || [];
      return `
        <div class="row" style="margin-bottom:8px">
          <span class="hint">这份规划里的定位和受众，会被选题判断直接引用。</span>
          <span class="spacer"></span>
          <button class="sm" id="pfEditCreator">编辑</button>
        </div>
        ${f.positioning ? `<div style="margin:8px 0"><b>定位</b>　${esc(f.positioning)}</div>` : ""}
        ${f.audience ? `<div style="margin:8px 0"><b>受众</b>　${esc(f.audience)}</div>` : ""}
        ${(f.pillars || []).length ? `<div style="margin:8px 0"><b>内容支柱</b>　${f.pillars.map((p) => `<span class="chip">${esc(p)}</span>`).join(" ")}</div>` : ""}
        ${f.cadence ? `<div style="margin:8px 0"><b>节奏</b>　${esc(f.cadence)}</div>` : ""}
        <div style="border-top:1px solid var(--line);margin:12px 0"></div>
        ${cre.markdown ? md(cre.markdown) : '<div class="empty">还没有规划正文</div>'}
        ${unknown.length ? `<div class="banner" style="margin-top:12px">还没有证据、暂时判断不了的：${unknown.map(esc).join("；")}</div>` : ""}`;
    }

    function stylePane(active, cand, samples) {
      const cardList = active.map((t) => {
        const mod = (prod.style.modules || {})[t.id] || {};
        const open = openType === t.id;
        const fixCount = (mod.fixes || []).length;
        return `<div class="list-item" style="margin-bottom:8px">
          <div class="row" style="cursor:pointer" data-type="${esc(t.id)}">
            <b>${esc(t.name)}</b>
            <span class="mono muted">${esc(t.id)}</span>
            ${stateTag(t.status)}
            <span class="hint">深读 ${t.deepCount || 0} 篇 · 目录同类 ${t.catalogCount || 0} 篇</span>
            ${fixCount ? `<span class="status-warn">你纠正过 ${fixCount} 次</span>` : ""}
            <span class="spacer"></span>
            <span class="hint">${open ? "收起" : "查看规则"}</span>
          </div>
          ${t.when ? `<div class="hint" style="margin-top:4px">适用：${esc(t.when)}</div>` : ""}
          ${open ? `<div style="margin-top:10px;border-top:1px solid var(--line);padding-top:10px">
            ${mod.rules ? md(mod.rules) : '<div class="empty">这一类的规则还没写成功</div>'}
            ${(mod.fixes || []).length ? `<div class="banner" style="margin-top:10px">你的纠正（下次学习优先遵守）：<br>${mod.fixes.map((x) => `· ${esc(x.note)}`).join("<br>")}</div>` : ""}
          </div>` : ""}
        </div>`;
      }).join("");

      const candList = cand.map((t) => `<div class="row" style="margin:4px 0">
        <span>${esc(t.name)}</span>${stateTag(t.status)}
        <span class="hint">${esc(t.when || "")}</span></div>`).join("");

      const sampleList = samples.length ? `
        <div style="border-top:1px solid var(--line);margin:14px 0 10px"></div>
        <div style="font-weight:700;margin-bottom:6px">试写验收 <span class="tag">这是用来给你挑错的，不是成稿</span></div>
        <div class="hint" style="margin-bottom:8px">按每类的规则试写了一句。哪句最不像你？指出来，我记进那一类的规则里，下次学习优先遵守。</div>
        ${samples.map((s, i) => `<div class="list-item" style="margin-bottom:8px">
          <div class="row"><b>${esc(s.title || "")}</b><span class="spacer"></span>
            <span class="hint">${esc(s.typeName || s.typeId || "")}</span></div>
          <div style="margin:6px 0">${nl2br(s.opening || "")}</div>
          <div class="row"><span class="hint">${esc(s.why || "")}</span><span class="spacer"></span>
            <button class="sm" data-nosample="${i}">这句不像我</button></div>
        </div>`).join("")}` : "";

      return `${cardList || '<div class="empty">还没有建立任何现行类型</div>'}
        ${cand.length ? `<div style="margin-top:12px"><div class="hint" style="margin-bottom:4px">候选（证据不足 2 篇，暂不建规则）</div>${candList}</div>` : ""}
        ${sampleList}`;
    }

    function memoryPane(mem, pend) {
      if (!mem.length) return '<div class="empty">还没有记住任何事</div>';
      const slots = {};
      for (const m of mem) (slots[m.slotName || m.slot] = slots[m.slotName || m.slot] || []).push(m);
      const blocks = Object.keys(slots).map((k) => `
        <div style="margin-bottom:12px">
          <div class="hint" style="margin-bottom:4px">${esc(k)}</div>
          ${slots[k].map((m) => `<div class="list-item" style="margin-bottom:6px">
            <div class="row" style="gap:8px;align-items:flex-start">
              <span style="flex:1">${nl2br(m.content)}</span>
              ${srcTag(m.source)}
            </div>
            <div class="row" style="margin-top:4px">
              <span class="hint">${(m.evidence || []).slice(0, 3).map((e) => esc(String(e))).join("、") || "无证据引用"}${(m.evidence || []).length > 3 ? " 等" : ""}</span>
              <span class="spacer"></span>
              ${m.status === "pending"
                ? `<button class="sm" data-mem="${esc(m.id)}" data-act="confirm">是我</button>
                   <button class="sm" data-mem="${esc(m.id)}" data-act="edit">改一下</button>
                   <button class="sm" data-mem="${esc(m.id)}" data-act="drop">不是</button>`
                : `<button class="sm" data-mem="${esc(m.id)}" data-act="edit">改</button>`}
            </div>
          </div>`).join("")}
        </div>`).join("");
      return `${pend.length ? `<div class="banner">下面标「推断·待确认」的是我从样本里猜的，不是你明说的。确认或纠正一下，以后写稿会更准。</div>` : ""}
        ${blocks}`;
    }

    // ---------------------------------------------------------------- 事件
    function bind() {
      const urlOf = () => (root.querySelector("#pfUrl") || {}).value || "";

      const crawl = root.querySelector("#pfCrawl") || root.querySelector("#pfMore");
      if (crawl) {
        crawl.onclick = async () => {
          const url = urlOf().trim();
          if (!url) { toast("先把主页链接贴上", "warn"); return; }
          crawl.disabled = true;
          // 再拉一页要往上加，不然永远停在第一次的 20 篇
          const have = (st.own && st.own.count) || 0;
          const want = crawl.id === "pfMore" ? Math.max(20, have + 20) : 20;
          try {
            await api.post("/api/profile/crawl", { homepageUrl: url, maxNotes: want });
            toast(`已开始拉取（上限 ${want} 篇），采完会自动入库。`, "ok");
            st = await api.get("/api/profile/status");
            paint();
          } catch (e) {
            toast("启动失败：" + e.message, "err");
            crawl.disabled = false;
          }
        };
      }

      const build = root.querySelector("#pfBuild");
      if (build) {
        build.onclick = () => {
          const url = urlOf().trim();
          const init = !(prod.style.index || []).some((x) => x.status === "active");
          confirmDialog(
            init
              ? "开始拆解？会用你的目录和最多 12 篇正文，调用 5–8 次模型，一次几分钱。跑的时候可以关掉这个页面。"
              : "重新拆一遍？会覆盖现在这一版规则。已有的纠正记录会保留。",
            async () => {
              try {
                await api.post("/api/profile/build", { homepageUrl: url, limit: 12, mode: "init" });
                toast("开始拆解", "ok");
                st = await api.get("/api/profile/status");
                paint();
              } catch (e) { toast("启动失败：" + e.message, "err"); }
            });
        };
      }

      const learn = root.querySelector("#pfLearn");
      if (learn) {
        learn.onclick = () => confirmDialog(
          "补充学习？只读上次学习之后的新帖，已有的类型和规则会保留，不会推翻你已经确认过的内容。",
          async () => {
            try {
              await api.post("/api/profile/learn", { limit: 12 });
              toast("开始补充学习", "ok");
              st = await api.get("/api/profile/status");
              paint();
            } catch (e) { toast("启动失败：" + e.message, "err"); }
          });
      }

      root.querySelectorAll("[data-ptab]").forEach((b) => {
        b.onclick = () => { tab = b.dataset.ptab; paint(); };
      });

      root.querySelectorAll("[data-type]").forEach((el) => {
        el.onclick = () => { openType = openType === el.dataset.type ? null : el.dataset.type; paint(); };
      });

      root.querySelectorAll("[data-nosample]").forEach((b) => {
        b.onclick = () => {
          const s = (prod.samples || [])[+b.dataset.nosample] || {};
          modal({
            title: "哪句不像你",
            html: `<div class="hint" style="margin-bottom:8px">${esc(s.title || "")}<br>${nl2br(s.opening || "")}</div>
              <label class="field"><span>哪里不像（写一句就行，可以留空）</span>
                <input type="text" id="noLike" placeholder="例：我不会用这种反问开头"></label>
              <div class="row"><button class="primary" id="noLikeOk">记下来</button>
              <span class="hint">会记进「${esc(s.typeName || "")}」这一类，下次学习优先遵守。</span></div>`,
            onMount: (body, wrap) => {
              body.querySelector("#noLikeOk").onclick = async () => {
                try {
                  await api.post("/api/profile/correct", {
                    typeId: s.typeId, sampleIndex: +b.dataset.nosample,
                    note: body.querySelector("#noLike").value.trim(),
                  });
                  toast("已记入这一类规则", "ok");
                  wrap.remove();
                  prod = await api.get("/api/profile");
                  paint();
                } catch (e) { toast("记录失败：" + e.message, "err"); }
              };
            },
          });
        };
      });

      root.querySelectorAll("[data-mem]").forEach((b) => {
        b.onclick = async () => {
          const act = b.dataset.act;
          try {
            if (act === "edit") {
              const row = b.closest(".list-item");
              const old = row.querySelector("span").textContent;
              modal({
                title: "改成什么",
                html: `<label class="field"><span>一条说清一件事</span>
                  <textarea id="memNew" rows="4">${esc(old)}</textarea></label>
                  <div class="row"><button class="primary" id="memOk">保存</button></div>`,
                onMount: (body, wrap) => {
                  body.querySelector("#memOk").onclick = async () => {
                    try {
                      await api.put("/api/profile/memory", {
                        id: b.dataset.mem, action: "edit",
                        content: body.querySelector("#memNew").value.trim(),
                      });
                      wrap.remove();
                      prod = await api.get("/api/profile");
                      paint();
                      toast("改好了", "ok");
                    } catch (e) { toast(e.message, "err"); }
                  };
                },
              });
              return;
            }
            await api.put("/api/profile/memory", { id: b.dataset.mem, action: act });
            prod = await api.get("/api/profile");
            paint();
          } catch (e) { toast(e.message, "err"); }
        };
      });

      const editCre = root.querySelector("#pfEditCreator");
      if (editCre) {
        editCre.onclick = () => {
          const cre = prod.creator || {};
          modal({
            title: "编辑运营规划",
            html: `<label class="field"><span>规划正文（markdown）</span>
                <textarea class="code" id="creMd" rows="16">${esc(cre.markdown || "")}</textarea></label>
              <div class="hint" style="margin-bottom:8px">这段是长期规划。定位和受众另外存一份给选题判断用，改这里不会改动那两项。</div>
              <div class="row"><button class="primary" id="creOk">保存</button></div>`,
            onMount: (body, wrap) => {
              body.querySelector("#creOk").onclick = async () => {
                try {
                  await api.put("/api/profile/creator", { markdown: body.querySelector("#creMd").value });
                  wrap.remove();
                  prod = await api.get("/api/profile");
                  paint();
                  toast("规划已保存", "ok");
                } catch (e) { toast(e.message, "err"); }
              };
            },
          });
        };
      }
    }

    await load();
    return () => { if (timer) clearInterval(timer); };
  }

  window.VIEWS = window.VIEWS || {};
  window.VIEWS.profile = renderProfile;
})();
