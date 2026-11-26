/* 视图：创作台 + 复盘台 + 评论洞察 + 设置 */
(function () {
  "use strict";
  const { api, esc, fmtNum, fmtPct, fmtDate, toast, drawer, runAI, confirmDialog } = window.WB;

  // ================================================================ 创作台

  const st = { drafts: [], topics: [], cur: null, tab: "plan", enums: null };

  async function renderCreate(root) {
    // 从选题台「去写作」过来会带 ?draft=xxx，直接选中那一篇，不用用户再去左边找
    const qs = new URLSearchParams((location.hash.split("?")[1] || ""));
    const wantDraft = qs.get("draft") || "";
    root.innerHTML = `
      <div class="page-head">
        <h1>创作台</h1>
        <span class="sub">定形态 → 排页序 → 写正文 → 起标题 → 配封面</span>
        <span class="spacer"></span>
        <button class="sm" id="newFromTopic">+ 从选题新建</button>
      </div>
      <div class="split-3">
        <div class="card">
          <h2>稿件 <span class="tag" id="draftTag"></span></h2>
          <div id="draftList"><div class="empty">加载中…</div></div>
        </div>

        <div class="card" id="editorCard">
          <div class="empty">从左边选一篇，或从选题台采纳一条再过来。</div>
        </div>

        <div class="aside-r">
          <div class="card">
            <div class="tabs">
              <button data-tab="plan" class="active">页序</button>
              <button data-tab="title">标题</button>
              <button data-tab="cover">封面</button>
            </div>
            <div id="sidePane"><div class="empty">先选一篇稿件</div></div>
          </div>
        </div>
      </div>
    `;

    async function loadDrafts(selectId) {
      const box = root.querySelector("#draftList");
      try {
        const res = await api.get("/api/drafts");
        st.drafts = res.items || [];
        root.querySelector("#draftTag").textContent = `${st.drafts.length} 篇`;
        if (!st.drafts.length) {
          box.innerHTML = `<div class="empty">还没有稿件。<br>去选题台采纳一条选题。</div>`;
          return;
        }
        box.innerHTML = st.drafts.map((d) => `
          <div class="list-item ${st.cur && st.cur.id === d.id ? "active" : ""}" data-draft="${esc(d.id)}">
            <div class="t">${esc(d.publishedTitle || d.topicTitle || "(未命名)")}</div>
            <div class="small muted">${esc(window.WB.PLATFORM_NAMES[d.platform] || d.platform)}
              · ${esc(d.contentForm || "未定形态")} · ${d.bodyChars || 0} 字
              ${d.status === "published" ? '· <span class="status-ok">已发布</span>' : ""}</div>
          </div>`).join("");
        box.querySelectorAll("[data-draft]").forEach((el) => {
          el.onclick = () => selectDraft(el.dataset.draft);
        });
        const target = selectId || (st.cur && st.cur.id) || (st.drafts[0] && st.drafts[0].id);
        if (target) selectDraft(target);
      } catch (e) {
        box.innerHTML = `<div class="banner err">${esc(e.message)}</div>`;
      }
    }

    async function selectDraft(id) {
      const d = st.drafts.find((x) => x.id === id);
      if (!d) return;
      st.cur = d;
      root.querySelectorAll("[data-draft]").forEach((el) =>
        el.classList.toggle("active", el.dataset.draft === id));

      let tp = null;
      try {
        const t = await api.get("/api/topics");
        st.enums = t.enums;
        tp = (t.items || []).find((x) => x.id === d.topicId);
      } catch (e) {}

      const forms = (st.enums && st.enums.forms) || [];
      const formOpts = ["", ...forms].map((f) =>
        `<option value="${f}" ${d.contentForm === f ? "selected" : ""}>${f || "未定"}</option>`).join("");
      // 选题定型在采纳时就锁死了，创作台只负责显示和继承，不在这里改
      const ei = d.intent || (tp && tp.editorialIntent) || {};
      const formNote = d.formInferred
        ? `<span class="hint status-warn">原选题写的是「${esc(d.formRaw || "空")}」，按体裁推成 ${esc(d.contentForm || "未定")}；要改直接在上面选</span>`
        : "";

      root.querySelector("#editorCard").innerHTML = `
        <div class="row" style="margin-bottom:10px">
          <label class="field" style="margin:0;min-width:120px"><span>平台</span>
            <select id="dPlatform">
              <option value="xhs" ${d.platform === "xhs" ? "selected" : ""}>小红书</option>
              <option value="dy" ${d.platform === "dy" ? "selected" : ""}>抖音</option>
              <option value="bili" ${d.platform === "bili" ? "selected" : ""}>B站</option>
            </select>
          </label>
          <label class="field grow" style="margin:0"><span>内容形式</span>
            <select id="dForm">${formOpts}</select>
          </label>
          <label class="field" style="margin:0;min-width:110px"><span>状态</span>
            <select id="dStatus">
              <option value="draft" ${d.status === "draft" ? "selected" : ""}>草稿</option>
              <option value="ready" ${d.status === "ready" ? "selected" : ""}>待发布</option>
              <option value="published" ${d.status === "published" ? "selected" : ""}>已发布</option>
            </select>
          </label>
        </div>
        ${formNote ? `<div class="hint" style="margin:-4px 0 8px">${formNote}</div>` : ""}
        ${tp ? `<div class="banner">
          <div>选题：${esc(tp.title)} ｜ 读者：${esc(tp.reader || "—")}</div>
          ${ei.genre ? `<div class="small muted" style="margin-top:3px">体裁：${esc(ei.genre)}${ei.creatorGoal ? ` ｜ 动机：${esc(ei.creatorGoal)}` : ""}</div>` : ""}
          ${ei.mustNotDrift
            ? `<div class="small" style="margin-top:4px"><b>不能写成：</b>${esc(ei.mustNotDrift)}</div>`
            : `<div class="small status-warn" style="margin-top:4px">这条选题没写「绝不能被改写成什么」，写的时候容易跑偏。<a href="#/topics">回选题台补一句</a></div>`}
        </div>` : ""}
        <label class="field">
          <span>发布标题 <span id="titleCount" class="hint"></span></span>
          <input type="text" id="dTitle" value="${esc(d.publishedTitle || "")}" placeholder="还没定就留空，右边可以生成">
        </label>
        <label class="field">
          <span>正文 <span id="charCount" class="hint"></span></span>
          <textarea id="dBody" rows="16" placeholder="正文写在这里">${esc(d.body || "")}</textarea>
        </label>
        <div id="selfCheckBox"></div>
        ${(d.captionPlan || []).length || (d.imagePlan || []).length ? `<details style="margin-bottom:10px">
          <summary class="hint" style="cursor:pointer">图文分工（写作时拆的两份清单，图别重复正文）</summary>
          <div style="margin-top:8px">
            <div class="small muted">只有正文能说的</div>
            <ul class="small">${(d.captionPlan || []).map((x) => `<li>${esc(x)}</li>`).join("") || "<li>—</li>"}</ul>
            <div class="small muted">只有图更有说服力的</div>
            <ul class="small">${(d.imagePlan || []).map((x) => `<li>${esc(x)}</li>`).join("") || "<li>—</li>"}</ul>
          </div>
        </details>` : ""}
        <div class="row">
          <button class="primary" id="saveDraft">保存</button>
          <button class="sm" id="genBody">生成正文</button>
          <button class="sm" id="markPublished">标记已发布</button>
          <span class="spacer"></span>
          <button class="ghost sm danger" id="delDraft">删除</button>
        </div>
      `;

      const bodyEl = root.querySelector("#dBody");
      const cnt = root.querySelector("#charCount");
      const tc = root.querySelector("#titleCount");
      function updCount() {
        const n = bodyEl.value.replace(/\s/g, "").length;
        cnt.innerHTML = n > 1000
          ? `<span class="status-err">${n}/1000 字（超了）</span>`
          : `${n}/1000 字`;
        const tn = root.querySelector("#dTitle").value.replace(/\s/g, "").length;
        tc.innerHTML = tn > 20 ? `<span class="status-err">${tn}/20 字</span>` : `${tn}/20 字`;
      }
      bodyEl.oninput = updCount;
      root.querySelector("#dTitle").oninput = updCount;
      updCount();
      renderSelfCheck(d.selfCheck);

      root.querySelector("#saveDraft").onclick = () => saveDraft(true);
      root.querySelector("#genBody").onclick = () => {
        const go = async () => {
          await runAI("draft_write", { draftId: d.id, topicId: d.topicId, material: "" }, {
            targetId: d.id,
            onApiResult: async (res) => {
              const text = res.text || "";
              if (!text.trim()) { toast("模型没返回正文", "err"); return; }
              const r = await api.post("/api/ai/ingest", {
                task: "draft_write", rawText: text, targetId: d.id,
              });
              if (r.ok) { toast("正文已写入，记得自己再读一遍", "ok"); loadDrafts(d.id); }
              else toast(r.message, "err");
            },
          });
        };
        if (bodyEl.value.trim()) {
          confirmDialog("正文里已经有内容，用 AI 重写会整段覆盖。继续？", go);
        } else {
          go();
        }
      };
      root.querySelector("#delDraft").onclick = () => confirmDialog("删除这篇稿件？", async () => {
        await api.del(`/api/drafts/${d.id}`);
        st.cur = null;
        loadDrafts();
      });
      root.querySelector("#markPublished").onclick = () => openPublish(d);
      // 改形态可以，但要提醒：体裁是上游定死的，形态改成教程/科普就很容易越过「不能写成什么」
      const FORM_BY_GENRE = {
        观点: "opinion_short", 观察: "life_record", 体验: "sensory_experience",
        证据测评: "evidence_review", 故事: "narrative_sequence",
        比较: "decision_comparison", 商业证明: "proof_led_commercial",
        操作指引: "operational_guidance",
      };
      root.querySelector("#dForm").onchange = () => {
        const want = FORM_BY_GENRE[ei.genre];
        const now = root.querySelector("#dForm").value;
        if (want && now && now !== want) {
          toast(`体裁是「${ei.genre}」，这条通常排成 ${want}；选 ${now} 容易越界，留心别写成别的体裁`, "");
        }
        renderSidePane();
      };
      root.querySelector("#dStatus").onchange = () => saveDraft(false);

      renderSidePane();
    }

    function renderSelfCheck(sc) {
      const box = root.querySelector("#selfCheckBox");
      if (!sc) { box.innerHTML = ""; return; }
      const bad = (sc.forbiddenPhrases || []).length;
      const ai = (sc.aiWords || []).length;
      const leak = (sc.referenceLeaks || []).length;
      if (!bad && !ai && !leak && !sc.overLimit) {
        box.innerHTML = `<div class="banner ok">自检通过：无违禁句式、无 AI 腔、没提参考作品，字数 ${sc.chars}</div>`;
        return;
      }
      box.innerHTML = `<div class="banner err">
        ${bad ? `违禁句式 ${bad} 处：${(sc.forbiddenPhrases || []).map((x) => esc(x.phrase)).join("、")}<br>` : ""}
        ${ai ? `AI 腔词：${(sc.aiWords || []).map(esc).join("、")}<br>` : ""}
        ${leak ? `提到了参考作品：${(sc.referenceLeaks || []).map(esc).join("、")}（读者没看过那条，删掉，直接讲这件事）<br>` : ""}
        ${sc.overLimit ? "正文超过 1000 字<br>" : ""}
      </div>`;
    }

    async function saveDraft(reload) {
      const d = st.cur;
      if (!d) return;
      const payload = {
        body: root.querySelector("#dBody").value,
        publishedTitle: root.querySelector("#dTitle").value.trim(),
        platform: root.querySelector("#dPlatform").value,
        contentForm: root.querySelector("#dForm").value,
        status: root.querySelector("#dStatus").value,
      };
      try {
        const r = await api.put(`/api/drafts/${d.id}`, payload);
        st.cur = r;
        renderSelfCheck(r.selfCheck);
        if (reload) toast("已保存", "ok");
        const idx = st.drafts.findIndex((x) => x.id === r.id);
        if (idx >= 0) st.drafts[idx] = { ...st.drafts[idx], ...r };
        root.querySelector("#draftList").querySelector(`[data-draft="${r.id}"] .small`)
          .textContent = `${window.WB.PLATFORM_NAMES[r.platform] || r.platform} · ${r.contentForm || "未定形态"} · ${r.bodyChars} 字`;
      } catch (e) {
        toast(e.message, "err");
      }
    }

    function openPublish(d) {
      const now = new Date();
      const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      window.WB.modal({
        title: "标记已发布",
        html: `
          <div class="hint" style="margin-bottom:10px">标记后会自动建一条复盘记录，方便以后录数据。</div>
          <label class="field"><span>发布时间</span><input type="datetime-local" id="pubAt" value="${local}"></label>
          <label class="field"><span>笔记链接（选填）</span><input type="url" id="pubUrl" placeholder="https://..."></label>
          <label class="field"><span>发布标题</span><input type="text" id="pubTitle" value="${esc(root.querySelector('#dTitle').value)}"></label>`,
        buttons: [
          { text: "取消" },
          {
            text: "确定", kind: "primary",
            onClick: async (body) => {
              const at = body.querySelector("#pubAt").value;
              const r = await api.post(`/api/drafts/${d.id}/publish`, {
                publishedAt: at ? new Date(at).toISOString() : new Date().toISOString(),
                url: body.querySelector("#pubUrl").value.trim(),
                title: body.querySelector("#pubTitle").value.trim(),
              });
              toast("已标记发布，复盘记录已建好", "ok");
              loadDrafts(r.draft.id);
            },
          },
        ],
      });
    }

    // ---------------- 右侧三 tab
    function renderSidePane() {
      const pane = root.querySelector("#sidePane");
      const d = st.cur;
      if (!d) { pane.innerHTML = `<div class="empty">先选一篇稿件</div>`; return; }

      if (st.tab === "plan") {
        const pp = d.pagePlan || {};
        const pages = pp.pages || [];
        pane.innerHTML = `
          <div class="row" style="margin-bottom:10px">
            <button class="primary sm" id="genPlan">生成页序</button>
            <span class="hint">${pp.countRange && pp.countRange.length ? `建议 ${pp.countRange[0]}-${pp.countRange[1]} 张` : ""}</span>
          </div>
          ${pages.length ? pages.map((p) => `
            <div class="list-item" style="cursor:default">
              <div class="t">第 ${p.index} 页 · ${esc(p.role || "")}</div>
              <div class="small muted">${esc(p.imageJob || "")} ｜ 文字档 ${esc(p.textLevel || "")}</div>
              ${p.text ? `<div class="small">${esc(p.text)}</div>` : ""}
              ${p.readerTask ? `<div class="small muted">任务：${esc(p.readerTask)}</div>` : ""}
            </div>`).join("")
            : `<div class="empty">还没有页序。点上面生成。</div>`}
        `;
        pane.querySelector("#genPlan").onclick = async () => {
          await runAI("page_plan", { draftId: d.id, contentForm: d.contentForm, material: "" }, {
            targetId: d.id,
            onApiResult: (res) => {
              if (res.json) {
                applyPages(res.json);
              } else toast("格式解析失败，请改用复制模式", "err");
            },
            onDone: () => loadDrafts(d.id),
          });
        };
        async function applyPages(json) {
          const r = await api.post("/api/ai/ingest", {
            task: "page_plan", rawText: JSON.stringify(json), targetId: d.id,
          });
          if (r.ok) { toast(`已生成 ${r.created} 页`, "ok"); loadDrafts(d.id); }
          else toast(r.message, "err");
        }
      }

      if (st.tab === "title") {
        const cands = d.titleCandidates || [];
        pane.innerHTML = `
          <div class="row" style="margin-bottom:10px">
            <button class="primary sm" id="genTitle">生成 10 个</button>
            <span class="hint">每条都要能追溯到公式编号</span>
          </div>
          ${cands.length ? cands.map((c, i) => `
            <div class="list-item" style="cursor:default${c.selected ? ";border-color:var(--accent)" : ""}">
              <div class="t">${esc(c.text)}</div>
              <div class="small muted">
                ${c.formulaId ? `#${esc(c.formulaId)} ` : ""}${esc(c.formulaName || "")}
                ｜ ${c.chars || (c.text || "").length} 字
                ${c.chars > 20 ? ' · <span class="status-err">超 20 字</span>' : ""}
              </div>
              ${c.reason ? `<div class="small muted">${esc(c.reason)}</div>` : ""}
              <div class="row" style="margin-top:6px">
                <button class="sm" data-use="${i}">设为发布标题</button>
              </div>
            </div>`).join("")
            : `<div class="empty">还没有候选标题</div>`}
        `;
        pane.querySelector("#genTitle").onclick = async () => {
          await runAI("title_gen", { draftId: d.id, topicId: d.topicId, count: 10 }, {
            targetId: d.id,
            onApiResult: (res) => {
              if (res.json) applyTitles(res.json);
              else toast("格式解析失败，请改用复制模式", "err");
            },
            onDone: () => loadDrafts(d.id),
          });
        };
        async function applyTitles(json) {
          const r = await api.post("/api/ai/ingest", {
            task: "title_gen", rawText: JSON.stringify(json), targetId: d.id,
          });
          if (r.ok) { toast(`加了 ${r.created} 个标题`, "ok"); loadDrafts(d.id); }
          else toast(r.message, "err");
        }
        pane.querySelectorAll("[data-use]").forEach((b) => {
          b.onclick = async () => {
            const c = cands[+b.dataset.use];
            root.querySelector("#dTitle").value = c.text;
            await api.put(`/api/drafts/${d.id}`, { publishedTitle: c.text });
            toast("已设为发布标题", "ok");
            renderSidePane();
          };
        });
      }

      if (st.tab === "cover") {
        const cv = d.cover || {};
        pane.innerHTML = `
          <div class="row" style="margin-bottom:10px">
            <button class="primary sm" id="genCover">生成封面方案</button>
          </div>
          ${cv.pattern || cv.coverTitle ? `
            <div class="kv"><span class="k">构图</span>${esc(cv.pattern || "—")}</div>
            <div class="kv"><span class="k">封面标题</span><b>${esc(cv.coverTitle || cv.title || "—")}</b></div>
            ${(cv.lines || []).length ? `<div class="ev">${(cv.lines || []).map(esc).join("<br>")}</div>` : ""}
            ${cv.visualNote ? `<div class="hint" style="margin-top:8px">${esc(cv.visualNote)}</div>` : ""}
            ${cv.prompt ? `<details style="margin-top:8px"><summary class="hint" style="cursor:pointer">生图提示词</summary>
              <textarea class="code" rows="6" readonly>${esc(cv.prompt)}</textarea></details>` : ""}
          ` : `<div class="empty">还没有封面方案</div>`}
        `;
        pane.querySelector("#genCover").onclick = async () => {
          await runAI("cover_plan", { draftId: d.id, material: "" }, {
            targetId: d.id,
            onApiResult: async (res) => {
              if (!res.json) { toast("格式解析失败", "err"); return; }
              const r = await api.post("/api/ai/ingest", {
                task: "cover_plan", rawText: JSON.stringify(res.json), targetId: d.id,
              });
              if (r.ok) { toast("封面方案已存", "ok"); loadDrafts(d.id); }
            },
            onDone: () => loadDrafts(d.id),
          });
        };
      }
    }

    root.querySelectorAll(".tabs button").forEach((b) => {
      b.onclick = () => {
        st.tab = b.dataset.tab;
        root.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
        renderSidePane();
      };
    });

    root.querySelector("#newFromTopic").onclick = () => pickAdoptedTopic();

    async function pickAdoptedTopic() {
      try {
        const res = await api.get("/api/topics?status=adopted");
        const items = res.items || [];
        if (!items.length) {
          toast("还没有已采纳的选题，先去选题台采纳一条", "err");
          location.hash = "#/topics";
          return;
        }
        drawer("选择选题", items.map((t) => `
          <div class="list-item" data-pick="${esc(t.id)}">
            <div class="t">${esc(t.title)}</div>
            <div class="small muted">${esc(t.reader || "")} ｜ ${esc(t.angle || "")}</div>
          </div>`).join(""), (body, wrap) => {
          body.querySelectorAll("[data-pick]").forEach((el) => {
            el.onclick = async () => {
              const d = await api.post(`/api/topics/${el.dataset.pick}/to-draft`, {});
              wrap.remove();
              await loadDrafts(d.id);
            };
          });
        });
      } catch (e) { toast(e.message, "err"); }
    }

    await loadDrafts(wantDraft || undefined);
    return () => {};
  }

  // ================================================================ 复盘台

  async function renderReview(root) {
    root.innerHTML = `
      <div class="page-head">
        <h1>复盘台</h1>
        <span class="sub">后台数据 → 对比基线 → 三层结论 → 沉淀经验</span>
        <span class="spacer"></span>
        <button class="sm" id="syncStats">同步后台数据</button>
        <button class="sm" id="refreshR">刷新</button>
      </div>
      <div class="card">
        <h2>账号数据 <span class="tag" id="accTag"></span></h2>
        <div id="accBody"><div class="empty">加载中…</div></div>
      </div>
      <div class="card">
        <h2>已发布内容 <span class="tag" id="rvTag"></span></h2>
        <div id="rvList"><div class="empty">加载中…</div></div>
      </div>
      <div class="card">
        <h2>经验库 <span class="tag" id="insTag"></span></h2>
        <div id="insList"><div class="empty">还没有沉淀经验</div></div>
      </div>
    `;

    let st = null;      // /api/stats/status
    let days = [];      // /api/stats/daily

    // ------------------------------------------------------ 账号数据卡
    async function loadStats() {
      try {
        st = await api.get("/api/stats/status");
        days = (await api.get("/api/stats/daily?days=30")).items || [];
        paintStats();
      } catch (e) {
        root.querySelector("#accBody").innerHTML = `<div class="banner err">${esc(e.message)}</div>`;
      }
    }

    function line(rows, field, color, name) {
      const pts = rows.filter((r) => typeof r[field] === "number");
      if (pts.length < 2) {
        return `<div class="hint">${esc(name)}：只有 ${pts.length} 个数据点，先多同步几次才有曲线</div>`;
      }
      const W = 300, H = 76, pad = 8;
      const vals = pts.map((r) => r[field]);
      const max = Math.max(...vals), min = Math.min(...vals);
      const span = max - min || 1;
      const x = (i) => pad + (i * (W - pad * 2)) / (pts.length - 1);
      const y = (v) => H - pad - ((v - min) / span) * (H - pad * 2);
      const d = pts.map((r, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(r[field]).toFixed(1)}`).join(" ");
      const last = vals[vals.length - 1];
      return `<div class="small muted" style="margin-bottom:2px">${esc(name)}
          <b style="color:${color};font-size:15px">${fmtNum(last)}</b>
          <span class="muted">最新 · ${pts.length} 天</span></div>
        <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img"
             aria-label="${esc(name)}趋势">
          <line x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}"
                stroke="var(--line-soft)" stroke-width="1"/>
          <path d="${d}" fill="none" stroke="${color}" stroke-width="1.8"
                stroke-linejoin="round" stroke-linecap="round"/>
          <circle cx="${x(pts.length - 1).toFixed(1)}" cy="${y(last).toFixed(1)}"
                  r="2.6" fill="${color}"/>
        </svg>`;
    }

    function paintStats() {
      const box = root.querySelector("#accBody");
      const tag = root.querySelector("#accTag");
      const j = (st && st.job) || {};
      const running = !!(st && st.running);

      tag.innerHTML = running
        ? `<span class="status-warn">同步中</span> ${esc(j.step || "")}`
        : (st && st.lastSyncAt
            ? `最近同步 ${esc(fmtDate(st.lastSyncAt))}`
            : "还没同步过");

      const acc = (st && st.account) || {};
      const sum = (st && st.summary) || {};
      const hostOpen = !!(st && st.browserRunning);

      let head = "";
      if (acc.name) {
        head = `<div class="row" style="gap:10px;flex-wrap:wrap;align-items:center">
          <span class="status-ok">● 已连接</span>
          <b>${esc(acc.name)}</b>
          <span class="small muted">粉丝 ${fmtNum(acc.fans_count)} · 获赞与收藏 ${fmtNum(acc.faved_count)}</span>
        </div>`;
      } else if (!hostOpen) {
        head = `<div class="banner">「我的号」的浏览器没开。先去采集台启动它并确认登录，再回来同步。</div>`;
      } else {
        head = `<div class="banner">浏览器开着，但还没读过数据。点右上角「同步后台数据」。</div>`;
      }

      const num = (v, unit) => (v == null ? "—" : fmtNum(v) + (unit || ""));
      const stats = sum.impressions != null ? `
        <div class="stat-row" style="margin-top:10px">
          <div class="stat"><span>曝光</span><b>${num(sum.impressions)}</b></div>
          <div class="stat"><span>观看</span><b>${num(sum.views)}</b></div>
          <div class="stat"><span>点赞</span><b>${num(sum.likes)}</b></div>
          <div class="stat"><span>收藏</span><b>${num(sum.collects)}</b></div>
          <div class="stat"><span>评论</span><b>${num(sum.comments)}</b></div>
          <div class="stat"><span>净涨粉</span><b>${num(sum.netFans)}</b></div>
          <div class="stat"><span>封面点击率</span><b>${num(sum.coverClickRate, "%")}</b></div>
          <div class="stat"><span>主页访客</span><b>${num(sum.homeViews)}</b></div>
        </div>` : "";

      const charts = days.length >= 2 ? `
        <div class="grid2" style="margin-top:14px;gap:16px">
          <div>${line(days, "impressions", "#0f766e", "每天曝光")}</div>
          <div>${line(days, "fansGained", "#b45309", "每天新增关注")}</div>
        </div>` : "";

      const foot = sum.beginDate
        ? `<div class="hint" style="margin-top:10px">
             后台统计区间 ${esc(sum.beginDate)} 至 ${esc(sum.endDate)}。
             曝光只有账号级口径，逐篇只有观看——平台逐篇接口不给曝光，不是我们漏采。
           </div>`
        : `<div class="hint" style="margin-top:10px">曝光、涨粉这些只有创作者后台才有，爬公开页拿不到。</div>`;

      box.innerHTML = head + stats + charts + foot;
    }

    async function syncStats(btn) {
      btn.disabled = true;
      const old = btn.textContent;
      btn.textContent = "同步中…";
      try {
        const r = await api.post("/api/stats/sync", {});
        if (!r.ok) { toast(r.message || "同步失败", "warn"); return; }
        toast("开始同步，大约 30–60 秒", "ok");
        // 轮询进度
        for (let i = 0; i < 60; i++) {
          await new Promise((res) => setTimeout(res, 2000));
          st = await api.get("/api/stats/status");
          paintStats();
          if (!st.running) break;
        }
        const j = (st && st.job) || {};
        if (j.status === "failed" || j.status === "interrupted") {
          toast(j.error || "同步中断了，重试一次", "err");
        } else if (j.status !== "done") {
          // 还在跑（轮询到上限了），先别对账，避免拿到半截数据
          toast("同步还没结束，稍后再看一眼进度", "warn");
        } else {
          const done = await api.post("/api/reviews/sync-backend", {});
          if (done.ok) {
            if (!done.created && !done.updated) {
              toast(`后台数据没变化，${done.skipped} 条已是最新`, "ok");
            } else {
              toast(`同步完成：新建 ${done.created} 条，更新 ${done.updated} 条，`
                    + `未变 ${done.skipped} 条`, "ok");
            }
          } else {
            toast(done.message || "对账失败", "warn");
          }
          await loadReviews();
          await loadStats();
        }
      } catch (e) {
        toast(e.message, "err");
      } finally {
        btn.disabled = false;
        btn.textContent = old;
      }
    }

    root.querySelector("#syncStats").onclick = (e) => syncStats(e.currentTarget);

    async function loadReviews() {
      const box = root.querySelector("#rvList");
      try {
        const res = await api.get("/api/reviews");
        const items = (res.items || []).slice().sort(
          (a, b) => String(b.publishedAt || "").localeCompare(String(a.publishedAt || "")));
        root.querySelector("#rvTag").textContent = `${items.length} 条`;
        if (!items.length) {
          box.innerHTML = `<div class="empty">还没有。去创作台点「标记已发布」，或先同步后台数据。</div>`;
          return;
        }
        box.innerHTML = `<div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>标题</th><th>发布</th><th class="num">观看</th>
          <th class="num">点赞</th><th class="num">收藏</th><th class="num">评论</th>
          <th class="num">点赞倍数</th><th>判定</th><th>操作</th></tr></thead>
          <tbody>${items.map(rvRow).join("")}</tbody></table></div>`;
        box.querySelectorAll("[data-rv]").forEach((b) => {
          b.onclick = () => openReview(b.dataset.rv);
        });
      } catch (e) {
        box.innerHTML = `<div class="banner err">${esc(e.message)}</div>`;
      }
    }

    function cell(r, day) {
      const m = (r.metrics || []).find((x) => x.day === day);
      return m && m.like != null ? fmtNum(m.like) : "—";
    }

    function rvRow(r) {
      const b = r.backend || {};
      const c = r.compare || {};
      const verdict = {
        above: '<span class="chip on">超出</span>',
        normal: '<span class="chip">正常</span>',
        below: '<span class="chip debate">低于</span>',
      }[c.verdict] || '<span class="chip">待录</span>';
      const n = (v) => (v == null ? "—" : fmtNum(v));
      const likes = b.likes != null ? n(b.likes) : cell(r, 7);
      return `<tr>
        <td class="t">${esc(r.title || "(未命名)")}</td>
        <td class="small nowrap">${esc(fmtDate(r.publishedAt))}</td>
        <td class="num">${n(b.views)}</td>
        <td class="num">${likes}</td>
        <td class="num">${n(b.collects)}</td>
        <td class="num">${n(b.comments)}</td>
        <td class="num">${c.likeMultiple != null ? c.likeMultiple + "×" : "—"}</td>
        <td>${verdict}</td>
        <td><button class="sm" data-rv="${esc(r.id)}">复盘</button></td>
      </tr>`;
    }

    async function openReview(id) {
      const res = await api.get("/api/reviews");
      const r = (res.items || []).find((x) => x.id === id);
      if (!r) return;

      const rows = [0, 1, 3, 7].map((d) => {
        const m = (r.metrics || []).find((x) => x.day === d) || {};
        return `<tr data-day="${d}">
          <td>D${d}</td>
          <td><input type="number" data-f="like" value="${m.like ?? ""}" style="width:80px"></td>
          <td><input type="number" data-f="collect" value="${m.collect ?? ""}" style="width:80px"></td>
          <td><input type="number" data-f="comment" value="${m.comment ?? ""}" style="width:70px"></td>
          <td><input type="number" data-f="share" value="${m.share ?? ""}" style="width:70px"></td>
          <td><input type="number" data-f="views" value="${m.views ?? ""}" style="width:80px"></td>
        </tr>`;
      }).join("");

      const cmp = r.compare || {};
      const bd = r.backend || {};
      const nb = (v) => (v == null ? "—" : fmtNum(v));
      const backendCard = (bd.likes != null || bd.views != null) ? `
        <div class="card">
          <h2>创作者后台 <span class="tag">平台直接给的数，不是估算</span></h2>
          <div class="stat-row">
            <div class="stat"><span>观看</span><b>${nb(bd.views)}</b></div>
            <div class="stat"><span>点赞</span><b>${nb(bd.likes)}</b></div>
            <div class="stat"><span>收藏</span><b>${nb(bd.collects)}</b></div>
            <div class="stat"><span>评论</span><b>${nb(bd.comments)}</b></div>
            <div class="stat"><span>分享</span><b>${nb(bd.shares)}</b></div>
          </div>
          <div class="hint" style="margin-top:8px">
            ${bd.daysSincePublish != null ? `发布后第 ${bd.daysSincePublish} 天 · ` : ""}
            观测于 ${esc(fmtDate(bd.at))}。逐篇接口不给曝光，曝光只在账号级。
          </div>
        </div>` : "";
      drawer(`复盘 · ${r.title || ""}`, `
        ${backendCard}
        <div class="card">
          <h2>数据录入 <span class="tag">留空=未知，不要填 0</span></h2>
          <div class="tbl-wrap"><table class="tbl">
            <thead><tr><th>时间</th><th>点赞</th><th>收藏</th><th>评论</th><th>分享</th><th>播放</th></tr></thead>
            <tbody>${rows}</tbody>
          </table></div>
          <div class="row" style="margin-top:10px">
            ${[0, 1, 3, 7].map((d) => `<button class="sm" data-save="${d}">存 D${d}</button>`).join("")}
          </div>
          ${cmp.likeMultiple != null ? `<div class="banner ${cmp.verdict === "above" ? "ok" : ""}" style="margin-top:10px">
            点赞是账号中位数 <b>${cmp.likeMultiple}×</b>（${({ above: "超出预期", normal: "正常", below: "低于预期" })[cmp.verdict]}）
          </div>` : `<div class="banner" style="margin-top:10px">还没有基线。同账号多录几条后才有中位数。</div>`}
        </div>

        <div class="card">
          <h2>三层结论</h2>
          ${r.conclusion ? `
            <div class="kv"><span class="k">一句话</span><b>${esc(r.conclusion.oneLine || "")}</b></div>
            <div class="kv"><span class="k">这一条</span>${esc(r.conclusion.thisPost || "—")}</div>
            <div class="kv"><span class="k">模板层</span>${esc(r.conclusion.template || "—")}</div>
            <div class="kv"><span class="k">账号层</span>${esc(r.conclusion.account || "—")}</div>
          ` : `<div class="empty">还没做复盘</div>`}
          <div class="row" style="margin-top:10px">
            <button class="primary sm" id="aiReview">让 AI 复盘</button>
          </div>
        </div>

        <div class="card">
          <h2>建议沉淀的经验 <span class="tag">最多 3 条</span></h2>
          ${(r.suggestions || []).length ? (r.suggestions || []).map((s, i) => `
            <div class="list-item" style="cursor:default">
              <div class="t">${esc(s.text)}</div>
              <div class="small muted">层面 ${esc(s.layer)} ｜ 依据 ${esc(s.basis || "—")} ｜ ${esc(s.status || "pending")}</div>
              ${s.status !== "accepted" ? `<button class="sm" data-acc="${i}" style="margin-top:6px">存入经验库</button>` : '<span class="chip on">已入库</span>'}
            </div>`).join("") : `<div class="empty">复盘后会出现建议</div>`}
        </div>
      `, (body, wrap) => {
        body.querySelectorAll("[data-save]").forEach((b) => {
          b.onclick = async () => {
            const d = +b.dataset.save;
            const tr = body.querySelector(`tr[data-day="${d}"]`);
            const get = (f) => {
              const v = tr.querySelector(`[data-f="${f}"]`).value.trim();
              return v === "" ? null : v;
            };
            const r2 = await api.post(`/api/reviews/${id}/metrics`, {
              day: d, like: get("like"), collect: get("collect"),
              comment: get("comment"), share: get("share"), views: get("views"),
            });
            toast(`D${d} 已保存`, "ok");
            wrap.remove();
            openReview(id);
            loadReviews();
          };
        });
        body.querySelector("#aiReview").onclick = async () => {
          await runAI("review", { reviewId: id }, {
            targetId: id,
            onApiResult: async (res) => {
              if (!res.json) { toast("解析失败，请用复制模式", "err"); return; }
              const r2 = await api.post("/api/ai/ingest", {
                task: "review", rawText: JSON.stringify(res.json), targetId: id,
              });
              if (r2.ok) { toast("复盘已生成", "ok"); wrap.remove(); openReview(id); loadReviews(); }
            },
            onDone: () => { wrap.remove(); openReview(id); loadReviews(); },
          });
        };
        body.querySelectorAll("[data-acc]").forEach((b) => {
          b.onclick = async () => {
            await api.post(`/api/reviews/${id}/accept-insight`, { index: +b.dataset.acc });
            toast("已存入经验库", "ok");
            wrap.remove();
            openReview(id);
            loadInsights();
          };
        });
      });
    }

    async function loadInsights() {
      const box = root.querySelector("#insList");
      try {
        const res = await api.get("/api/insights");
        const items = res.items || [];
        root.querySelector("#insTag").textContent = `${items.length} 条`;
        if (!items.length) { box.innerHTML = `<div class="empty">还没有沉淀经验</div>`; return; }
        const LN = { account: "账号", template: "模板", topic: "选题", expression: "表达" };
        box.innerHTML = items.map((i) => `
          <div class="list-item" style="cursor:default">
            <div class="t">${esc(i.text)}</div>
            <div class="small muted">${esc(LN[i.layer] || i.layer)} ｜ ${i.verified ? "已验证" : "待验证"} ｜ 依据 ${(i.basis || []).length} 条</div>
          </div>`).join("");
      } catch (e) {}
    }

    root.querySelector("#refreshR").onclick = () => { loadReviews(); loadInsights(); loadStats(); };
    await loadStats();
    await loadReviews();
    await loadInsights();
    return () => {};
  }

  // ================================================================ 评论洞察

  async function renderComments(root) {
    let curKey = "";

    root.innerHTML = `
      <div class="page-head">
        <h1>评论洞察</h1>
        <span class="sub">从评论里的真实诉求，反推一个选题</span>
      </div>
      <div class="card">
        <h2>选一条笔记</h2>
        <div class="row">
          <input type="text" id="cQ" class="grow" placeholder="搜标题找笔记，留空列出最近的">
          <button class="sm" id="cSearch">搜索</button>
          <input type="text" id="cKey" class="grow" placeholder="或直接粘贴 key（xhs:xxxx）">
          <button class="sm" id="cLoad">载入</button>
        </div>
        <div id="cPickList" style="margin-top:10px"></div>
      </div>
      <div class="grid2">
        <div class="card">
          <h2>评论 <span class="tag" id="cTag"></span></h2>
          <div id="cList"><div class="empty">先选一条笔记</div></div>
        </div>
        <div class="card">
          <h2>洞察与选题</h2>
          <div id="cInsight"><div class="empty">载入评论后可以生成</div></div>
        </div>
      </div>
    `;

    async function search() {
      const q = root.querySelector("#cQ").value.trim();
      const box = root.querySelector("#cPickList");
      try {
        const res = await api.get(`/api/contents?q=${encodeURIComponent(q)}&pageSize=20`);
        const items = res.items || [];
        box.innerHTML = items.length
          ? items.map((it) => `<div class="list-item" data-key="${esc(it.key)}">
              <div class="t">${esc(it.title || "(无标题)")}</div>
              <div class="small muted">${esc(window.WB.PLATFORM_NAMES[it.platform])} ｜ ${fmtNum(it.metrics.like)} 赞 ｜ ${esc(it.keyword || "")}</div>
            </div>`).join("")
          : `<div class="empty">没找到。换个词，或先去采集台采一批。</div>`;
        box.querySelectorAll("[data-key]").forEach((el) => {
          el.onclick = () => loadComments(el.dataset.key);
        });
      } catch (e) {
        box.innerHTML = `<div class="banner err">${esc(e.message)}</div>`;
      }
    }

    async function loadComments(key) {
      curKey = key;
      const listBox = root.querySelector("#cList");
      const insBox = root.querySelector("#cInsight");
      try {
        const [cm, rec] = await Promise.all([
          api.get(`/api/contents/${key.split(":")[0]}/${key.split(":")[1]}/comments?limit=80`),
          api.get(`/api/contents/${key.split(":")[0]}/${key.split(":")[1]}`).catch(() => null),
        ]);
        root.querySelector("#cKey").value = key;
        root.querySelector("#cTag").textContent = `${(cm.items || []).length} 条`;

        if (!(cm.items || []).length) {
          listBox.innerHTML = `<div class="empty">这条没有评论数据。采集时勾选"采集评论"才会存。</div>`;
          return;
        }
        const G = { ask: "追问", disagree: "反驳", add: "补充", emotion: "情绪", action: "行动", author: "作者" };
        const g = cm.groups || {};
        insBox.innerHTML = `
          <div class="hint" style="margin-bottom:10px">${Object.entries(g).map(([k, v]) => `${G[k] || k} <b>${v}</b>`).join(" · ")}</div>
          <button class="primary sm" id="genInsight">生成洞察与选题</button>
          <div id="insightResult" style="margin-top:12px"></div>
        `;

        listBox.innerHTML = (cm.items || []).map((c) => `
          <div class="ev" style="margin-bottom:6px">
            <span class="muted small">${fmtNum(c.like)} 赞${c.parentId ? " · 回复" : ""}</span>
            ${esc(c.content)}
          </div>`).join("");

        insBox.querySelector("#genInsight").onclick = async () => {
          await runAI("comment_insight", { contentKey: key }, {
            onApiResult: async (res) => {
              if (!res.json) { toast("解析失败，请用复制模式", "err"); return; }
              const r = await api.post("/api/ai/ingest", {
                task: "comment_insight", rawText: JSON.stringify(res.json),
              });
              if (r.ok) {
                toast("已生成选题卡", "ok");
                insBox.querySelector("#insightResult").innerHTML =
                  `<div class="topic"><div class="h"><b>${esc(r.items[0].title)}</b></div>
                   <div class="kv"><span class="k">读者</span>${esc(r.items[0].reader)}</div>
                   <div class="kv"><span class="k">质检</span>${r.items[0].qualityCheck.passCount}/${r.items[0].qualityCheck.total}</div></div>
                   <a href="#/topics">去选题台看 →</a>`;
              } else toast(r.message, "err");
            },
            onDone: (res) => {
              if (res && res.items) {
                insBox.querySelector("#insightResult").innerHTML =
                  `<div class="banner ok">已存入 ${res.created} 张选题卡。<a href="#/topics">去选题台 →</a></div>`;
              }
            },
          });
        };
        if (rec && rec.title) {
          insBox.insertAdjacentHTML("afterbegin",
            `<div class="kv" style="margin-bottom:8px"><span class="k">原笔记</span>${esc(rec.title)}</div>`);
        }
      } catch (e) {
        listBox.innerHTML = `<div class="banner err">${esc(e.message)}</div>`;
      }
    }

    root.querySelector("#cSearch").onclick = search;
    root.querySelector("#cLoad").onclick = () => {
      const k = root.querySelector("#cKey").value.trim();
      if (k.includes(":")) loadComments(k);
      else toast("key 形如 xhs:6541399f...", "err");
    };
    await search();
    return () => {};
  }

  // ================================================================ 设置

  async function renderSettings(root) {
    root.innerHTML = `<div class="page-head"><h1>设置</h1><span class="sub">改完自动保存</span></div>
      <div id="setBody"><div class="empty">加载中…</div></div>`;

    let s;
    try { s = await api.get("/api/settings"); }
    catch (e) { root.querySelector("#setBody").innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }

    function modelOptions(a) {
      const list = a.modelPresets || [];
      const cur = a.model || "";
      const known = list.some((m) => m.id === cur);
      let h = list.map((m) =>
        `<option value="${esc(m.id)}"${m.id === cur ? " selected" : ""}>${esc(m.label)}</option>`
      ).join("");
      if (!list.length) h = `<option value="${esc(cur)}" selected>${esc(cur)}</option>`;
      h += `<option value="__custom"${known ? "" : " selected"}>自定义…</option>`;
      return h;
    }

    root.querySelector("#setBody").innerHTML = `
      <div class="card">
        <h2>AI 模式</h2>
        <div class="row" style="margin-bottom:12px">
          <button class="sm ${s.ai.mode === "prompt" ? "primary" : ""}" data-mode="prompt">复制提示词</button>
          <button class="sm ${s.ai.mode === "api" ? "primary" : ""}" data-mode="api">一键直出</button>
          <span class="hint" id="modeNote"></span>
        </div>
        <div class="grid2">
          <label class="field"><span>Base URL（OpenAI 兼容）</span>
            <input type="text" id="aiBase" value="${esc(s.ai.baseUrl)}" placeholder="https://api.deepseek.com/v1"></label>
          <label class="field"><span>模型</span><select id="aiModel">${modelOptions(s.ai)}</select></label>
          <label class="field" id="modelCustomWrap" style="display:none"><span>自定义模型名</span>
            <input type="text" id="aiModelCustom" value="${esc(s.ai.modelKnown ? "" : s.ai.model)}" placeholder="服务商文档里的 model 值"></label>
          <label class="field"><span>API Key ${s.ai.apiKeySet ? `（当前 ${esc(s.ai.apiKeyMasked)}）` : "（未配置）"}</span>
            <input type="password" id="aiKey" placeholder="${s.ai.apiKeySet ? "留空则不修改" : "sk-..."}"></label>
          <label class="field"><span>温度</span>
            <input type="number" id="aiTemp" step="0.1" min="0" max="1.5" value="${s.ai.temperature}"></label>
        </div>
        <div class="hint" id="modelNote" style="margin:-2px 0 10px"></div>
        <div class="hint" style="margin:-4px 0 10px">
          换服务商只改地址和模型名即可，任何 OpenAI 兼容接口都能用。输出长度由系统按任务自动给，不用管。
        </div>
        <div class="row">
          <label class="check"><input type="checkbox" id="aiJson" ${s.ai.jsonMode ? "checked" : ""}> 要求返回 JSON</label>
          <label class="check"><input type="checkbox" id="aiThinking" ${s.ai.thinking ? "checked" : ""}> 思考模式</label>
          <button class="sm" id="saveAi">保存 AI 设置</button>
          <button class="sm" id="testAi">测试连接</button>
          <span class="hint" id="aiTest"></span>
        </div>
      </div>

      <div class="card">
        <h2>采集设置</h2>
        <div class="grid2">
          <label class="field"><span>Chrome 路径</span><input type="text" id="crChrome" value="${esc(s.crawler.chromePath)}"></label>
          <label class="field"><span>CDP 端口</span><input type="number" id="crPort" value="${s.crawler.cdpPort}"></label>
          <label class="field"><span>默认条数</span><input type="number" id="crNotes" value="${s.crawler.maxNotes}"></label>
          <label class="field"><span>单任务超时（分钟）</span><input type="number" id="crTimeout" value="${s.crawler.maxJobMinutes}"></label>
          <label class="field"><span>组间冷却（秒）</span><input type="number" id="crGuardCool" value="${s.crawler.guardCooldownSec}"></label>
          <label class="field"><span>搜索日上限（条）</span><input type="number" id="crGuardDaily" value="${s.crawler.guardDailySearch}"></label>
          <label class="field"><span>连撞几次风控刹车</span><input type="number" id="crGuardStrikes" value="${s.crawler.guardRiskStrikes}"></label>
          <label class="field"><span>刹车时长（分钟）</span><input type="number" id="crGuardMin" value="${s.crawler.guardRiskCooldownMin}"></label>
        </div>
        <div class="hint" style="margin:6px 0 10px">
          实测：461 是<b>密度</b>触发——上一组结束后 3 秒就起下一组必撞，当天连采 318 条本身没被拦。
          所以冷却比限额更关键。冷却设 0 = 关掉这一项；日上限填 0 = 不限。
          按 ID 补采（detail）不受日上限约束。
        </div>
        <div class="row">
          <button class="sm" id="saveCr">保存采集设置</button>
          <button class="sm" id="clrProfile">清空登录态</button>
          <span class="hint">清空后下次采集要重新扫码</span>
        </div>
      </div>

      <div class="card">
        <h2>互动结构阈值</h2>
        <div class="grid3">
          <label class="field"><span>收藏型：收藏率 ≥</span><input type="number" id="thC" step="0.05" value="${s.thresholds.collect}"></label>
          <label class="field"><span>争议型：评论率 ≥</span><input type="number" id="thM" step="0.01" value="${s.thresholds.comment}"></label>
          <label class="field"><span>共鸣型：分享率 ≥</span><input type="number" id="thS" step="0.01" value="${s.thresholds.share}"></label>
        </div>
        <div class="banner">这几个数是我给的先验值，不是 Beav 内置的。跑完第一批真实数据后要用中位数重新校准。</div>
        <button class="sm" id="saveTh">保存阈值</button>
      </div>

      <div class="card">
        <h2>数据</h2>
        <div class="row">
          <button class="sm" id="openData">打开数据目录</button>
          <button class="sm" id="openLogs">打开日志目录</button>
          <button class="sm" id="openPrompts">打开提示词目录</button>
          <button class="sm" id="cleanOrphan">清理孤儿进程</button>
          <span class="spacer"></span>
          <button class="danger sm" id="shutdown">关闭工作台</button>
        </div>
        <div class="hint" style="margin-top:10px">
          AI 调用统计：<span id="usageBox">读取中…</span>
        </div>
      </div>
    `;

    function noteMode() {
      root.querySelector("#modeNote").innerHTML = s.ai.mode === "api"
        ? "点任何生成按钮都直接调模型。失败会自动回落到复制模式。"
        : "生成按钮会给你一段提示词，复制到任意 AI 跑完再贴回来。";
    }
    noteMode();

    function paintModelNote() {
      const sel = root.querySelector("#aiModel");
      const wrap = root.querySelector("#modelCustomWrap");
      const note = root.querySelector("#modelNote");
      if (sel.value === "__custom") {
        wrap.style.display = "";
        note.textContent = "填服务商文档里给出的 model 值。";
        return;
      }
      wrap.style.display = "none";
      const m = (s.ai.modelPresets || []).find((x) => x.id === sel.value);
      note.textContent = m ? m.note : "";
    }
    root.querySelector("#aiModel").onchange = paintModelNote;
    paintModelNote();

    root.querySelectorAll("[data-mode]").forEach((b) => {
      b.onclick = async () => {
        s = await api.put("/api/settings", { ai: { mode: b.dataset.mode } });
        root.querySelectorAll("[data-mode]").forEach((x) =>
          x.classList.toggle("primary", x.dataset.mode === s.ai.mode));
        noteMode();
        toast("已切换模式", "ok");
      };
    });

    root.querySelector("#saveAi").onclick = async () => {
      const key = root.querySelector("#aiKey").value.trim();
      if (key) {
        await api.put("/api/settings/secret", { apiKey: key });
        root.querySelector("#aiKey").value = "";
      }
      let model = root.querySelector("#aiModel").value;
      if (model === "__custom") {
        model = root.querySelector("#aiModelCustom").value.trim() || s.ai.model;
      }
      s = await api.put("/api/settings", {
        ai: {
          baseUrl: root.querySelector("#aiBase").value.trim(),
          model,
          temperature: +root.querySelector("#aiTemp").value,
          thinking: root.querySelector("#aiThinking").checked,
          jsonMode: root.querySelector("#aiJson").checked,
        },
      });
      toast("AI 设置已保存", "ok");
      renderSettings(root);
    };

    root.querySelector("#testAi").onclick = async () => {
      const box = root.querySelector("#aiTest");
      box.textContent = "测试中…";
      const r = await api.post("/api/settings/test-ai");
      box.innerHTML = r.ok
        ? `<span class="status-ok">通了（${r.latencyMs}ms，${esc(r.model)}）</span>`
        : `<span class="status-err">失败：${esc(r.message)}</span>`;
    };

    root.querySelector("#saveCr").onclick = async () => {
      await api.put("/api/settings", { crawler: {
        chromePath: root.querySelector("#crChrome").value.trim(),
        cdpPort: +root.querySelector("#crPort").value,
        maxNotes: +root.querySelector("#crNotes").value,
        maxJobMinutes: +root.querySelector("#crTimeout").value,
        guardCooldownSec: +root.querySelector("#crGuardCool").value,
        guardDailySearch: +root.querySelector("#crGuardDaily").value,
        guardRiskStrikes: +root.querySelector("#crGuardStrikes").value,
        guardRiskCooldownMin: +root.querySelector("#crGuardMin").value,
      }});
      toast("采集设置已保存", "ok");
    };

    root.querySelector("#clrProfile").onclick = () => confirmDialog(
      "清空专用浏览器的登录态（browser_data 目录）？下次要重新扫码。", async () => {
        await api.post("/api/crawl/browser/close");
        toast("已关闭浏览器。请手动删除 browser_data 下的 cdp_ 目录", "warn");
      });

    root.querySelector("#saveTh").onclick = async () => {
      await api.put("/api/settings", { thresholds: {
        collect: +root.querySelector("#thC").value,
        comment: +root.querySelector("#thM").value,
        share: +root.querySelector("#thS").value,
      }});
      toast("阈值已保存（对之后导入的数据生效）", "ok");
    };

    ["data", "logs", "prompts"].forEach((w) => {
      const btn = root.querySelector("#open" + w[0].toUpperCase() + w.slice(1));
      if (btn) btn.onclick = () => api.post("/api/open-folder", { which: w });
    });
    root.querySelector("#cleanOrphan").onclick = async () => {
      const r = await api.post("/api/crawl/cleanup-orphans").catch(() => null);
      toast(r ? `清理完成（${r.fixed} 条）` : "服务重启时会自动清理", "ok");
    };
    root.querySelector("#shutdown").onclick = () => confirmDialog("关闭工作台服务？", async () => {
      await api.post("/api/shutdown");
      document.body.innerHTML = '<div style="padding:40px;font-family:sans-serif">工作台已关闭，可以关掉这个标签页了。</div>';
    });

    // 账号档案已移到「档案台」（拆解自己的号，产出运营规划与写法规则）

    (async () => {
      try {
        const u = await api.get("/api/ai/usage");
        root.querySelector("#usageBox").textContent = `${u.calls} 次调用，${u.tokens} tokens，失败 ${u.failures} 次`;
      } catch (e) {}
    })();

    return () => {};
  }

  window.VIEWS = window.VIEWS || {};
  window.VIEWS.create = renderCreate;
  window.VIEWS.review = renderReview;
  window.VIEWS.comments = renderComments;
  window.VIEWS.settings = renderSettings;
})();
