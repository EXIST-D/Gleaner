"use strict";

/* ---------- Helpers ---------- */

const TOKEN = document.querySelector('meta[name="gleaner-token"]').content;
const VIEWS = ["overview", "crawl", "posts", "packs", "bank"];
const LABEL_ORDER = ["candidate", "collection", "promo", "unclear", "irrelevant"];
const NOWCODER_COUNTS = [5, 10, 20, 40];
const METRIC_TITLES = { liked_count: "赞", collected_count: "收藏", comment_count: "评论", share_count: "分享", view_count: "播放/浏览" };
const URL_HINTS = {
  xhs: "每行一个链接，需要带 xsec_token，从浏览器地址栏复制",
  dy: "每行一个抖音视频或图文链接，例如 https://www.douyin.com/video/…",
  bili: "每行一个 B 站视频链接或 BV 号",
  ks: "每行一个快手视频链接，例如 https://www.kuaishou.com/short-video/…",
  zhihu: "每行一个知乎回答、文章或视频链接",
  nowcoder: "每行一个牛客讨论帖或动态链接，例如 https://www.nowcoder.com/discuss/…",
};

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
  crawl: { platform: "xhs", mode: "search", keywords: null, urls: "", pages: 1, count: null, force: false },
  job: null,
  since: 0,
  logLines: [],
  pollTimer: null,
  qrDeadline: null,
  waitDeadlines: {},
  tick: null,
  posts: { label: "candidate", platform: "", packed: "no", q: "", data: null },
  bank: { data: null, topic: "", company: "", q: "", showExcluded: false },
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
    if (view === "bank") await renderBank();
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    const open = params.get("open");
    if (open && view === "posts") await openPost(open);
    if (open && view === "packs") await openPack(open);
    if (open && view === "bank") await openQuestion(open);
    if (params.get("report") && view === "bank") await openExportPreview(params.get("report"));
  } catch (error) {
    toast(error.message, { error: true });
  }
}

async function refreshOverview() {
  state.overview = await api("/api/overview");
  state.waitDeadlines = {};
  for (const p of state.overview.platforms) {
    if (p.next_crawl_in > 0) state.waitDeadlines[p.id] = Date.now() + p.next_crawl_in * 1000;
  }
  return state.overview;
}

function platformOf(id) {
  const platforms = state.overview.platforms;
  return platforms.find((p) => p.id === id) || platforms[0];
}

function platformBadge(title) {
  return title ? `<span class="pill platform">${esc(title)}</span>` : "";
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
        <p>${o.unpacked_candidates ? `其中 ${o.unpacked_candidates} 篇疑似真实面经还没打包，可以去「帖子」里挑选。` : "从「抓取」开始，按关键词收集小红书、抖音、B 站、快手、知乎和牛客上的真实面经。"}</p>
      </div>
      <div class="head-actions">
        ${o.unpacked_candidates ? `<a class="btn" href="#posts">查看待打包</a>` : ""}
        <a class="btn primary large" href="#crawl">${running ? "查看抓取进度" : "开始抓取"}</a>
      </div>
    </div>

    <div class="grid stats">
      ${statCard("帖子总数", total, platformSummary(o) || `${o.stats.runs} 次抓取或导入`)}
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

function platformSummary(o) {
  const counts = o.stats.platforms || {};
  return o.platforms.filter((p) => counts[p.id]).map((p) => `${p.title} ${counts[p.id]}`).join(" · ");
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
        <div class="title">${platformBadge(run.platform)}${esc(what)}</div>
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
      <div><h1>抓取</h1><p>牛客由 Gleaner 直接读取公开帖子，其余平台调用本机的 MediaCrawler；结束后自动去重和初筛。</p></div>
    </div>
    <div class="crawl-grid">
      <div class="card" id="crawl-form"></div>
      <div class="card" id="crawl-progress"></div>
    </div>`;
  renderCrawlForm();
  await fetchJob(true);
  renderJob();
}

function perKeyword() {
  const platform = platformOf(state.crawl.platform);
  if (!platform.page_size) return state.crawl.count ?? platform.max_notes;
  return state.crawl.pages * platform.page_size;
}

function plannedNotes() {
  if (state.crawl.mode === "detail") return urlList().length;
  return perKeyword() * state.crawl.keywords.length;
}

function urlList() {
  return state.crawl.urls.split(/\s+/).map((s) => s.trim()).filter(Boolean);
}

function waitSeconds() {
  const deadline = state.waitDeadlines[state.crawl.platform];
  return deadline ? Math.max(0, Math.ceil((deadline - Date.now()) / 1000)) : 0;
}

function amountField(platform) {
  const c = state.crawl;
  if (!platform.page_size) {
    const current = perKeyword();
    const counts = [...new Set([...NOWCODER_COUNTS, platform.max_notes])].sort((a, b) => a - b);
    return `
      <div class="segmented">
        ${counts.map((n) => `<button data-action="crawl-count" data-count="${n}" class="${current === n ? "active" : ""}">${n} 篇</button>`).join("")}
      </div>`;
  }
  const maxPages = Math.max(1, Math.floor(state.overview.config.max_notes_cap / platform.page_size));
  return `
    <div class="segmented">
      ${Array.from({ length: maxPages }, (_, i) => i + 1).map((p) => `
        <button data-action="crawl-pages" data-pages="${p}" class="${c.pages === p ? "active" : ""}">${p} 页 · ${p * platform.page_size} 篇</button>`).join("")}
    </div>`;
}

function renderCrawlForm() {
  const el = $("#crawl-form");
  if (!el) return;
  const c = state.crawl;
  const o = state.overview;
  const platform = platformOf(c.platform);
  el.innerHTML = `
    <h2>新的抓取</h2>
    <div class="field">
      <span class="label">平台</span>
      <div class="segmented platforms">
        ${o.platforms.map((p) => `<button data-action="crawl-platform" data-platform="${esc(p.id)}" class="${p.id === platform.id ? "active" : ""}">${esc(p.title)}</button>`).join("")}
      </div>
    </div>
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
        <span class="help">${platform.page_size ? "每个关键词单独搜索；关键词里不能有逗号。" : "关键词里的每个词都要出现在标题或摘要里，例如「字节 后端」。"}</span>
      </div>
      <div class="field">
        <span class="label">每个关键词抓多少</span>
        ${amountField(platform)}
        <span class="help">${esc(platform.hint)}</span>
      </div>
    ` : `
      <div class="field">
        <span class="label">帖子链接</span>
        <textarea id="url-input" placeholder="${esc(URL_HINTS[platform.id] || "每行一个链接")}">${esc(c.urls)}</textarea>
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
    const platform = platformOf(state.crawl.platform);
    note.textContent = `距离上次抓取${platform.title}不到 ${platform.min_interval_minutes} 分钟，还需等待 ${fmtDuration(wait)}`;
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
    ? { platform: c.platform, keywords: c.keywords, max_notes: perKeyword(), force: c.force }
    : { platform: c.platform, urls: urlList(), force: c.force };
  const engine = platformOf(c.platform).engine;
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
    toast(engine === "nowcoder" ? "已开始抓取" : "已开始抓取，留意弹出的 Chrome 窗口");
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
  const cls = /ERROR|Traceback|Error:|失败/.test(line) ? "line err" : /Login|login|search \w+ keyword|下载完成|扫码|已保存帖子/.test(line) ? "line hl" : "line";
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
        MediaCrawler 会弹出一个独立的 Chrome 窗口，登录状态保存在本机；牛客不需要浏览器和登录。</p>
      </div>`;
    return;
  }
  const p = job.progress;
  const steps = p.steps;
  const currentIndex = steps.findIndex((s) => s.name === p.step);
  const platformTitle = platformOf(job.params.platform || "xhs").title;
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
      <h2>${platformBadge(platformTitle)}${esc(what)}</h2>
      <span class="pill dot ${esc(job.state)}">${esc(stateTitle)}</span>
    </div>
    <div class="steps">
      ${steps.map((s, i) => `
        <div class="step ${stepClass(i)}">
          <span class="bubble">${stepClass(i) === "done" ? "✓" : i + 1}</span><span>${esc(s.title)}</span>
        </div>`).join("")}
    </div>
    ${!finished && p.attention ? `
      <div class="attention"><span class="icon">${p.attention === "qr" ? "📱" : p.attention === "throttled" ? "⏳" : "⚠️"}</span><span>${esc(p.attention_title)}</span>
      ${qrLeft != null ? `<span class="count" id="qr-count">${fmtDuration(qrLeft)}</span>` : ""}</div>` : ""}
    <div class="job-meta">
      <span>已保存帖子 <b>${p.notes}</b></span>
      ${steps.some((s) => s.name === "download") ? `<span>已下载图片 <b>${p.media}</b></span>` : ""}
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
  if (f.platform) params.set("platform", f.platform);
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
        <button data-action="filter-label" data-label="" class="${!f.label ? "active" : ""}">全部<span class="n">${f.platform ? data.platforms.find((p) => p.id === f.platform)?.count || 0 : data.total}</span></button>
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
    ${data.platforms.length > 1 || f.platform ? `
      <div class="toolbar">
        <div class="filter">
          <button data-action="filter-platform" data-platform="" class="${!f.platform ? "active" : ""}">全部平台</button>
          ${data.platforms.map((p) => `<button data-action="filter-platform" data-platform="${esc(p.id)}" class="${f.platform === p.id ? "active" : ""}">${esc(p.title)}<span class="n">${p.count}</span></button>`).join("")}
        </div>
      </div>` : ""}
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
        <div class="meta"><span>${esc(p.platform_title)}</span><span>${esc(p.source_date || "日期未知")}</span>${p.likes != null ? `<span>♥ ${p.likes}</span>` : ""}</div>
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
    const metrics = Object.entries(post.metrics || {}).map(([k, v]) => `${METRIC_TITLES[k] || k} ${v}`).join(" · ");
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
        <dt>平台</dt><dd>${esc(post.platform_title)}</dd>
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

const ESCAPABLE = /\\([\\`*_{}[\]()#+\-.!|>~])/g;

function inline(text) {
  const kept = [];
  const protectedText = text.replace(ESCAPABLE, (_, char) => `\u0000${kept.push(char) - 1}\u0001`);
  return esc(protectedText)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\u0000(\d+)\u0001/g, (_, index) => esc(kept[Number(index)]));
}

function tableCells(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function renderMarkdown(markdown, { headingIds = false } = {}) {
  const lines = markdown.split("\n");
  const out = [];
  let list = null;
  let headingCount = 0;
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
    if (line.trim().startsWith("|") && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1] || "")) {
      closeList();
      const head = tableCells(line);
      const align = tableCells(lines[i + 1]).map((cell) => (cell.endsWith(":") ? "right" : ""));
      const rows = [];
      for (i += 2; i < lines.length && lines[i].trim().startsWith("|"); i++) rows.push(tableCells(lines[i]));
      i--;
      const cell = (tag, value, index) => `<${tag}${align[index] ? ` class="num"` : ""}>${inline(value)}</${tag}>`;
      out.push(`<div class="table-wrap"><table><thead><tr>${head.map((v, k) => cell("th", v, k)).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((v, k) => cell("td", v, k)).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    const details = line.match(/^<details>(?:<summary>(.*)<\/summary>)?\s*$/);
    if (details) {
      closeList();
      out.push(`<details><summary>${inline(details[1] || "展开")}</summary>`);
      continue;
    }
    if (/^<\/details>\s*$/.test(line)) {
      closeList();
      out.push("</details>");
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    const ordered = line.match(/^\d+\.\s+(.*)$/);
    const bullet = line.match(/^[-*]\s+(.*)$/);
    const quote = line.match(/^>\s?(.*)$/);
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
      closeList();
      out.push("<hr>");
    } else if (heading) {
      closeList();
      const level = Math.min(heading[1].length, 4);
      const id = headingIds && level === 2 ? ` id="md-h-${headingCount++}"` : "";
      out.push(`<h${level}${id}>${inline(heading[2])}</h${level}>`);
    } else if (quote) {
      closeList();
      out.push(`<blockquote>${inline(quote[1])}</blockquote>`);
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

/* ---------- Bank ---------- */

async function renderBank() {
  const b = state.bank;
  const data = await api("/api/bank");
  b.data = data;
  const view = $("#view-bank");
  const intro = `<div><h1>题库</h1><p>Agent 导入采集题库后整理好的题目：按领域分组，按出现次数排序，点开可以看原文和出处。</p></div>`;
  if (!data.available) {
    view.innerHTML = `<div class="view-head">${intro}</div><div class="card empty">${esc(data.reason)}</div>`;
    return;
  }
  const topicTitle = data.topics.find((t) => t.id === b.topic)?.title;
  const scope = [b.company, topicTitle ? `${topicTitle}（含子领域）` : ""].filter(Boolean).join(" · ") || "全部题目";
  const head = `
    <div class="view-head">
      ${intro}
      <div class="head-actions export-actions">
        <button class="btn" data-action="open-exports">已导出的报告</button>
        <button class="btn primary" data-action="export-bank">导出 Markdown</button>
        <span class="faint export-scope">导出范围：${esc(scope)}</span>
      </div>
    </div>`;
  const s = data.summary;
  const text = b.q.toLowerCase();
  const visible = data.questions.filter((q) =>
    (b.showExcluded || !q.excluded)
    && (!b.topic || q.topic === b.topic)
    && (!b.company || q.companies.includes(b.company))
    && (!text || `${q.canonical}\n${q.domains.join(" ")}\n${q.companies.join(" ")}`.toLowerCase().includes(text)));
  const groups = data.topics
    .map((topic) => ({ ...topic, items: visible.filter((q) => q.topic === topic.id) }))
    .filter((group) => group.items.length);
  const orphans = visible.filter((q) => !data.topics.some((topic) => topic.id === q.topic));
  if (orphans.length) groups.push({ id: "excluded", title: "已排除", items: orphans });

  const searchHadFocus = document.activeElement?.id === "bank-search";
  view.innerHTML = `
    ${head}
    <div class="grid stats">
      ${statCard("题目", s.questions, s.excluded ? `另有 ${s.excluded} 道已排除` : "全部有效")}
      ${statCard("出现次数", s.occurrences, `${s.repeated} 道题在多篇面经里出现`, true)}
      ${statCard("公司", s.companies, data.companies.slice(0, 3).map((c) => c.name).join("、") || "暂无")}
      ${statCard("有参考答案", s.answered, s.answered ? "已附来源" : "需要时让 Agent 研究答案")}
    </div>
    <div class="toolbar">
      <div class="filter">
        <button data-action="bank-company" data-company="" class="${!b.company ? "active" : ""}">全部公司</button>
        ${data.companies.map((c) => `<button data-action="bank-company" data-company="${esc(c.name)}" class="${b.company === c.name ? "active" : ""}">${esc(c.name)}<span class="n">${c.count}</span></button>`).join("")}
      </div>
      <span class="spacer"></span>
      ${s.excluded ? `<label class="check"><input type="checkbox" id="bank-excluded" ${b.showExcluded ? "checked" : ""}> 显示已排除</label>` : ""}
      <input type="search" id="bank-search" placeholder="搜索题目、领域、公司" value="${esc(b.q)}">
    </div>
    <div class="filter topic-filter">
      <button data-action="bank-topic" data-topic="" class="${!b.topic ? "active" : ""}">全部领域<span class="n">${s.questions}</span></button>
      ${data.topics.map((t) => `<button data-action="bank-topic" data-topic="${esc(t.id)}" class="${b.topic === t.id ? "active" : ""}">${esc(t.title)}<span class="n">${t.count}</span></button>`).join("")}
    </div>
    ${groups.length ? groups.map((group) => `
      <section class="topic-group">
        <h3>${esc(group.title)}<span>${group.items.length}</span></h3>
        <div class="card q-list">${group.items.map((q, i) => questionRow(q, i + 1)).join("")}</div>
      </section>`).join("") : `<div class="card empty">${data.questions.length ? "没有符合条件的题目" : "题库还是空的。打包帖子后，把 HANDOFF.md 交给 Agent 导入，题目就会出现在这里。"}</div>`}`;
  if (searchHadFocus) {
    const input = $("#bank-search");
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
}

async function exportBank() {
  const button = document.querySelector('[data-action="export-bank"]');
  if (button) {
    button.disabled = true;
    button.textContent = "导出中…";
  }
  try {
    const { export: result } = await api("/api/bank/export", {
      method: "POST",
      body: { company: state.bank.company, topic: state.bank.topic },
    });
    toast(`已导出 ${result.questions} 道题（${result.answered} 道有参考答案）：答案版和题目版各一份`);
    await openExportPreview(result.answer_file);
  } catch (error) {
    toast(error.message, { error: true });
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "导出 Markdown";
    }
  }
}

async function openExports() {
  openDrawer("加载中…", "");
  try {
    const { exports, directory } = await api("/api/bank/exports");
    const reports = exports.filter((item) => item.edition === "答案版");
    openDrawer("已导出的报告", `
      <p class="muted">报告保存在 <code>${esc(directory)}</code>，每次导出会生成答案版和题目版两份，同名会覆盖。</p>
      ${reports.length ? `<div class="list">${reports.map((item) => `
        <button class="list-row report-row" data-action="preview-export" data-name="${esc(item.name)}">
          <span class="grow"><span class="title">${esc(item.name.replace(/\.md$/, ""))}</span>
          <span class="sub">${esc(new Date(item.modified * 1000).toLocaleString("zh-CN"))} · ${Math.max(1, Math.round(item.size / 1024))} KB</span></span>
          <span class="pill text">预览</span>
        </button>`).join("")}</div>` : `<div class="empty">还没有导出过报告</div>`}`, null, { wide: true });
  } catch (error) {
    closeDrawer();
    toast(error.message, { error: true });
  }
}

async function openExportPreview(name) {
  openDrawer("加载中…", "", null, { wide: true });
  try {
    const report = await api(`/api/bank/export-file?name=${encodeURIComponent(name)}`);
    state.preview = report;
    const body = renderMarkdown(report.markdown, { headingIds: true });
    const toc = [...report.markdown.matchAll(/^##\s+(.+)$/gm)].map((match, index) => ({ index, title: match[1] }));
    const answerName = report.edition === "答案版" ? report.name : report.pair;
    const questionName = report.edition === "题目版" ? report.name : report.pair;
    openDrawer(report.name.replace(/\.md$/, ""), `
      <div class="preview-bar">
        ${report.pair ? `<div class="segmented">
          <button class="${report.edition === "答案版" ? "active" : ""}" data-action="preview-export" data-name="${esc(answerName)}">答案版</button>
          <button class="${report.edition === "题目版" ? "active" : ""}" data-action="preview-export" data-name="${esc(questionName)}">题目版</button>
        </div>` : ""}
        <span class="spacer"></span>
        <a class="btn small" href="/bank-export/${encodeURIComponent(report.name)}" download="${esc(report.name)}">下载 .md</a>
        <button class="btn small" data-action="copy-preview">复制全文</button>
        <button class="btn small ghost" data-action="copy" data-text="${esc(report.path)}">复制路径</button>
      </div>
      ${toc.length > 1 ? `<div class="toc">${toc.map((t) => `<button data-action="scroll-heading" data-index="${t.index}">${esc(t.title.replace(/（\d+ 题）$/, ""))}</button>`).join("")}</div>` : ""}
      <article class="markdown report">${body}</article>`, null, { wide: true });
  } catch (error) {
    closeDrawer();
    toast(error.message, { error: true });
  }
}

function questionRow(q, rank) {
  const answered = q.answer_status === "source_backed" || q.answer_status === "reviewed";
  return `
    <button class="q-row${q.excluded ? " excluded" : ""}" data-action="open-question" data-id="${esc(q.id)}">
      <span class="q-rank">${rank}</span>
      <span class="q-main">
        <span class="q-text">${esc(q.canonical)}</span>
        <span class="q-meta">
          <span class="pill ${q.frequency > 1 ? "text" : ""}">出现 ${q.frequency} 次</span>
          ${q.type_title ? `<span class="pill">${esc(q.type_title)}</span>` : ""}
          ${q.rounds.map((r) => `<span class="pill">${esc(r)}</span>`).join("")}
          ${answered ? `<span class="pill ok">${esc(q.answer_title)}</span>` : ""}
          ${q.excluded ? `<span class="pill">已排除</span>` : ""}
          <span class="q-companies">${esc(q.companies.join("、"))}</span>
        </span>
      </span>
    </button>`;
}

async function openQuestion(id) {
  openDrawer("加载中…", "");
  try {
    const { question: q } = await api(`/api/bank/question?id=${encodeURIComponent(id)}`);
    const a = q.answer;
    openDrawer(q.canonical, `
      <div class="badges">
        ${q.type_title ? `<span class="pill">${esc(q.type_title)}</span>` : ""}
        ${q.domains.map((d) => `<span class="pill">${esc(d)}</span>`).join("")}
        ${q.technologies.map((t) => `<span class="pill">${esc(t)}</span>`).join("")}
        <span class="pill ${q.frequency > 1 ? "text" : ""}">出现 ${q.frequency} 次</span>
      </div>
      ${q.excluded ? `<div class="intake-note muted-note">已排除，不会出现在报告里：${esc(q.excluded)}</div>` : ""}

      <div class="section-title">参考答案${a ? ` · ${esc(a.status_title)}` : ""}</div>
      ${a ? `
        <div class="answer">
          ${shortAnswerHtml(a.short_answer)}
          ${a.key_points.length ? `<details class="answer-more"><summary>有来源的要点 · ${a.key_points.length} 条</summary><ul>${a.key_points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></details>` : ""}
          ${a.spoken_answer ? `<div class="answer-sub">口述版</div><p>${esc(a.spoken_answer)}</p>` : ""}
          ${a.follow_up_questions.length ? `<div class="answer-sub">常见追问</div><ul>${a.follow_up_questions.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}
          ${a.common_mistakes.length ? `<div class="answer-sub">易错点</div><ul>${a.common_mistakes.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}
          ${a.interviewer_intent ? `<div class="answer-sub">面试官想考察什么</div><p>${esc(a.interviewer_intent)}</p>` : ""}
          ${a.sources.length ? `<div class="answer-sub">来源</div><ul class="sources">${a.sources.map((src) => `<li><a href="${esc(src.url)}" target="_blank" rel="noopener noreferrer">${esc(src.title)}</a>${src.publisher ? ` · ${esc(src.publisher)}` : ""}</li>`).join("")}</ul>` : ""}
        </div>` : `<div class="empty-note">还没有参考答案。需要时让 Agent 用 interview-bank 为采集题库研究有来源的答案。</div>`}

      <div class="section-title">出现记录 · ${q.occurrences.length} 次</div>
      <div class="occurrences">${q.occurrences.map(occurrenceCard).join("")}</div>

      ${q.merged_variants.length ? `<div class="section-title">合并过的问法</div><ul class="plain">${q.merged_variants.map((v) => `<li>${esc(v)}</li>`).join("")}</ul>` : ""}
      ${q.relations.length ? `<div class="section-title">相关题目</div><div class="related">${q.relations.map((r) => `<button class="related-item" data-action="open-question" data-id="${esc(r.id)}">${esc(r.canonical)}</button>`).join("")}</div>` : ""}`);
  } catch (error) {
    closeDrawer();
    toast(error.message, { error: true });
  }
}

function shortAnswerHtml(text) {
  const lines = (text || "").split("\n").map((line) => line.trim()).filter(Boolean);
  if (lines.length <= 1) return lines.length ? `<p>${esc(lines[0])}</p>` : "";
  return `<ul class="short-answer">${lines.map((line) => `<li>${esc(line)}</li>`).join("")}</ul>`;
}

function occurrenceCard(o) {
  const meta = [o.company, o.round, o.interview_type, o.event_date ? `面试 ${o.event_date}` : o.source_date ? `发布 ${o.source_date}` : ""].filter(Boolean);
  return `
    <div class="occurrence">
      <div class="occ-meta">${meta.map((m) => `<span>${esc(m)}</span>`).join("")}<span class="faint">${esc(o.source_type === "image" ? "截图" : "正文")}</span></div>
      ${o.follow_up_of ? `<button class="follow-up" data-action="open-question" data-id="${esc(o.follow_up_of.id)}">追问自：${esc(o.follow_up_of.text)}</button>` : ""}
      <blockquote>${esc(o.original_text)}</blockquote>
      ${o.image ? `<a class="occ-image" href="${esc(o.image)}" target="_blank" rel="noopener"><img src="${esc(o.image)}" alt="题目所在的截图" loading="lazy"></a>` : ""}
      <div class="occ-actions">
        ${o.source_url ? `<a class="btn small" href="${esc(o.source_url)}" target="_blank" rel="noopener noreferrer">打开原帖</a>` : ""}
        ${o.post_key ? `<a class="btn small ghost" href="#posts?open=${encodeURIComponent(o.post_key)}">在帖子里查看</a>` : ""}
      </div>
    </div>`;
}

/* ---------- Drawer ---------- */

function openDrawer(title, html, key = null, { wide = false } = {}) {
  $("#drawer-title").textContent = title;
  $("#drawer-body").innerHTML = html;
  $("#drawer-body").scrollTop = 0;
  $("#drawer").dataset.key = key || "";
  $("#drawer").classList.toggle("wide", wide);
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
    "filter-platform": () => { state.posts.platform = target.dataset.platform; renderPosts(); },
    "select-all": () => { state.posts.data.posts.filter((p) => !p.packed_in).forEach((p) => state.selected.add(p.key)); renderPosts(); },
    "clear-selection": () => { state.selected.clear(); renderPosts(); },
    "pack-selected": packSelected,
    "open-pack": () => openPack(target.dataset.id),
    "open-question": () => openQuestion(target.dataset.id),
    "export-bank": exportBank,
    "open-exports": openExports,
    "preview-export": () => openExportPreview(target.dataset.name),
    "copy-preview": () => state.preview && copy(state.preview.markdown),
    "scroll-heading": () => $(`#md-h-${target.dataset.index}`)?.scrollIntoView({ behavior: "smooth", block: "start" }),
    "bank-topic": () => { state.bank.topic = target.dataset.topic; renderBank(); },
    "bank-company": () => { state.bank.company = target.dataset.company; renderBank(); },
    "copy": () => copy(target.dataset.text),
    "crawl-mode": () => { state.crawl.mode = target.dataset.mode; renderCrawlForm(); },
    "crawl-pages": () => { state.crawl.pages = Number(target.dataset.pages); renderCrawlForm(); },
    "crawl-count": () => { state.crawl.count = Number(target.dataset.count); renderCrawlForm(); },
    "crawl-platform": () => {
      state.crawl.platform = target.dataset.platform;
      state.crawl.pages = 1;
      state.crawl.count = null;
      renderCrawlForm();
    },
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
  } else if (event.target.id === "bank-search") {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.bank.q = event.target.value.trim(); renderBank(); }, 250);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.id === "force-input") {
    state.crawl.force = event.target.checked;
    updateStartButton();
  } else if (event.target.id === "bank-excluded") {
    state.bank.showExcluded = event.target.checked;
    renderBank();
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
