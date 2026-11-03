// 前端只调用公开的 API 1.0；所有服务端文本使用 textContent 插入页面。
const API_BASE = "http://127.0.0.1:8010/api/v1";
const STORAGE_KEY = "changepilot:last-thread-id";
const $ = (selector) => document.querySelector(selector);
const resultArea = $("#result-area");
const timelineArea = $("#timeline-area");
const notice = $("#notice");
const submitButton = $("#submit-button");
const repositorySelect = $("#repository-name");
const repositoryHelp = $("#repository-help");
const retryRepositories = $("#retry-repositories");
const serviceStatus = $("#service-status");
const viewLabels = { analysis: "变更分析", report: "分析报告", timeline: "分析时间线" };
let analysisInProgress = false;
let serviceReady = false;
const historyState = {
  report: { offset: 0, loading: false, requestId: 0 },
  timeline: { offset: 0, loading: false, requestId: 0 },
};
let currentDetail = null;

function showView(viewName, updateHash = false) {
  const name = Object.hasOwn(viewLabels, viewName) ? viewName : "analysis";
  for (const view of Object.keys(viewLabels)) {
    $("#view-" + view).classList.toggle("hidden", view !== name);
  }
  document.querySelectorAll("[data-view]").forEach((link) => {
    const active = link.dataset.view === name;
    link.classList.toggle("nav-active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  $("#breadcrumb-current").textContent = viewLabels[name];
  if (updateHash && window.location.hash !== `#${name}`) window.location.hash = name;
  window.scrollTo({ top: 0, behavior: "instant" });
}

function setProcessStage(stage) {
  document.querySelectorAll(".process-stage").forEach((item) => {
    item.classList.toggle("is-current", Number(item.dataset.stage) === stage);
  });
}

function syncSubmitButton() {
  const unavailable = !serviceReady || repositorySelect.disabled || !repositorySelect.value;
  submitButton.disabled = analysisInProgress || unavailable;
  submitButton.textContent = analysisInProgress ? "分析进行中…" : "开始分析";
  submitButton.setAttribute("aria-busy", String(analysisInProgress));
  submitButton.title = unavailable ? "请先连接服务并选择代码仓库" : "";
}

function element(tag, className = "", content = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = content == null ? "" : String(content);
  return node;
}

function showNotice(message, error = false) {
  notice.textContent = message;
  notice.classList.toggle("error", error);
  notice.classList.remove("hidden");
}

function clearNotice() {
  notice.classList.add("hidden");
  notice.textContent = "";
}

function formatDate(value) {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN");
}

const workflowLabels = {
  queued: "等待开始", running: "分析中", awaiting_requirement_review: "等待需求确认",
  completed: "分析完成", failed: "分析失败", rejected: "已拒绝",
};

function historyCard(item, view) {
  const card = element("button", "history-card");
  card.type = "button";
  const top = element("span", "history-card-top");
  top.append(
    element("span", `badge ${item.workflow_status || ""}`, workflowLabels[item.workflow_status] || item.workflow_status || "分析会话"),
    element("time", "", formatDate(item.updated_at)),
  );
  const title = element("strong", "history-card-title", item.request_text || "未填写需求");
  const meta = element("span", "history-card-meta", `${item.repository_name || "未知仓库"} · ${item.commit_ref || "HEAD"}`);
  const footer = element("span", "history-card-footer");
  footer.append(element("code", "", item.thread_id), element("span", "", view === "report" ? "查看报告 →" : "查看时间线 →"));
  card.append(top, title, meta, footer);
  card.addEventListener("click", async () => {
    card.disabled = true;
    try {
      await loadThread(item.thread_id, view);
      clearNotice();
    } catch (error) {
      showNotice(explainError(error), true);
    } finally {
      card.disabled = false;
    }
  });
  return card;
}

// 列表接口只取卡片摘要；完整报告与事件只在点击后读取。
async function loadHistory(view, reset = false) {
  const state = historyState[view];
  if (state.loading) return;
  if (reset) {
    state.offset = 0;
    $(`#${view}-cards`).replaceChildren();
  }
  const requestId = ++state.requestId;
  const cards = $(`#${view}-cards`);
  const more = $(`#${view}-more`);
  state.loading = true;
  more.disabled = true;
  if (!state.offset) cards.append(element("p", "history-loading", "正在读取已保存的分析…"));
  try {
    const params = new URLSearchParams({ limit: "20", offset: String(state.offset), report_only: String(view === "report") });
    const page = await api(`/analyses?${params}`);
    if (requestId !== state.requestId) return;
    cards.querySelector(".history-loading")?.remove();
    for (const item of page.items || []) cards.append(historyCard(item, view));
    state.offset += (page.items || []).length;
    more.classList.toggle("hidden", !page.has_more);
    $(`#${view}-empty`).classList.toggle("hidden", cards.children.length > 0);
    $(`#${view}-history`).classList.toggle("hidden", cards.children.length === 0);
  } catch (error) {
    cards.querySelector(".history-loading")?.remove();
    $(`#${view}-history`).classList.add("hidden");
    $(`#${view}-empty`).classList.remove("hidden");
    showNotice(`读取历史记录失败：${explainError(error)}`, true);
  } finally {
    state.loading = false;
    more.disabled = false;
  }
}

function showHistory(view) {
  currentDetail = null;
  $(`#${view === "report" ? "result" : "timeline"}-area`).classList.add("hidden");
  $(`#${view}-back`).classList.add("hidden");
  $(`#${view}-history`).classList.add("hidden");
  $(`#${view}-empty`).classList.add("hidden");
  showView(view, true);
  loadHistory(view, true);
}

function rememberThread(threadId) {
  if (!/^[0-9a-f]{32}$/.test(threadId || "")) return;
  localStorage.setItem(STORAGE_KEY, threadId);
  // 最近会话留作可点击入口，避免一进页面就把长 ID 填入输入框。
  const recentThread = $("#recent-thread");
  recentThread.dataset.threadId = threadId;
  recentThread.replaceChildren(
    element("span", "", "最近一次会话"),
    element("code", "", threadId),
    element("strong", "", "填入此会话 →"),
  );
  recentThread.classList.remove("hidden");
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: options.body ? { "Content-Type": "application/json", ...options.headers } : options.headers,
    });
  } catch {
    const error = new Error("无法连接 API。请启动后端，并确认地址为 127.0.0.1:8010。");
    error.status = 0;
    throw error;
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    const message = typeof detail === "string" ? detail : detail?.message || JSON.stringify(detail || body);
    const error = new Error(message || `请求失败：HTTP ${response.status}`);
    error.status = response.status;
    error.detail = detail;
    throw error;
  }
  return body;
}

function explainError(error) {
  if (error.status === 0) return error.message;
  if (error.status === 404 && String(error.message).includes("代码仓库不存在")) {
    return "所选代码仓库不存在。请刷新仓库列表，选择 sample_repos 中的目录名。";
  }
  if (error.status === 404 && String(error.message).includes("Git ref")) {
    return "这个 Git 快照不存在。请检查提交号、分支名，或先使用 HEAD。";
  }
  if (error.status === 503 && String(error.message).includes("PostgreSQL")) {
    return "PostgreSQL 尚未就绪。请启动数据库后重试。";
  }
  if (error.status === 422 && Array.isArray(error.detail)) {
    const first = error.detail[0];
    return `输入有误：${first?.msg || "请检查仓库、快照和需求内容。"}`;
  }
  return error.message || `请求失败（HTTP ${error.status}）。`;
}

function setServiceStatus(label, state) {
  serviceStatus.textContent = label;
  serviceStatus.className = `service-status ${state}`;
}

function setRepositories(names) {
  const previous = repositorySelect.value;
  repositorySelect.replaceChildren();
  if (!names.length) {
    const option = element("option", "", "暂无可用仓库");
    option.value = "";
    repositorySelect.append(option);
    repositorySelect.disabled = true;
    repositoryHelp.textContent = "请在项目根目录的 sample_repos 中放入一个代码仓库。";
    syncSubmitButton();
    return;
  }
  for (const name of names) {
    const option = element("option", "", name);
    option.value = name;
    repositorySelect.append(option);
  }
  repositorySelect.value = names.includes(previous) ? previous : names[0];
  repositorySelect.disabled = false;
  repositoryHelp.textContent = `找到 ${names.length} 个仓库。请选择目录名，不需要输入完整路径。`;
  syncSubmitButton();
}

async function refreshWorkspace() {
  retryRepositories.classList.add("hidden");
  serviceReady = false;
  syncSubmitButton();
  setServiceStatus("正在检查服务", "checking");
  const [ready, repositories] = await Promise.allSettled([
    api("/health/ready"),
    api("/repositories"),
  ]);
  if (repositories.status === "fulfilled") {
    setRepositories(repositories.value.repositories || []);
  } else {
    repositorySelect.replaceChildren(element("option", "", "仓库列表不可用"));
    repositorySelect.disabled = true;
    repositoryHelp.textContent = explainError(repositories.reason);
    retryRepositories.classList.remove("hidden");
  }
  if (ready.status === "fulfilled") {
    serviceReady = true;
    setServiceStatus("服务已就绪", "ready");
  } else if (ready.reason.status === 0) {
    setServiceStatus("后端未连接", "unavailable");
  } else {
    setServiceStatus("数据库未就绪", "warning");
  }
  syncSubmitButton();
}

function section(title) {
  const container = element("section", "report-section");
  container.append(element("h3", "", title));
  return container;
}

function appendLines(parent, values, emptyText = "暂无记录") {
  if (!Array.isArray(values) || values.length === 0) {
    parent.append(element("p", "", emptyText));
    return;
  }
  const list = element("ul");
  for (const value of values) list.append(element("li", "", value));
  parent.append(list);
}

const eventDescriptions = {
  "analysis.created": ["会话已创建", "需求和仓库信息已经保存。"],
  "analysis.started": ["开始分析", "系统开始读取代码快照并分析变更。"],
  "checkpoint.saved": ["阶段进度已保存", "当前分析阶段已写入数据库。"],
  "analysis.review_required": ["等待需求确认", "需求中有待确认的假设，需要人工审核后继续。"],
  "review.recorded": ["审核决定已记录", "人工审核意见已保存，流程会按该决定继续。"],
  "analysis.rejected": ["分析已拒绝", "本次需求未获批准，分析已停止。"],
  "analysis.completed": ["分析已完成", "结果已汇总为报告并保存。"],
  "analysis.failed": ["分析失败", "分析未能完成，请查看页面提示并排查原因。"],
};

function renderTimeline(context) {
  const panel = section("处理记录");
  const events = [...(context.events || [])].sort((a, b) =>
    String(a.created_at || "").localeCompare(String(b.created_at || "")));
  panel.append(element("p", "timeline-summary", `共 ${events.length} 条事件，按发生时间排列。英文标识用于与后端记录核对。`));
  if (!events.length) panel.append(element("p", "", "当前会话尚无时间线事件。"));
  for (const event of events) {
    const [title, description] = eventDescriptions[event.event_type] || ["流程事件", "系统记录了一条处理事件。"];
    const item = element("div", "timeline-item");
    const content = element("div");
    content.append(element("strong", "", title), element("span", "event-code", event.event_type));
    content.append(element("p", "", description));
    const timestamp = event.created_at ? new Date(event.created_at).toLocaleString("zh-CN") : "时间未知";
    content.append(element("p", "timeline-time", timestamp));
    item.append(content);
    panel.append(item);
  }
  return panel;
}

function renderReview(context) {
  const panel = section("需求待人工确认");
  panel.append(element("p", "", "这次分析发现需要确认的假设。请阅读需求快照后选择批准、修订或拒绝。"));
  const checkpoint = [...(context.checkpoints || [])].reverse().find((item) => item.stage.includes("requirement"));
  if (checkpoint) {
    const payload = checkpoint.payload || {};
    const summary = payload.requirement_analysis?.summary || payload.summary || payload.reason;
    if (summary) panel.append(element("p", "", summary));
    appendLines(panel, payload.requirement_analysis?.assumptions || payload.assumptions || [], "具体假设请参考分析时间线或后端上下文。 ");
  }
  const form = element("form", "review-form");
  const feedback = element("textarea");
  feedback.placeholder = "审核意见；修订和拒绝时必填";
  feedback.rows = 3;
  feedback.maxLength = 10000;
  const actor = element("input");
  actor.placeholder = "审核人标识（可选）";
  actor.maxLength = 200;
  const actions = element("div", "review-actions");
  for (const [action, label] of [["approve", "批准并继续"], ["revise", "提交修订"], ["reject", "拒绝分析"]]) {
    const button = element("button", action === "approve" ? "primary-button" : "secondary-button", label);
    button.type = "button";
    button.addEventListener("click", async () => {
      if (action !== "approve" && !feedback.value.trim()) {
        showNotice("修订或拒绝时请填写审核意见。", true);
        feedback.focus();
        return;
      }
      actions.querySelectorAll("button").forEach((item) => { item.disabled = true; });
      showNotice("正在保存审核决定并恢复分析，请稍候…");
      try {
        const updated = await api(`/analyses/${context.thread.thread_id}/reviews`, {
          method: "POST",
          body: JSON.stringify({ action, feedback: feedback.value, actor_id: actor.value || null }),
        });
        showNotice(action === "reject" ? "分析已拒绝。" : "审核已保存，分析已恢复。 ");
        renderContext(updated);
      } catch (error) {
        if (error.status === 409 && error.detail?.thread_id) {
          await loadThread(error.detail.thread_id);
          showNotice("修订后的需求仍需人工确认，请继续审核。 ");
        } else {
          showNotice(explainError(error), true);
        }
      } finally {
        actions.querySelectorAll("button").forEach((item) => { item.disabled = false; });
      }
    });
    actions.append(button);
  }
  form.append(feedback, actor, actions);
  panel.append(form);
  return panel;
}

function renderReport(report) {
  const overview = report.overview || {};
  const metrics = element("div", "report-grid");
  for (const [number, label] of [
    [overview.active_acceptance_count || 0, "验收标准"],
    [overview.finding_count || 0, "分析发现"],
    [overview.gate_count || 0, "实施门禁"],
  ]) {
    const card = element("div", "metric-card");
    card.append(element("strong", "", number), element("span", "", label));
    metrics.append(card);
  }
  resultArea.append(metrics);
  const summary = section("需求与代码证据");
  summary.append(element("p", "", report.requirement?.summary || "暂无需求摘要"));
  summary.append(element("p", "", `快照：${report.candidate_set?.commit || "—"} · 已核对文件：${report.candidate_set?.file_count ?? 0}`));
  appendLines(summary, report.candidate_set?.paths || [], "未选中候选文件。 ");
  resultArea.append(summary);
  for (const branch of report.branch_results || []) {
    const names = { impact: "代码影响", test: "测试策略", risk: "风险与依赖" };
    const panel = section(names[branch.branch] || branch.branch);
    panel.append(element("p", "", `状态：${branch.status} · 覆盖：${branch.coverage?.analyzed ?? 0}/${branch.coverage?.assigned ?? 0}`));
    if (branch.status !== "success") {
      appendLines(panel, branch.degraded_reasons || [], "该分支未完成，请查看错误详情。 ");
      appendLines(panel, (branch.errors || []).map((error) => error.message), "");
    }
    if (!(branch.findings || []).length) panel.append(element("p", "", "该分支没有产生发现。"));
    for (const finding of branch.findings || []) {
      const row = element("div", "finding");
      row.append(element("strong", "", `[${finding.priority}] ${finding.path || ""}`));
      row.append(element("p", "", finding.summary));
      panel.append(row);
    }
    resultArea.append(panel);
  }
  const gates = section("实施前需处理");
  appendLines(gates, (report.gates || []).map((gate) => gate.message || gate.summary || gate.code || JSON.stringify(gate)), "没有待解除的门禁。 ");
  resultArea.append(gates);
  if ((report.warnings || []).length) {
    const warnings = section("需留意的事项");
    appendLines(warnings, report.warnings.map((item) => item.message));
    resultArea.append(warnings);
  }
}

function renderContext(context, isDemo = false, targetView = "report") {
  currentDetail = context;
  resultArea.replaceChildren();
  timelineArea.replaceChildren();
  resultArea.classList.remove("hidden");
  timelineArea.classList.remove("hidden");
  $("#report-empty").classList.add("hidden");
  $("#timeline-empty").classList.add("hidden");
  const thread = context.thread || {};
  if (!isDemo) rememberThread(thread.thread_id);
  const header = element("section", "result-header");
  const metadata = element("div", "result-meta");
  metadata.append(element("span", `badge ${thread.workflow_status || ""}`, isDemo ? "离线示例" : thread.workflow_status || "分析结果"));
  if (context.report) metadata.append(element("span", `badge ${context.report.overview?.status || ""}`, `报告：${context.report.overview?.status || "—"}`));
  header.append(metadata, element("h2", "", context.report?.overview?.headline || (isDemo ? "订单取消功能影响分析" : "分析会话")));
  header.append(element("p", "", thread.request_text || ""));
  header.append(element("div", "session-code", isDemo ? "示例数据 · 不代表真实分析结果" : `thread_id: ${thread.thread_id || "—"}`));
  resultArea.append(header);
  if (thread.workflow_status === "awaiting_requirement_review" && !isDemo) resultArea.append(renderReview(context));
  if (context.report) renderReport(context.report);
  timelineArea.append(renderTimeline(context));
  setProcessStage(thread.workflow_status === "awaiting_requirement_review" ? 2 : 3);
  for (const view of ["report", "timeline"]) {
    $(`#${view}-history`).classList.add("hidden");
    $(`#${view}-back`).classList.remove("hidden");
  }
  if (targetView === "report") timelineArea.classList.add("hidden");
  else resultArea.classList.add("hidden");
  showView(targetView, true);
}

async function loadThread(threadId, targetView = "report") {
  const context = await api(`/analyses/${encodeURIComponent(threadId)}`);
  renderContext(context, false, targetView);
  return context;
}

$("#analysis-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  clearNotice();
  analysisInProgress = true;
  syncSubmitButton();
  setProcessStage(2);
  const startedAt = Date.now();
  const waitingMessage = () => `正在分析真实代码 · 已等待 ${Math.floor((Date.now() - startedAt) / 1000)} 秒。模型调用可能需要一些时间，并会消耗 DeepSeek Token。`;
  showNotice(waitingMessage());
  const waitingTimer = window.setInterval(() => {
    if (analysisInProgress) showNotice(waitingMessage());
  }, 1000);
  const form = new FormData(event.currentTarget);
  try {
    const response = await api("/analyses", {
      method: "POST",
      body: JSON.stringify({
        request_text: String(form.get("request_text")).trim(),
        repository_name: String(form.get("repository_name")).trim(),
        commit: String(form.get("commit")).trim(),
      }),
    });
    rememberThread(response.thread_id);
    await loadThread(response.thread_id);
    showNotice("分析完成，报告已保存。 ");
  } catch (error) {
    if (error.status === 409 && error.detail?.thread_id) {
      rememberThread(error.detail.thread_id);
      try {
        await loadThread(error.detail.thread_id);
        showNotice("分析已暂停，请审核需求假设后继续。 ");
      } catch (loadError) { showNotice(explainError(loadError), true); }
    } else {
      showNotice(explainError(error), true);
      if (error.status === 404) refreshWorkspace();
      setProcessStage(1);
    }
  } finally {
    window.clearInterval(waitingTimer);
    analysisInProgress = false;
    syncSubmitButton();
  }
});

$("#resume-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try { await loadThread($("#thread-id").value.trim()); clearNotice(); }
  catch (error) { showNotice(explainError(error), true); }
});

$("#recent-thread").addEventListener("click", (event) => {
  const threadId = event.currentTarget.dataset.threadId;
  if (!threadId) return;
  $("#thread-id").value = threadId;
  $("#thread-id").focus();
});

retryRepositories.addEventListener("click", refreshWorkspace);
$("#fill-example").addEventListener("click", () => {
  $("#request-text").value = "为订单系统增加取消订单功能。仅订单所属用户可以取消待支付订单，成功后记录审计日志。";
  $("#request-text").focus();
});

$("#demo-button").addEventListener("click", () => {
  renderContext({
    thread: { workflow_status: "completed", request_text: "仅订单所属用户可取消待支付订单；成功后记录审计日志。" },
    events: [
      { event_type: "analysis.created", created_at: "2026-01-01T09:00:00Z" },
      { event_type: "analysis.started", created_at: "2026-01-01T09:01:00Z" },
      { event_type: "analysis.completed", created_at: "2026-01-01T09:03:00Z" },
    ],
    report: {
      overview: { status: "gated", headline: "订单取消功能：建议先确认并发与审计一致性", active_acceptance_count: 4, finding_count: 3, gate_count: 1 },
      requirement: { summary: "订单取消仅允许 PENDING 状态及订单所属用户。" },
      candidate_set: { commit: "demo-snapshot", file_count: 3, paths: ["order_app/models.py", "order_app/service.py", "tests/test_service.py"] },
      branch_results: [
        { branch: "impact", status: "success", coverage: { analyzed: 2, assigned: 2 }, findings: [{ priority: "major", path: "order_app/service.py", summary: "新增 cancel_order，校验归属和支付状态。" }] },
        { branch: "test", status: "success", coverage: { analyzed: 1, assigned: 1 }, findings: [{ priority: "major", path: "tests/test_service.py", summary: "补充成功、越权、重复取消与审计失败测试。" }] },
        { branch: "risk", status: "success", coverage: { analyzed: 2, assigned: 2 }, findings: [{ priority: "minor", path: "order_app/service.py", summary: "支付回调与取消可能并发，需要确认状态更新策略。" }] },
      ],
      gates: [{ message: "实施前确认取消与支付并发时的状态一致性规则。" }],
    },
  }, true);
  showNotice("当前展示离线示例；点击“开始分析”可调用真实后端。 ");
});

document.querySelectorAll("[data-view]").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    if (link.dataset.view === "analysis") showView("analysis", true);
    else showHistory(link.dataset.view);
  });
});
for (const view of ["report", "timeline"]) {
  $(`#${view}-back`).addEventListener("click", () => showHistory(view));
  $(`#${view}-more`).addEventListener("click", () => loadHistory(view));
}
document.querySelectorAll("[data-refresh-history]").forEach((button) => {
  button.addEventListener("click", () => loadHistory(button.dataset.refreshHistory, true));
});
document.querySelectorAll("[data-view-target]").forEach((button) => {
  button.addEventListener("click", () => showView(button.dataset.viewTarget, true));
});
window.addEventListener("hashchange", () => {
  const view = window.location.hash.slice(1);
  if (view === "report" || view === "timeline") {
    if (currentDetail) showView(view);
    else showHistory(view);
  } else showView(view);
});

const recent = localStorage.getItem(STORAGE_KEY);
if (recent) rememberThread(recent);
const initialView = window.location.hash.slice(1);
if (initialView === "report" || initialView === "timeline") showHistory(initialView);
else showView(initialView);
syncSubmitButton();
refreshWorkspace();
