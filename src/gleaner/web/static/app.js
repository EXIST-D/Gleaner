"use strict";

/* ---------- Helpers ---------- */

const TOKEN = document.querySelector('meta[name="gleaner-token"]').content;
const VIEWS = ["overview", "crawl", "posts", "packs"];
const LABEL_ORDER = ["candidate", "collection", "promo", "unclear", "irrelevant"];

const $ = (selector, root = document) => root.querySelector(selector);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

async function api(path, { method = "GET", body } = {}) {
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-Gleaner-Token": TOKEN },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({ ok: false, error: `服务器返回 ${response.status}` }));
  if (!response.ok || data.ok === false) throw new Error(data.error || `服务器返回 ${response.status}`);
  return data;
}

let toastTimer = null;
function toast(message, { error = false } = {}) {
  const el = $("#toast");
  el.textContent = message;
  el.className = error ? "toast error" : "toast";
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), error ? 5000 : 2600);
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fmtAgo(iso) {
  if (!iso) return "";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

function fmtDuration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function labelTitle(id) {
  const found = state.overview?.labels.find((label) => label.id === id);
  return found ? found.title : "未初筛";
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制");
  } catch {
    toast("复制失败，请手动选择文字复制", { error: true });
  }
}

/* ---------- State ---------- */

const state = {
  view: null,
  overview: null,
  crawl: { mode: "search", keywords: null, urls: "", pages: 1, force: false },
  job: null,
  since: 0,
  logLines: [],
  pollTimer: null,
  qrDeadline: null,
  waitDeadline: null,
  tick: null,
  posts: { label: "candidate", packed: "no", q: "", data: null },
  selected: new Set(),
};

/* ---------- Routing ---------- */

function currentView() {
  const view = location.hash.replace("#", "").split("?")[0];
  return VIEWS.includes(view) ? view : "overview";
}

async function show(view) {
  state.view = view;
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  VIEWS.forEach((name) => ($(`#view-${name}`).hidden = name !== view));
  closeDrawer();
  updateSelectionBar();
  try {
    if (view === "overview") await renderOverview();
    if (view === "crawl") await renderCrawl();
    if (view === "posts") await renderPosts();
    if (view === "packs") await renderPacks();
    const open = new URLSearchParams(location.hash.split("?")[1] || "").get("open");
    if (open && view === "posts") await openPost(open);
    if (open && view === "packs") await openPack(open);
  } catch (error) {
    toast(error.message, { error: true });
  }
}

async function refreshOverview() {
  state.overview = await api("/api/overview");
  const wait = state.overview.next_crawl_in;
  state.waitDeadline = wait > 0 ? Date.now() + wait * 1000 : null;
  return state.overview;
}

/* ---------- Overview ---------- */

async function renderOverview() {
  const o = await refreshOverview();
  const labels = o.stats.labels || {};
  const total = o.stats.posts || 0;
  const segments = [...LABEL_ORDER, "unscreened"]
    .filter((id) => labels[id])
    .map((id) => ({ id, n: labels[id], title: id === "unscreened" ? "未初筛" : labelTitle(id) }));
  const running = o.job && o.job.state === "running";

  $("#view-overview").innerHTML = `
    <div class="hero">
      <div>
        <h1>${total ? `已经拾回 ${total} 篇面经帖子` : "还没有抓取过帖子"}</h1>
        <p>${o.unpacked_candidates ? `其中 ${o.unpacked_candidates} 篇疑似真实面经还没打包，可以去「帖子」里挑选。` : "从「抓取」开始，按关键词收集小红书上的真实面经。"}</p>
      </div>
      <div class="head-actions">
        ${o.unpacked_candidates ? `<a class="btn" href="#posts">查看待打包</a>` : ""}
        <a class="btn primary large" href="#crawl">${running ? "查看抓取进度" : "开始抓取"}</a>
      </div>
    </div>

    <div class="grid stats">
      ${statCard("帖子总数", total, `${o.stats.runs} 次抓取或导入`)}
      ${statCard("疑似真实面经", labels.candidate || 0, "规则初筛结果", true)}
      ${statCard("待打包面经", o.unpacked_candidates, "还没交给题库")}
      ${statCard("已打包", o.stats.packed, o.stats.changed_after_pack ? `${o.stats.changed_after_pack} 篇打包后被修改` : "已生成素材包")}
    </div>

    <div class="grid two">
      <div class="stack">
        <div class="card">
          <h2>初筛分布</h2>
          ${total ? `
            <div class="bar">${segments.map((s) => `<span class="c-${s.id}" data-width="${(s.n / total) * 100}" title="${esc(s.title)} ${s.n}"></span>`).join("")}</div>
            <div class="legend">${segments.map((s) => `<span><i class="c-${s.id}"></i>${esc(s.title)} ${s.n}</span>`).join("")}</div>
          ` : `<div class="empty">暂无数据</div>`}
        </div>
        <div class="card">
          <h2>最近抓取</h2>
          <div class="list">
            ${o.runs.length ? o.runs.map(runRow).join("") : `<div class="empty">还没有抓取记录</div>`}
          </div>
        </div>
      </div>
      <div class="stack">
        <div class="card">
          <h2>关键词进度 <small>每个关键词抓到的最新发布日期</small></h2>
          <div class="list">
            ${o.checkpoints.length ? o.checkpoints.map((c) => `
              <div class="list-row"><div class="grow title">${esc(c.keyword)}</div><span class="pill">${esc(c.latest)}</span></div>
            `).join("") : `<div class="empty">抓取后会显示在这里</div>`}
          </div>
        </div>
        <div class="card">
          <h2>环境检查</h2>
          <div class="list">
            ${o.checks.map((c) => `
              <div class="list-row check-row">
                <span class="mark ${c.ok ? "ok" : "bad"}">${c.ok ? "✓" : "!"}</span>
                <div class="grow"><div class="title">${esc(c.title)}</div><div class="sub" title="${esc(c.detail)}">${esc(c.detail)}</div></div>
              </div>`).join("")}
          </div>
        </div>
      </div>
    </div>`;
  document.querySelectorAll("#view-overview .bar [data-width]").forEach((span) => {
    span.style.width = `${span.dataset.width}%`;
  });
}

function statCard(label, value, hint, accent = false) {
  return `<div class="card stat${accent ? " accent" : ""}"><span class="label">${esc(label)}</span><span class="value">${esc(value)}</span><span class="hint">${esc(hint)}</span></div>`;
}

function runRow(run) {
  const statusTitle = { ok: "成功", degraded: "降级", running: "进行中", failed: "失败" }[run.status] || run.status;
  const what = run.mode === "import" ? "导入已有数据" : run.targets ? `指定 ${run.targets} 篇帖子` : (run.keywords || []).join("、");
  return `
    <div class="list-row">
      <span class="pill dot ${esc(run.status)}">${esc(statusTitle)}</span>
      <div class="grow">
        <div class="title">${esc(what)}</div>
        <div class="sub" title="${esc(run.message)}">${run.message ? esc(run.message) : `读到 ${run.records} 条，新增 ${run.new} 篇`}</div>
      </div>
      <span class="faint" title="${esc(fmtTime(run.started_at))}">${esc(fmtAgo(run.started_at))}</span>
    </div>`;
}

/* ---------- Crawl ---------- */

async function renderCrawl() {
  const o = state.overview || (await refreshOverview());
  if (state.crawl.keywords === null) state.crawl.keywords = [...o.config.keywords];
  $("#view-crawl").innerHTML = `
    <div class="view-head">
      <div><h1>抓取</h1><p>调用本机的 MediaCrawler 抓取小红书帖子，结束后自动去重和初筛。</p></div>
    </div>
    <div class="crawl-grid">
      <div class="card" id="crawl-form"></div>
      <div class="card" id="crawl-progress"></div>
    </div>`;
  renderCrawlForm();
  await fetchJob(true);
  renderJob();
}

function plannedNotes() {
  const o = state.overview;
  if (state.crawl.mode === "detail") return urlList().length;
  return state.crawl.pages * o.config.page_size * state.crawl.keywords.length;
}

function urlList() {
  return state.crawl.urls.split(/\s+/).map((s) => s.trim()).filter(Boolean);
}

function waitSeconds() {
  return state.waitDeadline ? Math.max(0, Math.ceil((state.waitDeadline - Date.now()) / 1000)) : 0;
}

function renderCrawlForm() {
  const el = $("#crawl-form");
  if (!el) return;
  const c = state.crawl;
  const o = state.overview;
  const maxPages = Math.max(1, Math.floor(o.config.max_notes_cap / o.config.page_size));
  el.innerHTML = `
    <h2>新的抓取</h2>
    <div class="field">
      <div class="segmented" role="tablist">
        <button data-action="crawl-mode" data-mode="search" class="${c.mode === "search" ? "active" : ""}">关键词搜索</button>
        <button data-action="crawl-mode" data-mode="detail" class="${c.mode === "detail" ? "active" : ""}">指定帖子</button>
      </div>
    </div>
    ${c.mode === "search" ? `
      <div class="field">
        <span class="label">关键词</span>
        <div class="chips" id="keyword-chips">
          ${c.keywords.map((k, i) => `<span class="chip">${esc(k)}<button data-action="remove-keyword" data-index="${i}" aria-label="删除">✕</button></span>`).join("")}
          <input type="text" id="keyword-input" placeholder="${c.keywords.length ? "继续添加，回车确认" : "例如：后端 面经，回车确认"}">
        </div>
        <span class="help">每个关键词单独搜索；关键词里不能有逗号。</span>
      </div>
      <div class="field">
        <span class="label">每个关键词抓多少</span>
        <div class="segmented">
          ${Array.from({ length: maxPages }, (_, i) => i + 1).map((p) => `
            <button data-action="crawl-pages" data-pages="${p}" class="${c.pages === p ? "active" : ""}">${p} 页 · ${p * o.config.page_size} 篇</button>`).join("")}
        </div>
        <span class="help">小红书搜索按页返回，每页 ${o.config.page_size} 篇。</span>
      </div>
    ` : `
      <div class="field">
        <span class="label">帖子链接</span>
        <textarea id="url-input" placeholder="每行一个链接，需要带 xsec_token，从浏览器地址栏复制">${esc(c.urls)}</textarea>
      </div>
    `}
    <div class="plan"><span>本次最多约</span><strong id="plan-count">${plannedNotes()} 篇</strong></div>
    <button class="btn primary large block" data-action="start-crawl" id="start-btn">开始抓取</button>
    <div class="wait-note" id="wait-note" hidden></div>
    <details class="advanced">
      <summary>高级</summary>
      <label class="check"><input type="checkbox" id="force-input" ${c.force ? "checked" : ""}> 忽略两次抓取之间的最小间隔（不推荐）</label>
    </details>`;
  updateStartButton();
}

function updateStartButton() {
  const btn = $("#start-btn");
  if (!btn) return;
  const running = state.job && state.job.state === "running";
  const wait = waitSeconds();
  const empty = state.crawl.mode === "search" ? state.crawl.keywords.length === 0 : urlList().length === 0;
  btn.disabled = running || empty || (wait > 0 && !state.crawl.force);
  btn.textContent = running ? "抓取进行中…" : "开始抓取";
  const note = $("#wait-note");
  if (wait > 0 && !running) {
    note.hidden = false;
    note.textContent = `距离上次抓取不到 ${state.overview.config.min_interval_minutes} 分钟，还需等待 ${fmtDuration(wait)}`;
  } else {
    note.hidden = true;
  }
  const plan = $("#plan-count");
  if (plan) plan.textContent = `${plannedNotes()} 篇`;
}

function addKeyword(raw) {
  const parts = raw.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
  for (const part of parts) if (!state.crawl.keywords.includes(part)) state.crawl.keywords.push(part);
  renderCrawlForm();
  $("#keyword-input")?.focus();
}

async function startCrawl() {
  const c = state.crawl;
  const body = c.mode === "search"
    ? { keywords: c.keywords, max_notes: c.pages * state.overview.config.page_size, force: c.force }
    : { urls: urlList(), force: c.force };
  try {
    const data = await api("/api/crawl", { method: "POST", body });
    state.job = data.job;
    state.since = data.job.next;
    state.qrDeadline = null;
    state.logLines = [];
    const log = $("#crawl-log");
    if (log) log.innerHTML = "";
    renderJob();
    startPolling();
    toast("已开始抓取，留意弹出的 Chrome 窗口");
  } catch (error) {
    toast(error.message, { error: true });
  }
}

async function fetchJob(reset = false) {
  if (reset) state.since = 0;
  const data = await api(`/api/crawl?since=${state.since}`);
  const job = data.job;
  if (!job) {
    state.job = null;
    return null;
  }
  const fresh = job.lines;
  if (reset || !state.job || state.job.id !== job.id) {
    state.logLines = [];
    const log = $("#crawl-log");
    if (log) log.innerHTML = "";
  }
  state.job = { ...job, lines: undefined };
  state.since = job.next;
  appendLog(fresh);
  const progress = job.progress;
  if (progress.attention === "qr" && progress.qr_seconds != null) {
    if (!state.qrDeadline || state.lastQrSeconds !== progress.qr_seconds) {
      state.qrDeadline = Date.now() + progress.qr_seconds * 1000;
      state.lastQrSeconds = progress.qr_seconds;
    }
  } else {
    state.qrDeadline = null;
  }
  return job;
}

function startPolling() {
  stopPolling();
  $("#live-dot").hidden = false;
  state.pollTimer = setInterval(async () => {
    try {
      const job = await fetchJob();
      renderJob();
      if (!job || job.state !== "running") {
        stopPolling();
        await refreshOverview();
        updateStartButton();
        if (job) toast(job.state === "ok" ? "抓取完成" : job.state === "stopped" ? "抓取已停止" : "抓取结束，有问题需要看看", { error: job.state !== "ok" && job.state !== "stopped" });
      }
    } catch (error) {
      stopPolling();
      toast(error.message, { error: true });
    }
  }, 1000);
}

function stopPolling() {
  clearInterval(state.pollTimer);
  state.pollTimer = null;
  $("#live-dot").hidden = true;
}

const MAX_LOG_LINES = 1500;

function logLineHtml(line) {
  const cls = /ERROR|Traceback|Error:|失败/.test(line) ? "line err" : /Login|login|search Xiaohongshu keyword|下载完成|扫码/.test(line) ? "line hl" : "line";
  return `<div class="${cls}">${esc(line)}</div>`;
}

function appendLog(lines) {
  if (!lines || !lines.length) return;
  state.logLines.push(...lines);
  if (state.logLines.length > MAX_LOG_LINES) state.logLines.splice(0, state.logLines.length - MAX_LOG_LINES);
  const log = $("#crawl-log");
  if (!log) return;
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  log.insertAdjacentHTML("beforeend", lines.map(logLineHtml).join(""));
  while (log.childElementCount > MAX_LOG_LINES) log.firstElementChild.remove();
  if (nearBottom) log.scrollTop = log.scrollHeight;
}

function renderJob() {
  const el = $("#crawl-progress");
  if (!el) return;
  const job = state.job;
  updateStartButton();
  if (!job) {
    el.innerHTML = `
      <div class="idle">
        <div class="big">🌾</div>
        <h2>还没有进行中的抓取</h2>
        <p class="muted">开始后，这里会显示进度、需要你操作的提示（扫码、验证码）和实时日志。<br>
        MediaCrawler 会弹出一个独立的 Chrome 窗口，登录状态保存在本机。</p>
      </div>`;
    return;
  }
  const p = job.progress;
  const steps = state.overview.steps;
  const currentIndex = steps.findIndex((s) => s.id === p.step);
  const finished = job.state !== "running";
  const stateTitle = { running: "进行中", ok: "完成", degraded: "部分完成", failed: "失败", stopped: "已停止" }[job.state];
  const elapsed = ((job.finished_at ? new Date(job.finished_at) : new Date()) - new Date(job.started_at)) / 1000;
  const what = job.params.targets.length ? `指定 ${job.params.targets.length} 篇帖子` : job.params.keywords.join("、");
  const qrLeft = state.qrDeadline ? Math.max(0, Math.ceil((state.qrDeadline - Date.now()) / 1000)) : null;

  const allDone = finished && job.state === "ok";
  const stepClass = (i) => (allDone || i < currentIndex ? "done" : i === currentIndex ? "current" : "");
  const keepLog = $("#crawl-log");
  el.innerHTML = `
    <div class="card-head">
      <h2>${esc(what)}</h2>
      <span class="pill dot ${esc(job.state)}">${esc(stateTitle)}</span>
    </div>
    <div class="steps">
      ${steps.map((s, i) => `
        <div class="step ${stepClass(i)}">
          <span class="bubble">${stepClass(i) === "done" ? "✓" : i + 1}</span><span>${esc(s.title)}</span>
        </div>`).join("")}
    </div>
    ${!finished && p.attention ? `
      <div class="attention"><span class="icon">${p.attention === "qr" ? "📱" : "⚠️"}</span><span>${esc(p.attention_title)}</span>
      ${qrLeft != null ? `<span class="count" id="qr-count">${fmtDuration(qrLeft)}</span>` : ""}</div>` : ""}
    <div class="job-meta">
      <span>已保存帖子 <b>${p.notes}</b></span>
      <span>已下载图片 <b>${p.media}</b></span>
      <span>用时 <b id="elapsed">${fmtDuration(elapsed)}</b></span>
      <span>最多约 <b>${job.params.planned_notes}</b> 篇</span>
    </div>
    ${finished ? resultBox(job) : `<button class="btn danger small" data-action="stop-crawl">停止抓取</button>`}
    <div class="terminal-head"><span>实时日志</span><span class="faint">完整日志保存在工作区 runs/ 目录</span></div>
    <div class="terminal" id="crawl-log"></div>`;
  const log = $("#crawl-log");
  if (keepLog) {
    log.replaceWith(keepLog);
  } else {
    log.innerHTML = state.logLines.map(logLineHtml).join("");
    log.scrollTop = log.scrollHeight;
  }
}

function resultBox(job) {
  const o = job.outcome;
  if (job.state === "ok" || (o && o.new)) {
    const labels = o ? Object.entries(o.labels || {}).map(([k, v]) => `${labelTitle(k)} ${v}`).join("、") : "";
    return `
      <div class="result ok">
        <h3>${o ? `读到 ${o.records} 条，新增 ${o.new} 篇${o.updated ? `，更新 ${o.updated} 篇` : ""}` : "完成"}</h3>
        <p>${labels ? `新帖初筛：${esc(labels)}` : "没有新帖子需要初筛"}${job.error ? `<br>${esc(job.error)}` : ""}</p>
        <div class="actions"><a class="btn primary small" href="#posts">去看新帖子</a></div>
      </div>`;
  }
  return `
    <div class="result bad">
      <h3>${job.state === "stopped" ? "抓取已停止" : "这次没有抓到新帖子"}</h3>
      <p>${esc(job.error || "没有更多信息，可以查看下面的日志")}</p>
    </div>`;
}

/* ---------- Posts ---------- */

async function renderPosts() {
  const f = state.posts;
  const params = new URLSearchParams();
  if (f.label) params.set("label", f.label);
  if (f.packed) params.set("packed", f.packed);
  if (f.q) params.set("q", f.q);
  const data = await api(`/api/posts?${params}`);
  f.data = data;
  if (!state.overview) await refreshOverview();
  const visibleKeys = new Set(data.posts.filter((p) => !p.packed_in).map((p) => p.key));

  const view = $("#view-posts");
  const searchHadFocus = document.activeElement?.id === "post-search";
  view.innerHTML = `
    <div class="view-head">
      <div><h1>帖子</h1><p>点开一篇查看正文和图片、修改标签；勾选后可以打包交给题库。</p></div>
      <div class="head-actions">
        ${f.packed !== "yes" && visibleKeys.size ? `<button class="btn" data-action="select-all">全选当前 ${visibleKeys.size} 篇</button>` : ""}
      </div>
    </div>
    <div class="toolbar">
      <div class="filter">
        <button data-action="filter-label" data-label="" class="${!f.label ? "active" : ""}">全部<span class="n">${data.total}</span></button>
        ${LABEL_ORDER.map((id) => `<button data-action="filter-label" data-label="${id}" class="${f.label === id ? "active" : ""}">${esc(labelTitle(id))}<span class="n">${data.counts[id] || 0}</span></button>`).join("")}
      </div>
      <span class="spacer"></span>
      <div class="segmented">
        <button data-action="filter-packed" data-packed="no" class="${f.packed === "no" ? "active" : ""}">未打包</button>
        <button data-action="filter-packed" data-packed="yes" class="${f.packed === "yes" ? "active" : ""}">已打包</button>
        <button data-action="filter-packed" data-packed="" class="${!f.packed ? "active" : ""}">全部</button>
      </div>
      <input type="search" id="post-search" placeholder="搜索标题、正文、话题" value="${esc(f.q)}">
    </div>
    ${data.posts.length ? `<div class="posts">${data.posts.map(postCard).join("")}</div>` : `<div class="card empty">没有符合条件的帖子</div>`}`;
  if (searchHadFocus) {
    const input = $("#post-search");
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
  updateSelectionBar();
}

function postCard(p) {
  const selected = state.selected.has(p.key);
  const intake = p.intake === "text" ? `<span class="pill text">读正文 · ${p.body_questions} 题</span>` : `<span class="pill">看图 · ${p.images} 张</span>`;
  return `
    <article class="post-card${selected ? " selected" : ""}" data-action="open-post" data-key="${esc(p.key)}">
      <div class="thumb">${p.thumb ? `<img src="${esc(p.thumb)}" alt="" loading="lazy">` : `<div class="placeholder">${esc(p.excerpt || p.title)}</div>`}</div>
      ${p.packed_in ? `<span class="pill packed-flag">已打包</span>` : `<span class="pick" data-action="toggle-select" data-key="${esc(p.key)}" title="选择">${selected ? "✓" : ""}</span>`}
      <div class="content">
        <div class="title">${esc(p.title)}</div>
        <div class="badges"><span class="pill ${esc(p.label || "")}">${esc(p.label_title)}</span>${p.kind === "video" ? `<span class="pill">视频</span>` : intake}</div>
        <div class="meta"><span>${esc(p.source_date || "日期未知")}</span>${p.likes != null ? `<span>♥ ${p.likes}</span>` : ""}</div>
      </div>
    </article>`;
}

function updateSelectionBar() {
  const bar = $("#selection-bar");
  const count = state.selected.size;
  bar.hidden = state.view !== "posts" || count === 0;
  $("#selection-count").textContent = `已选 ${count} 篇`;
}

async function openPost(key) {
  openDrawer("加载中…", "");
  try {
    const { post } = await api(`/api/post?key=${encodeURIComponent(key)}`);
    const reasons = post.label_reasons.map((r) => `<span>${esc(r)}</span>`).join("");
    const metrics = Object.entries(post.metrics || {}).map(([k, v]) => `${{ liked_count: "赞", collected_count: "收藏", comment_count: "评论", share_count: "分享" }[k] || k} ${v}`).join(" · ");
    const imageCount = post.images.length;
    const intake = post.kind === "video"
      ? "视频帖：打包时只导入正文，视频不打包。"
      : post.intake === "text"
        ? `打包时会<b>读正文</b>导入：正文列出了 ${post.body_questions} 道题${imageCount ? `，${imageCount} 张图片不打包，也不用看` : ""}。`
        : `打包时会<b>看图</b>导入 ${imageCount} 张图片；正文只用来确认公司和轮次。`;
    const body = (post.body || "").replace(/\n[\t ]*(?=\n)/g, "");
    openDrawer(post.title, `
      <div class="intake-note">${intake}</div>
      ${post.images.length ? `<div class="section-title">图片 · ${post.images.length} 张</div>
        <div class="gallery">${post.images.map((src) => `<a href="${esc(src)}" target="_blank" rel="noopener"><img src="${esc(src)}" alt="" loading="lazy"></a>`).join("")}</div>` : ""}
      <div class="section-title">标签${post.label_source === "manual" ? " · 人工" : post.label_source === "rule" ? " · 规则初筛" : ""}</div>
      <div class="label-picker">
        ${LABEL_ORDER.map((id) => `<button class="${id}${post.label === id ? " active" : ""}" data-action="set-label" data-key="${esc(post.key)}" data-label="${id}">${esc(labelTitle(id))}</button>`).join("")}
      </div>
      ${reasons ? `<div class="section-title">命中理由</div><div class="reasons">${reasons}</div>` : ""}
      <div class="section-title">正文</div>
      <div class="body-text">${esc(body || "（正文为空）")}</div>
      <div class="section-title">信息</div>
      <dl class="kv">
        <dt>发布日期</dt><dd>${esc(post.source_date || "未知")}</dd>
        <dt>搜索关键词</dt><dd>${esc(post.keywords.join("、") || "无")}</dd>
        <dt>话题</dt><dd>${esc(post.tags.join("、") || "无")}</dd>
        <dt>互动</dt><dd>${esc(metrics || "无")}</dd>
        <dt>打包</dt><dd>${post.packed_in ? esc(post.packed_in) + (post.changed_after_pack ? "（打包后作者修改过）" : "") : "未打包"}</dd>
        <dt>帖子键</dt><dd><code>${esc(post.key)}</code></dd>
      </dl>
      <div class="drawer-actions">
        <a class="btn" href="${esc(post.open_url)}" target="_blank" rel="noopener noreferrer">打开原帖</a>
        <button class="btn ghost" data-action="copy" data-text="${esc(post.url)}">复制链接</button>
        ${post.packed_in ? "" : `<button class="btn ${state.selected.has(post.key) ? "" : "primary"}" data-action="toggle-select" data-key="${esc(post.key)}" data-refresh="drawer">${state.selected.has(post.key) ? "移出待打包" : "加入待打包"}</button>`}
      </div>`, key);
  } catch (error) {
    closeDrawer();
    toast(error.message, { error: true });
  }
}

async function setLabel(key, label) {
  try {
    await api("/api/label", { method: "POST", body: { key, label } });
    toast(`已改为「${labelTitle(label)}」`);
    await openPost(key);
    await renderPosts();
    refreshOverview();
  } catch (error) {
    toast(error.message, { error: true });
  }
}

async function packSelected() {
  const keys = [...state.selected];
  if (!keys.length) return;
  try {
    const { pack } = await api("/api/pack", { method: "POST", body: { keys } });
    state.selected.clear();
    toast(`已生成素材包：${pack.posts} 篇（读正文 ${pack.text_posts}，看图 ${pack.image_posts}）`);
    location.hash = "#packs";
    setTimeout(() => openPack(pack.pack_id), 150);
  } catch (error) {
    toast(error.message, { error: true });
  }
}

/* ---------- Packs ---------- */

async function renderPacks() {
  const { packs } = await api("/api/packs");
  $("#view-packs").innerHTML = `
    <div class="view-head">
      <div><h1>素材包</h1><p>每个素材包附带 HANDOFF.md，交给装了 interview-bank 的 Agent 导入采集题库。</p></div>
    </div>
    ${packs.length ? `<div class="grid packs-grid">${packs.map((p) => `
      <div class="card pack-card" data-action="open-pack" data-id="${esc(p.id)}">
        <div class="card-head"><h2>${p.posts} 篇帖子</h2><span class="faint">${esc(fmtTime(p.created_at))}</span></div>
        <div class="badges"><span class="pill text">读正文 ${p.text_posts}</span><span class="pill">看图 ${p.image_posts}</span></div>
        <ul>${p.titles.map((t) => `<li>${esc(t)}</li>`).join("")}${p.posts > p.titles.length ? `<li class="faint">还有 ${p.posts - p.titles.length} 篇</li>` : ""}</ul>
        <div class="id">${esc(p.id)}</div>
      </div>`).join("")}</div>` : `<div class="card empty">还没有素材包。在「帖子」里勾选帖子，然后点「打包所选」。</div>`}`;
}

async function openPack(id) {
  openDrawer("加载中…", "");
  try {
    const { pack } = await api(`/api/pack?id=${encodeURIComponent(id)}`);
    openDrawer(`素材包 · ${pack.posts.length} 篇`, `
      <div class="drawer-actions top">
        <button class="btn" data-action="copy" data-text="${esc(pack.path + "/HANDOFF.md")}">复制 HANDOFF.md 路径</button>
        <button class="btn ghost" data-action="copy" data-text="${esc(pack.path)}">复制素材包目录</button>
      </div>
      <div class="markdown">${renderMarkdown(pack.handoff)}</div>`);
  } catch (error) {
    closeDrawer();
    toast(error.message, { error: true });
  }
}

function inline(text) {
  return esc(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

function renderMarkdown(markdown) {
  const lines = markdown.split("\n");
  const out = [];
  let list = null;
  const closeList = () => {
    if (list) out.push(`</${list}>`);
    list = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith("```")) {
      closeList();
      const code = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) code.push(lines[i]);
      const text = code.join("\n");
      out.push(`<pre><button class="btn small copy" data-action="copy" data-text="${esc(text)}">复制</button><code>${esc(text)}</code></pre>`);
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    const ordered = line.match(/^\d+\.\s+(.*)$/);
    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (heading) {
      closeList();
      out.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`);
    } else if (ordered || bullet) {
      const kind = ordered ? "ol" : "ul";
      if (list !== kind) {
        closeList();
        out.push(`<${kind}>`);
        list = kind;
      }
      out.push(`<li>${inline((ordered || bullet)[1])}</li>`);
    } else if (!line.trim()) {
      closeList();
    } else {
      closeList();
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();
  return out.join("");
}

/* ---------- Drawer ---------- */

function openDrawer(title, html, key = null) {
  $("#drawer-title").textContent = title;
  $("#drawer-body").innerHTML = html;
  $("#drawer").dataset.key = key || "";
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  $("#overlay").hidden = false;
}

function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
  $("#overlay").hidden = true;
}

/* ---------- Events ---------- */

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const { action } = target.dataset;
  if (action === "toggle-select") {
    event.stopPropagation();
    const key = target.dataset.key;
    state.selected.has(key) ? state.selected.delete(key) : state.selected.add(key);
    if (target.dataset.refresh === "drawer") openPost(key);
    renderPosts();
    return;
  }
  if (target.tagName === "A") return;
  event.preventDefault();
  const handlers = {
    "open-post": () => openPost(target.dataset.key),
    "set-label": () => setLabel(target.dataset.key, target.dataset.label),
    "close-drawer": closeDrawer,
    "filter-label": () => { state.posts.label = target.dataset.label; renderPosts(); },
    "filter-packed": () => { state.posts.packed = target.dataset.packed; renderPosts(); },
    "select-all": () => { state.posts.data.posts.filter((p) => !p.packed_in).forEach((p) => state.selected.add(p.key)); renderPosts(); },
    "clear-selection": () => { state.selected.clear(); renderPosts(); },
    "pack-selected": packSelected,
    "open-pack": () => openPack(target.dataset.id),
    "copy": () => copy(target.dataset.text),
    "crawl-mode": () => { state.crawl.mode = target.dataset.mode; renderCrawlForm(); },
    "crawl-pages": () => { state.crawl.pages = Number(target.dataset.pages); renderCrawlForm(); },
    "remove-keyword": () => { state.crawl.keywords.splice(Number(target.dataset.index), 1); renderCrawlForm(); },
    "start-crawl": startCrawl,
    "stop-crawl": async () => {
      try {
        await api("/api/crawl/stop", { method: "POST", body: {} });
        toast("正在停止…");
      } catch (error) {
        toast(error.message, { error: true });
      }
    },
  };
  handlers[action]?.();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDrawer();
  if (event.target.id === "keyword-input") {
    const input = event.target;
    if ((event.key === "Enter" || event.key === "," || event.key === "，") && input.value.trim()) {
      event.preventDefault();
      addKeyword(input.value);
    } else if (event.key === "Backspace" && !input.value && state.crawl.keywords.length) {
      state.crawl.keywords.pop();
      renderCrawlForm();
      $("#keyword-input")?.focus();
    }
  }
});

let searchTimer = null;
document.addEventListener("input", (event) => {
  if (event.target.id === "url-input") {
    state.crawl.urls = event.target.value;
    updateStartButton();
  } else if (event.target.id === "post-search") {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.posts.q = event.target.value.trim(); renderPosts(); }, 250);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.id === "force-input") {
    state.crawl.force = event.target.checked;
    updateStartButton();
  }
});

document.addEventListener("focusout", (event) => {
  if (event.target.id === "keyword-input" && event.target.value.trim()) addKeyword(event.target.value);
});

$("#overlay").addEventListener("click", closeDrawer);
window.addEventListener("hashchange", () => show(currentView()));

/* Clocks that do not need the server: QR countdown, elapsed time, crawl interval. */
state.tick = setInterval(() => {
  if (state.view !== "crawl") return;
  const qr = $("#qr-count");
  if (qr && state.qrDeadline) qr.textContent = fmtDuration(Math.max(0, (state.qrDeadline - Date.now()) / 1000));
  const elapsed = $("#elapsed");
  if (elapsed && state.job && state.job.state === "running") elapsed.textContent = fmtDuration((Date.now() - new Date(state.job.started_at)) / 1000);
  if (!state.pollTimer) updateStartButton();
}, 1000);

(async function init() {
  try {
    await refreshOverview();
    if (state.overview.job && state.overview.job.state === "running") {
      await fetchJob(true);
      startPolling();
    }
  } catch (error) {
    toast(error.message, { error: true });
  }
  show(currentView());
})();
