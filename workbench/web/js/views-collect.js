/* 视图：采集台 + 选题台 */
(function () {
  "use strict";
  const { api, esc, fmtNum, fmtPct, fmtDate, fmtSec, relTime, toast, drawer, runAI, confirmDialog } = window.WB;

  // ================================================================ 采集台

  const collectState = {
    es: null,
    jobId: null,
    batches: [],
    browser: null,
    keywords: [],
    sortType: "general",
    tab: "log",
  };

  const SORT_LABELS = {
    general: "综合",
    popularity_descending: "热门",
    time_descending: "最新",
  };

  function closeStream() {
    if (collectState.es) {
      collectState.es.close();
      collectState.es = null;
    }
  }

  async function renderCollect(root, params) {
    closeStream();
    root.innerHTML = `
      <div class="page-head">
        <h1>采集台</h1>
        <span class="sub">MediaCrawler · 关键词批量扫盘</span>
        <span class="spacer"></span>
        <button id="openRaw" class="sm">打开原始数据目录</button>
      </div>
      <div class="card" id="browserBar">检查专用浏览器…</div>
      <div class="card" id="guardBar" style="display:none"></div>
      <div class="grid2">
        <div class="card">
          <h2>发起采集</h2>
          <div class="row" style="margin-bottom:10px">
            <label class="field" style="margin:0;min-width:150px">
              <span>平台</span>
              <select id="platform">
                <option value="xhs">小红书</option>
                <option value="dy">抖音</option>
                <option value="bili">B站</option>
              </select>
            </label>
            <label class="field" style="margin:0;min-width:90px">
              <span>每个关键词条数</span>
              <select id="maxNotes">
                <option value="10">10</option>
                <option value="20" selected>20</option>
                <option value="30">30</option>
                <option value="50">50</option>
              </select>
            </label>
            <label class="check" style="margin-bottom:6px">
              <input type="checkbox" id="withComments" checked> 采集评论
            </label>
          </div>
          <div class="row" style="margin-bottom:10px;align-items:center">
            <span class="small muted nowrap">排序（仅小红书）</span>
            <div class="btn-group" id="sortSeg">
              <button type="button" data-sort="general">综合</button>
              <button type="button" data-sort="popularity_descending">热门</button>
              <button type="button" data-sort="time_descending">最新</button>
            </div>
            <span class="hint" id="sortHint" style="flex:1 1 200px"></span>
          </div>
          <label class="field">
            <span>关键词（回车添加，或直接粘贴多个用逗号分隔）</span>
            <input type="text" id="kwInput" placeholder="MBTI, INTJ, i人">
          </label>
          <div class="pill-list" id="kwList" style="margin-bottom:12px"></div>
          <div class="row">
            <button class="primary" id="startBtn">开始采集</button>
            <button id="stopBtn" disabled>停止</button>
            <span class="hint" id="startHint">建议一次不超过 50 条</span>
          </div>
        </div>

        <div class="card">
          <h2>运行状态 <span class="tag" id="jobTag"></span></h2>
          <div class="stat-row" id="jobStats">
            <div class="stat"><b>空闲</b><span>当前任务</span></div>
          </div>
          <div class="progress"><i id="progBar"></i></div>
          <div class="hint" id="jobHint">还没有运行中的任务。</div>
        </div>
      </div>

      <div class="card">
        <h2>日志
          <span class="spacer"></span>
          <label class="check small"><input type="checkbox" id="onlyErr"> 只看错误</label>
          <button class="ghost sm" id="clearLog">清屏</button>
          <button class="ghost sm" id="copyLog">复制</button>
        </h2>
        <div class="logbox" id="logBox"></div>
      </div>

      <div class="card">
        <h2>采集批次 <span class="tag" id="batchTag"></span></h2>
        <div id="batchTable"><div class="empty">还没有批次。先启动专用浏览器并登录，再发起采集。</div></div>
      </div>
    `;

    // ------------------------------ 关键词 chip
    function drawKws() {
      const box = root.querySelector("#kwList");
      box.innerHTML = collectState.keywords
        .map((k, i) => `<span class="chip on">${esc(k)} <a href="#" data-i="${i}" style="text-decoration:none;color:inherit">×</a></span>`)
        .join("");
      box.querySelectorAll("a").forEach((a) => {
        a.onclick = (e) => {
          e.preventDefault();
          collectState.keywords.splice(+a.dataset.i, 1);
          drawKws();
        };
      });
    }

    const kwInput = root.querySelector("#kwInput");
    kwInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === ",") {
        e.preventDefault();
        addKws(kwInput.value);
        kwInput.value = "";
      }
    });
    kwInput.addEventListener("blur", () => {
      if (kwInput.value.trim()) { addKws(kwInput.value); kwInput.value = ""; }
    });
    function addKws(raw) {
      String(raw).split(/[,，\s]+/).filter(Boolean).forEach((k) => {
        if (!collectState.keywords.includes(k) && collectState.keywords.length < 10) {
          collectState.keywords.push(k);
        }
      });
      drawKws();
    }
    collectState.keywords = ["MBTI", "INTJ", "i人"];
    drawKws();

    // ------------------------------ 排序方式（仅小红书）
    const SORT_HINTS = {
      general: "平台认为当下最相关，变化最快，适合每天跑",
      popularity_descending: "历史累积高赞，重复率高，建议每周校准一次",
      time_descending: "按发布时间倒序，零赞偏多，适合找刚起的苗头",
    };
    function drawSort() {
      const seg = root.querySelector("#sortSeg");
      const isXhs = root.querySelector("#platform").value === "xhs";
      seg.querySelectorAll("button").forEach((b) => {
        b.classList.toggle("on", b.dataset.sort === collectState.sortType);
        b.disabled = !isXhs;
      });
      root.querySelector("#sortHint").textContent = isXhs
        ? (SORT_HINTS[collectState.sortType] || "")
        : "排序只对小红书生效，其他平台按默认排序";
    }
    root.querySelector("#sortSeg").querySelectorAll("button").forEach((b) => {
      b.onclick = () => { collectState.sortType = b.dataset.sort; drawSort(); };
    });
    root.querySelector("#platform").onchange = drawSort;
    drawSort();

    // ------------------------------ 浏览器状态
    async function refreshBrowser() {
      const bar = root.querySelector("#browserBar");
      try {
        const st = await api.get("/api/crawl/browser/status?role=crawl");
        collectState.browser = st;
        const otherOn = st.other && st.other.running;
        bar.innerHTML = `
          <div class="row">
            <div class="stat"><b class="${st.running ? "status-ok" : "status-warn"}">${st.running ? "已启动" : "未启动"}</b><span>采集号浏览器 ${st.port}</span></div>
            <span class="spacer"></span>
            <button class="sm" id="launchBtn">${st.running ? "打开登录页" : "启动采集号浏览器"}</button>
            <button class="sm" id="closeBrowserBtn" ${st.running ? "" : "disabled"}>关闭浏览器</button>
          </div>
          <div class="hint" style="margin-top:8px">
            这里登录的是<b>采集号（小号）</b>，专门用来爬别人的内容，风控风险落在它身上。
            你自己的大号只在复盘台点「同步后台数据」时用，两个号不同时开。
            <br>登录态目录：<span class="mono">${esc(st.profileDir)}</span>
            ${otherOn ? `<br><span class="status-warn">「${esc(st.other.roleName)}」的浏览器现在还开着</span>，先关掉它再启动本号，否则两个号同 IP 同时在线容易被平台关联。` : ""}
          </div>`;
        bar.querySelector("#launchBtn").onclick = async () => {
          bar.querySelector("#launchBtn").disabled = true;
          const r = await api.post("/api/crawl/browser/launch", {
            platform: root.querySelector("#platform").value, role: "crawl",
          });
          if (r.ok) toast("采集号浏览器已就绪，请在窗口里确认已登录", "ok");
          else toast(r.message || "启动失败", "err");
          refreshBrowser();
        };
        bar.querySelector("#closeBrowserBtn").onclick = async () => {
          await api.post("/api/crawl/browser/close", { role: "crawl" });
          refreshBrowser();
        };
      } catch (e) {
        bar.innerHTML = `<div class="banner err">读取浏览器状态失败：${esc(e.message)}</div>`;
      }
    }

    // ------------------------------ 采集护栏（组间冷却 / 日限额 / 风控刹车）
    // 规则全在服务端的 core/crawl_guard.py，这里只负责显示「还能不能采、还要等多久」，
    // 不在前端重算一遍——两套判断迟早会不一致。
    const guardBox = root.querySelector("#guardBar");
    let guardTimer = null;

    function mmss(sec) {
      sec = Math.max(0, Math.round(sec));
      if (sec >= 60) return `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`;
      return `${sec} 秒`;
    }

    async function refreshGuard() {
      let g;
      try { g = await api.get("/api/crawl/guard"); } catch (e) { return; }
      const lim = g.limits || {}, t = g.today || {}, risk = g.risk || {}, cd = g.cooldown || {};
      const cap = +lim.dailySearch || 0;
      const used = +t.search || 0;
      const rest = Math.max(0, cap - used);
      const riskLeft = +risk.leftSec || 0;
      const cdLeft = +cd.leftSec || 0;

      let tone = "status-ok", head = "可以采";
      if (riskLeft) { tone = "status-err"; head = `风控刹车 ${mmss(riskLeft)}`; }
      else if (cdLeft) { tone = "status-warn"; head = `冷却 ${mmss(cdLeft)}`; }
      else if (rest <= 0) { tone = "status-err"; head = "今日额度用完"; }

      guardBox.style.display = "";
      guardBox.innerHTML = `
        <div class="row">
          <div class="stat"><b class="${tone}">${head}</b><span>采集护栏</span></div>
          <div class="stat"><b>${used} / ${cap}</b><span>今日搜索条数</span></div>
          <div class="stat"><b>${t.detail || 0} · ${t.creator || 0}</b><span>ID 补采 · 读后台</span></div>
          <span class="spacer"></span>
          ${riskLeft ? `<button class="sm" id="guardResetBtn">解除刹车</button>` : ""}
        </div>
        <div class="hint" style="margin-top:8px">
          组间冷却 ${lim.cooldownSec} 秒 · 搜索日上限 ${cap} 条 ·
          连续撞 ${lim.riskStrikes} 次风控刹车 ${lim.riskCooldownMin} 分钟。
          461 是密度触发（上一组刚结束就起下一组必撞），不是日总量，所以卡的是节奏不是次数。
          ${riskLeft && risk.lastMark ? `<br>最近命中：<span class="mono">${esc(risk.lastMark)}</span>` : ""}
        </div>`;

      const rb = guardBox.querySelector("#guardResetBtn");
      if (rb) {
        rb.onclick = async () => {
          rb.disabled = true;
          await api.post("/api/crawl/guard/reset", { what: "risk" });
          toast("已解除刹车。建议先采 30 条试水，别再连组。", "ok");
          refreshGuard();
        };
      }

      const hint = root.querySelector("#startHint");
      if (hint) {
        hint.textContent = rest > 0
          ? `今天还能采 ${rest} 条（上限 ${cap}）`
          : "今天搜索额度已用完；按 ID 补采不受此限";
      }
    }

    // ------------------------------ 日志
    const logBox = root.querySelector("#logBox");
    let logLines = [];
    let autoScroll = true;
    logBox.addEventListener("scroll", () => {
      autoScroll = logBox.scrollTop + logBox.clientHeight > logBox.scrollHeight - 30;
    });

    function pushLog(it) {
      logLines.push(it);
      if (logLines.length > 3000) logLines.shift();
      renderLogs();
    }
    function renderLogs() {
      const onlyErr = root.querySelector("#onlyErr").checked;
      const rows = onlyErr ? logLines.filter((x) => x.level === "error") : logLines;
      logBox.innerHTML = rows
        .map((x) => `<div class="l-${x.level}"><span class="ts">${(x.ts || "").slice(11)}</span>${esc(x.text)}</div>`)
        .join("");
      if (autoScroll) logBox.scrollTop = logBox.scrollHeight;
      const m = [...logLines].reverse().find((x) => /(\d+)\s*条/.test(x.text));
      if (m) {
        const n = +m.text.match(/(\d+)\s*条/)[1];
        const max = +root.querySelector("#maxNotes").value;
        root.querySelector("#progBar").style.width = Math.min(100, (n / Math.max(1, max)) * 100) + "%";
      }
    }
    root.querySelector("#onlyErr").onchange = renderLogs;
    root.querySelector("#clearLog").onclick = () => { logLines = []; logBox.innerHTML = ""; };
    root.querySelector("#copyLog").onclick = () => {
      navigator.clipboard.writeText(logLines.map((x) => x.text).join("\n"))
        .then(() => toast("日志已复制", "ok"));
    };

    // ------------------------------ SSE
    function attachStream(jobId) {
      closeStream();
      collectState.jobId = jobId;
      const es = new EventSource(`/api/crawl/jobs/${jobId}/stream?after=0`);
      collectState.es = es;
      let done = false;
      es.addEventListener("log", (e) => {
        try { pushLog(JSON.parse(e.data)); } catch (err) {}
      });
      es.addEventListener("done", (e) => {
        done = true;
        let d = {};
        try { d = JSON.parse(e.data); } catch (err) {}
        root.querySelector("#startBtn").disabled = false;
        root.querySelector("#stopBtn").disabled = true;
        root.querySelector("#jobTag").textContent = d.status || "结束";
        root.querySelector("#jobHint").innerHTML =
          `任务结束（${d.status || "?"}，退出码 ${d.exitCode}）。` +
          (d.status === "done" ? ` 点下面的批次表里那条的「导入」把数据存进库。` : "");
        if (d.status === "done") toast("采集完成，去批次表点导入", "ok");
        else toast("采集结束：" + (d.status || ""), "err");
        es.close();
        collectState.es = null;
        loadBatches();
        refreshGuard();   // 组间冷却从这一刻开始计时
      });
      es.onerror = () => {
        if (done) return;
        // 浏览器会自动重连，这里只提示一次
      };
    }

    // ------------------------------ 启动 / 停止
    root.querySelector("#startBtn").onclick = async () => {
      if (!collectState.keywords.length) { toast("先填关键词", "err"); return; }
      const btn = root.querySelector("#startBtn");
      btn.disabled = true;
      try {
        const platform = root.querySelector("#platform").value;
        const res = await api.post("/api/crawl/jobs", {
          platform,
          keywords: collectState.keywords,
          maxNotes: +root.querySelector("#maxNotes").value,
          withComments: root.querySelector("#withComments").checked,
          sortType: platform === "xhs" ? collectState.sortType : "",
        });
        logLines = [];
        logBox.innerHTML = "";
        root.querySelector("#startBtn").disabled = true;
        root.querySelector("#stopBtn").disabled = false;
        root.querySelector("#jobTag").textContent = "运行中";
        root.querySelector("#jobHint").textContent =
          "采集中，浏览器窗口会自己动作，别去操作它。日志在下面实时滚动。";
        attachStream(res.jobId);
      } catch (e) {
        btn.disabled = false;
        toast(e.message, "err");
        // 被护栏拦下时护栏条会变（冷却/刹车/额度），立刻刷一次让用户看到还要等多久
        refreshGuard();
      }
    };

    root.querySelector("#stopBtn").onclick = async () => {
      if (!collectState.jobId) return;
      root.querySelector("#stopBtn").disabled = true;
      await api.post(`/api/crawl/jobs/${collectState.jobId}/stop`);
      toast("已发送停止", "warn");
    };

    // ------------------------------ 批次表
    async function loadBatches() {
      const box = root.querySelector("#batchTable");
      try {
        const res = await api.get("/api/crawl/batches");
        collectState.batches = res.items || [];
        root.querySelector("#batchTag").textContent = `${collectState.batches.length} 条`;
        if (!collectState.batches.length) {
          box.innerHTML = `<div class="empty">还没有批次。先启动专用浏览器并登录，再发起采集。</div>`;
          return;
        }
        box.innerHTML = `
          <div class="tbl-wrap"><table class="tbl">
            <thead><tr>
              <th>批次</th><th>平台</th><th>关键词</th><th>开始</th>
              <th class="num">耗时</th><th>状态</th><th>内容</th><th>操作</th>
            </tr></thead>
            <tbody>${collectState.batches.map(batchRow).join("")}</tbody>
          </table></div>`;

        box.querySelectorAll("[data-import]").forEach((btn) => {
          btn.onclick = async () => {
            const id = btn.dataset.import;
            btn.disabled = true;
            btn.textContent = "导入中…";
            try {
              const r = await api.post(`/api/crawl/batches/${id}/import`, {});
              if (!r.added && r.duplicated) {
                toast(`这批之前已经导过了：${r.duplicated} 条全在库里，没有新增。`, "");
              } else {
                toast(`新增 ${r.added} · 更新 ${r.updated} · 重复 ${r.duplicated} · 评论 ${r.comments}`, "ok");
              }
              loadBatches();
            } catch (e) {
              toast(e.message, "err");
            } finally {
              btn.disabled = false;
              btn.textContent = "导入";
            }
          };
        });
        box.querySelectorAll("[data-del]").forEach((btn) => {
          btn.onclick = () => confirmDialog("删除这条批次记录？（原始文件不会删）", async () => {
            await api.del(`/api/crawl/batches/${btn.dataset.del}`);
            loadBatches();
          });
        });
      } catch (e) {
        box.innerHTML = `<div class="banner err">${esc(e.message)}</div>`;
      }
    }

    function batchRow(b) {
      const st = {
        running: '<span class="chip on">运行中</span>',
        done: '<span class="chip on">完成</span>',
        failed: '<span class="chip debate">失败</span>',
        empty: '<span class="chip resonance">没采到</span>',
        stopped: '<span class="chip">已停止</span>',
        timeout: '<span class="chip resonance">超时</span>',
        interrupted: '<span class="chip unknown">中断</span>',
      }[b.status] || `<span class="chip">${esc(b.status || "")}</span>`;

      const imp = b.importStats;
      // 累计新增 / 首次有效率：重导一次全是重复，用最后一次的 0 会显示成「采集失败」
      const totalAdded = b.importTotalAdded != null ? b.importTotalAdded : (imp ? imp.added : null);
      const effPct = b.firstEfficiency != null ? Math.round(b.firstEfficiency * 100)
        : (imp && imp.efficiency != null ? Math.round(imp.efficiency * 100) : null);
      const times = b.importTimes || (b.importedAt ? 1 : 0);
      const imported = imp
        ? `<span class="status-ok">${totalAdded}</span>${effPct == null ? "" : ` <span class="small muted">· 效 ${effPct}%</span>`}`
        : (b.rawFileCount ? `<span class="status-warn">待导入</span>` : "—");
      const impTitle = imp
        ? `累计新增 ${totalAdded} 条（导入 ${times} 次）`
          + `\n最近一次：新增 ${imp.added} · 重复 ${imp.repeated == null ? (imp.updated || 0) + (imp.duplicated || 0) : imp.repeated} · 共 ${imp.contents} 行`
          + `\n有效率按首次导入算${effPct == null ? "" : ` ${effPct}%`}`
        : "";

      // 对话里提的采集：标出来，别跟手动扫盘混在一起看
      const fromChat = b.origin === "chat"
        ? ` <span class="chip on" title="${esc(b.reason || "选题搭子提交")}">对话</span>` +
          (b.reason ? `<div class="small muted" style="margin-top:2px;max-width:260px;white-space:normal;line-height:1.5">${esc(String(b.reason).slice(0, 40))}${String(b.reason).length > 40 ? "…" : ""}</div>` : "")
        : "";

      return `<tr>
        <td class="mono small">${esc((b.id || "").replace("b_", ""))}</td>
        <td>${esc(window.WB.PLATFORM_NAMES[b.platform] || b.platform)}</td>
        <td class="small">${esc((b.keywords || []).join("、"))}${b.sortType ? ` <span class="chip">${esc(SORT_LABELS[b.sortType] || b.sortType)}</span>` : ""}${fromChat}</td>
        <td class="small nowrap">${esc(fmtDate(b.startedAt))}</td>
        <td class="num small">${b.durationSec ? fmtSec(b.durationSec) : "—"}</td>
        <td>${st}</td>
        <td class="small" title="${esc(impTitle)}">${imported}</td>
        <td class="nowrap">
          <button class="sm" data-import="${esc(b.id)}" ${b.rawFileCount ? "" : "disabled"}>${times ? "重导" : "导入"}</button>
          <button class="ghost sm danger" data-del="${esc(b.id)}">删</button>
        </td>
      </tr>`;
    }

    root.querySelector("#openRaw").onclick = () => api.post("/api/open-folder", { which: "raw" });

    await refreshBrowser();
    await refreshGuard();
    // 冷却/刹车是倒计时，15 秒刷一次够用；这个页面不常驻，timer 在离开时必须清掉
    guardTimer = setInterval(refreshGuard, 15000);
    await loadBatches();

    // 如果已有任务在跑，直接接上去
    try {
      const j = await api.get("/api/crawl/jobs?limit=5");
      if (j.current) {
        root.querySelector("#startBtn").disabled = true;
        root.querySelector("#stopBtn").disabled = false;
        attachStream(j.current.id);
      }
    } catch (e) {}

    return () => { closeStream(); if (guardTimer) clearInterval(guardTimer); };
  }

  // ================================================================ 选题台

  const SEL_KEY = "wb.topics.selected";

  function loadSel() {
    try {
      const raw = JSON.parse(localStorage.getItem(SEL_KEY) || "[]");
      return Array.isArray(raw) ? raw : [];
    } catch (e) { return []; }
  }

  const topicsState = { selected: new Set(loadSel()), page: 1, items: [], enums: null };

  function saveSel() {
    try { localStorage.setItem(SEL_KEY, JSON.stringify([...topicsState.selected])); } catch (e) {}
  }

  async function renderTopics(root, params) {
    root.innerHTML = `
      <div class="page-head">
        <h1>选题台</h1>
        <span class="sub">从素材里找缺口，不是从脑子里想话题</span>
        <span class="spacer"></span>
        <button id="refreshBtn" class="sm">刷新</button>
      </div>
      <div class="grid2">
        <div class="card">
          <h2>素材池 <span class="tag" id="poolTag"></span></h2>
          <div class="row" style="margin-bottom:10px">
            <select id="fPlatform" style="width:auto">
              <option value="">全部平台</option>
              <option value="xhs">小红书</option>
              <option value="dy">抖音</option>
              <option value="bili">B站</option>
            </select>
            <select id="fStructure" style="width:auto">
              <option value="">全部结构</option>
              <option value="collect">收藏型</option>
              <option value="debate">争论型</option>
              <option value="resonance">共鸣型</option>
              <option value="pass_by">路过型</option>
              <option value="unknown">未知</option>
            </select>
            <select id="fSort" style="width:auto">
              <option value="like">按点赞</option>
              <option value="collectRate">按收藏率</option>
              <option value="commentRate">按评论率</option>
              <option value="relativeRank">按相对位次</option>
              <option value="publishedAt">按发布时间</option>
            </select>
            <select id="fAge" style="width:auto" title="按发布时间筛">
              <option value="0">全部时效</option>
              <option value="30">30 天内</option>
              <option value="90">90 天内</option>
              <option value="180">180 天内</option>
              <option value="365">1 年内</option>
            </select>
            <input type="text" id="fQ" placeholder="搜标题/正文" style="width:120px">
          </div>
          <div id="poolBox"><div class="empty">加载中…</div></div>
          <div class="row" style="margin-top:10px">
            <button class="sm" id="prevPage">上一页</button>
            <span class="hint" id="pageInfo"></span>
            <button class="sm" id="nextPage">下一页</button>
            <span class="spacer"></span>
            <span class="hint" id="selInfo">已选 0 条</span>
            <a href="#" id="clearSel" class="hint" style="margin-left:8px;display:none">清空</a>
          </div>
        </div>

        <div>
          <div class="card">
            <h2>选题搭子 <span class="tag" id="ctxTag">未选素材</span></h2>
            <div class="row" style="margin-bottom:8px">
              <input type="text" id="seed" class="grow" placeholder="这轮想研究什么（可留空）">
              <select id="count" style="width:auto" title="最多出几条">
                <option>2</option><option>3</option><option selected>4</option><option>5</option><option>8</option>
              </select>
              <select id="accountSel" style="width:auto"><option value="">默认账号</option></select>
            </div>
            <details style="margin-bottom:8px">
              <summary class="hint" style="cursor:pointer">展开约束（选填，填了不容易跑偏）</summary>
              <div style="margin-top:8px">
                <label class="field"><span>mustPreserve 不能被带偏的</span><input type="text" id="sb_must" placeholder="比如：讲关系，不讲人格打分"></label>
                <label class="field"><span>forbiddenDrift 看起来有流量但要避免的</span><input type="text" id="sb_forbid" placeholder="比如：十六型排行榜"></label>
                <label class="field"><span>assumptionsToVerify 需要验证的判断</span><input type="text" id="sb_assume" placeholder="比如：读者是 20-28 岁职场新人"></label>
                <label class="field"><span>openQuestions 还不清楚的</span><input type="text" id="sb_open" placeholder="比如：他们更关心恋爱还是职场"></label>
                <label class="field"><span>allowedExpansion 可以延展的相邻场景</span><input type="text" id="sb_expand" placeholder="比如：MBTI 和加班、汇报、背锅"></label>
              </div>
            </details>
            <div class="hint" id="pickHint" style="margin-bottom:6px"></div>
            <div class="banner err" id="keyHint" style="display:none">
              还没配 API Key，对话用不了。<a href="#/settings">去设置填一个 →</a>
            </div>
            <div id="chatHost"></div>
          </div>

          <div class="card">
            <h2>选题卡 <span class="tag" id="topicTag"></span></h2>
            <div class="row" style="margin-bottom:10px">
              <select id="tStatus" style="width:auto">
                <option value="">全部状态</option>
                <option value="candidate">待定</option>
                <option value="adopted">已采纳</option>
                <option value="rejected">已驳回</option>
              </select>
            </div>
            <div id="topicBox"><div class="empty">还没有选题</div></div>
          </div>
        </div>
      </div>
    `;

    // ---------- 账号下拉
    let accounts = [];
    try {
      const a = await api.get("/api/accounts");
      accounts = a.items || [];
      const sel = root.querySelector("#accountSel");
      accounts.forEach((x) => {
        const o = document.createElement("option");
        o.value = x.id;
        o.textContent = `${window.WB.PLATFORM_NAMES[x.platform] || x.platform} · ${x.displayName || "未命名"}`;
        sel.appendChild(o);
      });
    } catch (e) {}

    // ---------- API Key 检查：对话没法复制粘贴，没 key 就是不能用
    async function checkKey() {
      try {
        const st = await api.get("/api/settings");
        const ok = st.ai.mode === "api" && st.ai.apiKeySet;
        root.querySelector("#keyHint").style.display = ok ? "none" : "";
      } catch (e) {}
    }
    checkKey();

    // ---------- 素材池
    async function loadPool() {
      const box = root.querySelector("#poolBox");
      const q = new URLSearchParams({
        platform: root.querySelector("#fPlatform").value,
        structure: root.querySelector("#fStructure").value,
        sort: root.querySelector("#fSort").value,
        age: root.querySelector("#fAge").value,
        q: root.querySelector("#fQ").value.trim(),
        page: topicsState.page,
        pageSize: 50,
      });
      try {
        const res = await api.get("/api/contents?" + q.toString());
        topicsState.items = res.items || [];
        root.querySelector("#poolTag").textContent = `共 ${res.total} 条`;
        root.querySelector("#pageInfo").textContent = `第 ${res.page} 页`;
        if (!res.items.length) {
          box.innerHTML = `<div class="empty">这个地方还没有数据。去<a href="#/collect">采集台</a>跑一次，然后导入。</div>`;
          return;
        }
        box.innerHTML = `
          <div class="tbl-wrap"><table class="tbl">
            <thead><tr>
              <th style="width:28px"></th><th>标题</th><th>平台</th><th>发布</th>
              <th class="num">点赞</th><th class="num">收藏率</th><th class="num">评论率</th>
              <th>结构</th><th>关键词</th>
            </tr></thead>
            <tbody>${res.items.map(poolRow).join("")}</tbody>
          </table></div>`;

        box.querySelectorAll("[data-pick]").forEach((cb) => {
          cb.checked = topicsState.selected.has(cb.dataset.pick);
          cb.onchange = () => {
            if (cb.checked) topicsState.selected.add(cb.dataset.pick);
            else topicsState.selected.delete(cb.dataset.pick);
            saveSel();
            updateSelInfo();
          };
        });
        box.querySelectorAll("[data-open]").forEach((a) => {
          a.onclick = (e) => {
            e.preventDefault();
            openContentDetail(a.dataset.open);
          };
        });
        updateSelInfo();
      } catch (e) {
        box.innerHTML = `<div class="banner err">${esc(e.message)}</div>`;
      }
    }

    function ageLabel(it) {
      const d = it.ageDays;
      if (d == null) return `<span class="muted">—</span>`;
      let txt;
      if (d <= 1) txt = "今天";
      else if (d < 30) txt = `${d}天`;
      else if (d < 365) txt = `${Math.floor(d / 30)}个月`;
      else txt = `${Math.floor(d / 365)}年`;
      const cls = d > 365 ? "status-err" : (d > 180 ? "status-warn" : "");
      return `<span class="${cls}">${txt}</span>`;
    }

    function poolRow(it) {
      const m = it.metrics || {};
      return `<tr>
        <td><input type="checkbox" data-pick="${esc(it.key)}" style="width:auto"></td>
        <td class="t"><a href="#" data-open="${esc(it.key)}" style="color:inherit;text-decoration:none">${esc(it.title || "(无标题)")}</a></td>
        <td class="small nowrap">${esc(window.WB.PLATFORM_NAMES[it.platform] || it.platform)}</td>
        <td class="small nowrap">${ageLabel(it)}</td>
        <td class="num">${fmtNum(m.like)}</td>
        <td class="num">${fmtPct(m.collectRate)}</td>
        <td class="num">${fmtPct(m.commentRate)}</td>
        <td><span class="chip ${esc(it.structure || "")}">${esc(it.structureName || "—")}</span></td>
        <td class="small muted">${esc(it.keyword || "")}</td>
      </tr>`;
    }

    function ageRangeText() {
      const v = root.querySelector("#fAge").value;
      return { "30": "30 天内", "90": "90 天内", "180": "180 天内", "365": "1 年内" }[v] || "";
    }

    function updateSelInfo() {
      const n = topicsState.selected.size;
      root.querySelector("#selInfo").textContent = `已选 ${n} 条`;
      root.querySelector("#clearSel").style.display = n ? "" : "none";

      const tag = root.querySelector("#ctxTag");
      if (tag) tag.textContent = n ? `指定深读 ${n} 条` : "未选素材";

      const hint = root.querySelector("#pickHint");
      if (!hint) return;
      const rng = ageRangeText();
      hint.innerHTML = n
        ? `已勾 <b>${Math.min(n, 10)} 条</b> → 它会把这几条的正文和评论逐条读完再出选题。`
        : `没勾素材 → 它自己在库里找${rng ? `（时效：${rng}）` : ""}，不按点赞筛。`;
    }

    // 内容详情
    async function openContentDetail(key) {
      const [p, id] = key.split(":");
      try {
        const [rec, cm] = await Promise.all([
          api.get(`/api/contents/${p}/${id}`),
          api.get(`/api/contents/${p}/${id}/comments?limit=50`).catch(() => ({ items: [] })),
        ]);
        const m = rec.metrics || {};
        drawer("内容详情", `
          <div class="topic" style="border:none;padding:0">
            <div class="h"><b>${esc(rec.title || "(无标题)")}</b></div>
            <div class="stat-row" style="margin:10px 0">
              <div class="stat"><b>${fmtNum(m.like)}</b><span>点赞</span></div>
              <div class="stat"><b>${fmtNum(m.collect)}</b><span>收藏</span></div>
              <div class="stat"><b>${fmtNum(m.comment)}</b><span>评论</span></div>
              <div class="stat"><b>${fmtNum(m.share)}</b><span>分享</span></div>
              <div class="stat"><b>${fmtPct(m.collectRate)}</b><span>收藏率</span></div>
              <div class="stat"><b>${fmtPct(m.commentRate)}</b><span>评论率</span></div>
            </div>
            ${(rec.metricsMissing || []).length ? `<div class="banner">缺失字段：${rec.metricsMissing.join("、")}（按未知处理，不当作 0）</div>` : ""}
            <div class="hint" style="white-space:pre-wrap;margin:10px 0">${esc((rec.body || "").slice(0, 800))}</div>
            <div class="hint">关键词：${esc((rec.source || {}).keyword || "—")} ｜ 发布：${esc(fmtDate(rec.publishedAt))} ｜ 相对位次：${rec.relativeRank == null ? "—" : rec.relativeRank}</div>
            ${rec.url ? `<div style="margin-top:8px"><a href="${esc(rec.url)}" target="_blank">打开原链接</a></div>` : ""}
          </div>
          <div class="card" style="margin-top:14px">
            <h2>评论 ${cm.items ? `(${cm.items.length})` : ""}</h2>
            <div class="hint" style="margin-bottom:8px">${cm.groups ? Object.entries(cm.groups).map(([k, v]) => `${({ask:"追问",disagree:"反驳",add:"补充",emotion:"情绪",action:"行动",author:"作者"})[k] || k} ${v}`).join(" · ") : "无"}</div>
            ${(cm.items || []).length
              ? (cm.items || []).map((c) => `<div class="ev" style="margin-bottom:6px"><span class="muted small">${fmtNum(c.like)}赞</span> ${esc(c.content)}</div>`).join("")
              : `<div class="empty">没有评论数据（采集时没勾选评论，或这条没人评论）</div>`}
          </div>
        `);
      } catch (e) {
        const msg = String(e.message || "");
        if (/404|不存在|not found/i.test(msg)) {
          toast("这条笔记不在内容池里（可能属于早期无图批次，已归档）", "err");
        } else {
          toast(msg, "err");
        }
      }
    }

    root.querySelector("#fPlatform").onchange = () => { topicsState.page = 1; loadPool(); };
    root.querySelector("#fStructure").onchange = () => { topicsState.page = 1; loadPool(); };
    root.querySelector("#fSort").onchange = () => { topicsState.page = 1; loadPool(); };
    root.querySelector("#fAge").onchange = () => { topicsState.page = 1; loadPool(); updateSelInfo(); };
    root.querySelector("#clearSel").onclick = (e) => {
      e.preventDefault();
      topicsState.selected.clear();
      saveSel();
      loadPool();
    };
    let qTimer = null;
    root.querySelector("#fQ").oninput = () => {
      clearTimeout(qTimer);
      qTimer = setTimeout(() => { topicsState.page = 1; loadPool(); }, 350);
    };
    root.querySelector("#prevPage").onclick = () => { if (topicsState.page > 1) { topicsState.page--; loadPool(); } };
    root.querySelector("#nextPage").onclick = () => { topicsState.page++; loadPool(); };

    // ---------- 选区 → 对话上下文
    function getCtx() {
      return {
        accountId: root.querySelector("#accountSel").value,
        contentKeys: [...topicsState.selected],
        seed: root.querySelector("#seed").value.trim(),
        count: +root.querySelector("#count").value || 4,
        candidateRange: ageRangeText(),
        seedBrief: {
          mustPreserve: root.querySelector("#sb_must").value.trim(),
          forbiddenDrift: root.querySelector("#sb_forbid").value.trim(),
          assumptionsToVerify: root.querySelector("#sb_assume").value.trim(),
          openQuestions: root.querySelector("#sb_open").value.trim(),
          allowedExpansion: root.querySelector("#sb_expand").value.trim(),
        },
      };
    }

    // 对话就是原来的「生成选题」，只是能追问、能只存其中一条
    let chatPanel = null;
    window.ChatPanel.mount(root.querySelector("#chatHost"), {
      getCtx,
      openNote: openContentDetail,
      onSaved: () => loadTopics(),
      placeholder: "勾素材后问：这几条里哪个缺口最能做？",
    }).then((p) => { chatPanel = p; })
      .catch((e) => {
        root.querySelector("#chatHost").innerHTML =
          `<div class="banner err">对话组件没起来：${esc(e.message)}</div>`;
      });

    // ---------- 选题卡
    async function loadTopics() {
      const box = root.querySelector("#topicBox");
      const q = new URLSearchParams({ status: root.querySelector("#tStatus").value });
      try {
        const res = await api.get("/api/topics?" + q.toString());
        topicsState.enums = res.enums || topicsState.enums;
        const adopted = res.items.filter((x) => x.status === "adopted").length;
        root.querySelector("#topicTag").innerHTML = `${res.items.length} 条`
          + (adopted ? ` · <a href="#/create">${adopted} 篇待写</a>` : "");
        if (!res.items.length) {
          box.innerHTML = `<div class="empty">还没有选题。左边选几条素材，填个 seed，点生成。</div>`;
          return;
        }
        box.innerHTML = res.items.map(topicCard).join("");
        bindTopicActions();
      } catch (e) {
        box.innerHTML = `<div class="banner err">${esc(e.message)}</div>`;
      }
    }

    function methodName(v) {
      const m = (topicsState.enums || {}).methods || {};
      return m[v] || v || "—";
    }

    function enumName(group, v) {
      const m = (topicsState.enums || {})[group] || {};
      return m[v] || v || "—";
    }

    function topicCard(t) {
      const qc = t.qualityCheck || {};
      const dots = (qc.items || []).map((i) =>
        `<i class="${i.pass ? "ok" : "bad"}" title="${esc(i.label)}：${esc(i.message)}"></i>`).join("");
      const statusChip = {
        candidate: '<span class="chip">待定</span>',
        adopted: '<span class="chip on">已采纳</span>',
        rejected: '<span class="chip pass_by">已驳回</span>',
      }[t.status] || "";
      const ei = t.editorialIntent || {};
      const ref = ((t.evidence || [])[0] || {}).ref;
      // 证据素材被归档后，这条选题就只剩文案，指不回原文。标出来，别装没事
      const staleChip = t.stale ? '<span class="chip bad" title="证据素材已从内容池归档">素材已归档</span>' : "";
      const openLink = t.stale ? '<span class="hint">（原笔记已归档，无法查证）</span>'
        : (ref ? ` <a href="#" data-open="${esc(ref)}" class="hint">看原笔记</a>` : "");
      return `<div class="topic" style="margin-bottom:10px" data-topic="${esc(t.id)}">
        <div class="h">
          <b>${esc(t.title || "(无标题)")}</b>
          ${statusChip}
          ${staleChip}
        </div>
        ${t.stale ? `<div class="banner" style="margin:6px 0">这条的支撑素材在早期批次里，当时没存图、链接已过期，已移出内容池。证据指不回去，建议驳回重做。</div>` : ""}
        <div class="kv"><span class="k">读者</span>${esc(t.reader || "—")}</div>
        <div class="kv">
          <span class="k">缺口</span>${esc(enumName("gaps", t.gap))}
          ｜ <span class="k">角度</span>${esc(enumName("angles", t.angle))}
          ${t.layer ? `｜ <span class="k">层</span>${esc(t.layer)}` : ""}
        </div>
        <div class="kv">
          <span class="k">方法</span>${esc(methodName(t.method))}
          ｜ <span class="k">体裁</span>${esc(ei.genre || "—")}
          ${ei.creatorGoal ? `｜ <span class="k">动机</span>${esc(ei.creatorGoal.slice(0, 18))}` : ""}
        </div>
        ${(t.evidence || []).length ? `<div class="ev">${esc((t.evidence[0].quote || "").slice(0, 90))}${openLink}</div>` : ""}
        ${ei.mustNotDrift ? `<div class="hint" style="margin-top:4px">不能写成：${esc(ei.mustNotDrift)}</div>` : ""}
        <div class="foot">
          <span class="dots">${dots}</span>
          <span class="hint">${qc.passCount || 0}/${qc.total || 11}</span>
          <span class="spacer"></span>
          ${t.status === "adopted"
            ? `<button class="primary sm" data-write="${esc(t.id)}">去写作</button>`
            : `<button class="sm" data-adopt="${esc(t.id)}">采纳并建稿</button>`}
          <button class="sm" data-edit="${esc(t.id)}">改</button>
          <button class="ghost sm danger" data-reject="${esc(t.id)}">驳回</button>
        </div>
      </div>`;
    }

    function bindTopicActions() {
      const box = root.querySelector("#topicBox");
      // 采纳不是打个标记：这一步就把稿子建出来，否则创作台里是空的（用户点进去会以为没生效）
      box.querySelectorAll("[data-adopt]").forEach((b) => {
        b.onclick = async () => {
          b.disabled = true;
          b.textContent = "建稿中…";
          try {
            await api.post(`/api/topics/${b.dataset.adopt}/to-draft`, {});
            toast("已采纳，稿子已建在创作台", "ok");
            loadTopics();
          } catch (e) {
            b.disabled = false;
            b.textContent = "采纳并建稿";
            toast(e.message, "err");
          }
        };
      });
      // 已采纳的选题：直接带到创作台，并选中这篇稿子
      box.querySelectorAll("[data-write]").forEach((b) => {
        b.onclick = async () => {
          b.disabled = true;
          try {
            const d = await api.post(`/api/topics/${b.dataset.write}/to-draft`, {});
            location.hash = `#/create?draft=${d.id}`;
          } catch (e) {
            b.disabled = false;
            toast(e.message, "err");
          }
        };
      });
      box.querySelectorAll("[data-reject]").forEach((b) => {
        b.onclick = async () => {
          await api.put(`/api/topics/${b.dataset.reject}`, { status: "rejected" });
          loadTopics();
        };
      });
      box.querySelectorAll("[data-edit]").forEach((b) => {
        b.onclick = () => editTopic(b.dataset.edit);
      });
      box.querySelectorAll("[data-open]").forEach((a) => {
        a.onclick = (e) => { e.preventDefault(); openContentDetail(a.dataset.open); };
      });
    }

    async function editTopic(id) {
      const list = await api.get("/api/topics");
      topicsState.enums = list.enums || topicsState.enums;
      const t = (list.items || []).find((x) => x.id === id);
      if (!t) return;
      const enums = list.enums || {};
      const ei = t.editorialIntent || {};
      const opts = (map, cur) => Object.entries(map || {}).map(([k, v]) =>
        `<option value="${k}" ${cur === k ? "selected" : ""}>${esc(v)}</option>`).join("");
      const gapOpts = opts(enums.gaps, t.gap);
      const angOpts = opts(enums.angles, t.angle);
      const methodOpts = `<option value="" ${!t.method ? "selected" : ""}>未选</option>`
        + opts(enums.methods, t.method);
      const genreOpts = `<option value="" ${!ei.genre ? "selected" : ""}>未定</option>`
        + (enums.genres || []).map((g) =>
          `<option value="${esc(g)}" ${ei.genre === g ? "selected" : ""}>${esc(g)}</option>`).join("");
      const layerOpts = ["", "L1", "L2", "L3", "L4", "L5"].map((k) =>
        `<option value="${k}" ${t.layer === k ? "selected" : ""}>${k || "未标"}</option>`).join("");
      const formOpts = ["", ...(enums.forms || [])].map((k) =>
        `<option value="${k}" ${t.contentForm === k ? "selected" : ""}>${k || "未定"}</option>`).join("");

      drawer("编辑选题", `
        <label class="field"><span>标题（≤20 字）</span><input type="text" id="eTitle" value="${esc(t.title)}"></label>
        <label class="field"><span>读者（谁在什么时刻）</span><input type="text" id="eReader" value="${esc(t.reader)}"></label>
        <div class="row">
          <label class="field grow"><span>缺口</span><select id="eGap">${gapOpts}</select></label>
          <label class="field grow"><span>角度</span><select id="eAngle">${angOpts}</select></label>
        </div>
        <div class="row">
          <label class="field grow"><span>方法</span><select id="eMethod">${methodOpts}</select></label>
          <label class="field grow"><span>体裁</span><select id="eGenre">${genreOpts}</select></label>
        </div>
        <div class="row">
          <label class="field grow"><span>层级</span><select id="eLayer">${layerOpts}</select></label>
          <label class="field grow"><span>内容形式</span><select id="eForm">${formOpts}</select></label>
        </div>
        <label class="field"><span>作者动机（记录/表达/涨粉/卖货…）</span><input type="text" id="eGoal" value="${esc(ei.creatorGoal || "")}"></label>
        <label class="field"><span>绝不能被改写成什么</span><input type="text" id="eDrift" value="${esc(ei.mustNotDrift || "")}" placeholder="比如：不能写成「INTP 提高行动力」的教程"></label>
        <label class="field"><span>我们凭什么讲得出来</span><input type="text" id="eDeliver" value="${esc(t.canDeliver || "")}"></label>
        <label class="field"><span>证据（一行一条）</span>
          <textarea id="eEv">${esc((t.evidence || []).map((e) => e.quote || e).join("\n"))}</textarea>
        </label>
        <details>
          <summary class="hint" style="cursor:pointer">内容机会四问（选填）</summary>
          <div style="margin-top:8px">
            <label class="field"><span>读者具体卡在哪</span><input type="text" id="eProblem" value="${esc(t.userProblem || "")}"></label>
            <label class="field"><span>文章要回答什么、边界在哪</span><input type="text" id="eDir" value="${esc(t.contentDirection || "")}"></label>
            <label class="field"><span>读者看完得到什么</span><input type="text" id="eValue" value="${esc(t.contentValue || "")}"></label>
            <label class="field"><span>为什么这个号讲合理</span><input type="text" id="eFit" value="${esc(t.fitReason || "")}"></label>
          </div>
        </details>
        <div class="row">
          <button class="primary" id="saveTopic">保存</button>
          <span class="hint" id="chkHint"></span>
        </div>
      `, (body, wrap) => {
        body.querySelector("#saveTopic").onclick = async () => {
          const ev = body.querySelector("#eEv").value.split("\n").filter(Boolean);
          const oldRef = ((t.evidence || [])[0] || {}).ref || "";
          const payload = {
            title: body.querySelector("#eTitle").value.trim(),
            reader: body.querySelector("#eReader").value.trim(),
            gap: body.querySelector("#eGap").value,
            angle: body.querySelector("#eAngle").value,
            method: body.querySelector("#eMethod").value,
            editorialIntent: {
              genre: body.querySelector("#eGenre").value,
              readerGain: (t.editorialIntent || {}).readerGain || "",
              creatorGoal: body.querySelector("#eGoal").value.trim(),
              mustNotDrift: body.querySelector("#eDrift").value.trim(),
            },
            layer: body.querySelector("#eLayer").value || null,
            contentForm: body.querySelector("#eForm").value || null,
            canDeliver: body.querySelector("#eDeliver").value.trim(),
            userProblem: body.querySelector("#eProblem").value.trim(),
            contentDirection: body.querySelector("#eDir").value.trim(),
            contentValue: body.querySelector("#eValue").value.trim(),
            fitReason: body.querySelector("#eFit").value.trim(),
            evidence: ev.map((q, i) => ({ quote: q, ref: i === 0 ? oldRef : "" })),
          };
          const r = await api.put(`/api/topics/${id}`, payload);
          const qc = r.qualityCheck || {};
          toast(`已保存（质检 ${qc.passCount}/${qc.total}）`, qc.pass ? "ok" : "");
          wrap.remove();
          loadTopics();
        };
      });
    }

    root.querySelector("#tStatus").onchange = loadTopics;
    root.querySelector("#refreshBtn").onclick = () => { loadPool(); loadTopics(); };

    await loadPool();
    await loadTopics();
    return () => { if (chatPanel) chatPanel.destroy(); };
  }

  window.VIEWS = window.VIEWS || {};
  window.VIEWS.collect = renderCollect;
  window.VIEWS.topics = renderTopics;
})();
