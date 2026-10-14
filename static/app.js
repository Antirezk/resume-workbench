"use strict";
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="app-token"]').content;
const state = { files: [], job: null, filter: "all", detail: null, polling: null, busy: false, ready: false, maxFiles: 50, resumeCount: null, counting: false, countError: "", countRevision: 0 };
Object.assign(state,{view:"batch",library:null,libraryPage:1,libraryDeleted:false,libraryRequest:0});
const statusLabels = { done: "已整理", failed: "未成功", duplicate: "重复文件", queued: "等待中", processing: "处理中", canceled: "已停止" };
const presets = {
  qwen: { base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  "qwen-sg": { base_url: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  deepseek: { base_url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  custom: { base_url: "http://127.0.0.1:11434/v1", model: "" }
};
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeRemarkColor = value => /^#[0-9a-f]{6}$/i.test(value || "") ? value.toLowerCase() : "#18594c";

function remarkFields(prefix, row) {
  const color = safeRemarkColor(row.remark_color);
  const palette = [["红色","#b33a32"],["绿色","#18594c"],["橙色","#a86513"],["蓝色","#285d9b"],["紫色","#7b4a9e"],["灰色","#6a7681"]];
  return `<label class="full-field">短备注（显示在姓名下方）<input id="${prefix}Short" data-remark-prefix="${prefix}" maxlength="50" placeholder="例如：不符合、待联系、已约面试" value="${esc(row.short_remark || "")}"></label><div class="remark-color-toolbar"><label class="remark-color-control">字体颜色<input id="${prefix}Color" data-remark-prefix="${prefix}" type="color" value="${color}" aria-label="短备注字体颜色"></label><div class="remark-color-presets">${palette.map(([label,hex]) => `<button type="button" class="remark-color-swatch" data-color-prefix="${prefix}" data-color="${hex}" style="background-color:${hex}" aria-label="${label}" title="${label}"></button>`).join("")}</div></div><p class="remark-live-preview">预览：<span id="${prefix}Preview" style="color:${color}">${esc(row.short_remark || "短备注预览")}</span></p><label class="full-field">详细备注（点击后查看）<textarea id="${prefix}Detail" rows="5" maxlength="5000" placeholder="填写沟通情况、面试安排和后续跟进事项…">${esc(row.remark || "")}</textarea></label>`;
}
function collectRemarks(prefix) {
  return {short_remark:$(prefix + "Short").value,remark_color:safeRemarkColor($(prefix + "Color").value),remark:$(prefix + "Detail").value};
}
function previewRemark(prefix) {
  $(prefix + "Preview").textContent = $(prefix + "Short").value || "短备注预览";
  $(prefix + "Preview").style.color = safeRemarkColor($(prefix + "Color").value);
}
for (const id of ["remarkForm","editFields"]) {
  $(id).addEventListener("input", event => {
    if (event.target.dataset.remarkPrefix) previewRemark(event.target.dataset.remarkPrefix);
  });
  $(id).addEventListener("click", event => {
    const button = event.target.closest("[data-color-prefix]");
    if (!button) return;
    $(button.dataset.colorPrefix + "Color").value = button.dataset.color;
    previewRemark(button.dataset.colorPrefix);
  });
}

async function api(path, options = {}) {
  options.headers = { "X-App-Token": token, ...options.headers };
  if (options.body && !(options.body instanceof FormData)) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  let response;
  try { response = await fetch(path, options); }
  catch { throw new Error("无法连接本地工具，请检查启动窗口是否仍在运行"); }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || "操作未完成，请稍后重试");
  }
  return response;
}
let toastTimer;
function toast(message, error = false) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").className = "toast" + (error ? " error" : "");
  $("toast").hidden = false;
  toastTimer = setTimeout(() => $("toast").hidden = true, 6000);
}
function handle(fn) { return async event => { try { await fn(event); } catch (error) { toast(error.message, true); } }; }
function settings() {
  return { base_url: $("baseUrl").value.trim(), model: $("model").value.trim(), api_key: $("apiKey").value.trim(),
    json_mode: $("jsonMode").checked,
    use_ocr: $("useOcr").checked, force_ocr: $("forceOcr").checked };
}
function openSettings() { $("settingsDialog").showModal(); }
function connectionStatus() {
  const configured = state.ready || $("apiKey").value.trim() || $("baseUrl").value.startsWith("http://127.0.0.1");
  $("connectionText").textContent = configured ? $("model").value + " · 已配置" : "配置模型 API";
  $("settingsStatus").querySelector(".live-dot").classList.toggle("muted", !configured);
}
$("openSettings").addEventListener("click", openSettings);
$("settingsStatus").addEventListener("click", openSettings);
document.querySelector(".close-settings").addEventListener("click", () => $("settingsDialog").close());
$("provider").addEventListener("change", () => {
  const preset = presets[$("provider").value];
  $("baseUrl").value = preset.base_url; $("model").value = preset.model;
  $("testResult").textContent = "";
});
$("settingsForm").addEventListener("submit", event => {
  event.preventDefault();
  const config = settings(); delete config.api_key;
  try { localStorage.setItem("resumeSettings", JSON.stringify(config)); } catch {}
  connectionStatus(); $("settingsDialog").close(); toast("设置已保存，API Key 仅保留在当前页面");
});
$("testApi").addEventListener("click", handle(async () => {
  $("testApi").disabled = true; $("testResult").className = "test-result";
  $("testResult").textContent = "正在测试连接…";
  try {
    const result = await (await api("/api/test", { method: "POST", body: settings() })).json();
    $("testResult").textContent = result.message + "，模型：" + result.model;
  } catch (error) {
    $("testResult").textContent = error.message; $("testResult").className = "test-result error";
  } finally { $("testApi").disabled = false; }
}));
$("useOcr").addEventListener("change", () => { if (!$("useOcr").checked) $("forceOcr").checked = false; });
$("forceOcr").addEventListener("change", () => { if ($("forceOcr").checked) $("useOcr").checked = true; });

function addFiles(files) {
  const accepted = /\.(pdf|docx|txt|png|jpe?g|zip)$/i;
  let rejected = [];
  for (const file of files) {
    if (!accepted.test(file.name)) { rejected.push(file.name); continue; }
    if (file.size > (file.name.toLowerCase().endsWith(".zip") ? 120 : 20) * 1024 * 1024) { rejected.push(file.name + "（过大）"); continue; }
    if (!state.files.some(item => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified)) {
      state.files.push(file);
    }
  }
  if (rejected.length) toast("未添加：" + rejected.slice(0, 4).join("、"), true);
  inspectFiles();
}
let countTimer, countController;
function inspectFiles() {
  const revision = ++state.countRevision;
  clearTimeout(countTimer); countController?.abort();
  state.resumeCount = null; state.countError = ""; state.counting = !!state.files.length;
  const size = state.files.reduce((sum, file) => sum + file.size, 0);
  if (size > 120 * 1024 * 1024) { state.countError = "所选文件总大小超过 120 MB，请移除部分文件"; state.counting = false; }
  renderFiles();
  if (!state.counting) return;
  countTimer = setTimeout(async () => {
    const form = new FormData();
    for (const file of state.files) form.append("files", file);
    const controller = new AbortController(); countController = controller;
    try {
      const result = await (await api("/api/uploads/inspect", { method: "POST", body: form, signal: controller.signal })).json();
      if (revision !== state.countRevision) return;
      state.resumeCount = result.resume_count;
      if (!result.within_limit) state.countError = `自动识别 ${result.resume_count} 份简历，每批最多 ${result.max_files} 份，请移除部分文件或拆分 ZIP`;
    } catch (error) {
      if (revision !== state.countRevision || controller.signal.aborted) return;
      state.countError = error.message;
    } finally {
      if (revision === state.countRevision) { state.counting = false; renderFiles(); }
    }
  }, 150);
}
function renderFiles() {
  const size = state.files.reduce((sum, file) => sum + file.size, 0);
  $("fileCount").textContent = !state.files.length ? "尚未选择文件" : state.counting ? "正在自动统计简历数量…" : state.resumeCount != null ? `自动识别 ${state.resumeCount} 份简历 · ${(size / 1024 / 1024).toFixed(1)} MB` : "数量识别未完成";
  $("uploadValidation").textContent = state.countError;
  $("uploadValidation").hidden = !state.countError;
  $("fileList").hidden = !state.files.length;
  $("fileList").innerHTML = state.files.map((file, i) => `<div class="file-chip"><span>${esc(file.name)}</span><button data-remove-file="${i}" aria-label="移除 ${esc(file.name)}">×</button></div>`).join("");
  $("start").disabled = !state.files.length || state.busy || state.counting || !!state.countError || state.resumeCount == null || state.resumeCount > state.maxFiles || size > 120 * 1024 * 1024 || !!(state.job && state.job.status !== "completed");
}
$("fileList").addEventListener("click", event => {
  const button = event.target.closest("[data-remove-file]");
  if (button) { state.files.splice(Number(button.dataset.removeFile), 1); inspectFiles(); }
});
$("dropzone").addEventListener("click", () => $("files").click());
$("dropzone").addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); $("files").click(); } });
$("files").addEventListener("change", event => { addFiles(event.target.files); event.target.value = ""; });
for (const name of ["dragover", "dragenter"]) $("dropzone").addEventListener(name, event => { event.preventDefault(); $("dropzone").classList.add("dragging"); });
$("dropzone").addEventListener("dragleave", () => $("dropzone").classList.remove("dragging"));
$("dropzone").addEventListener("drop", event => { event.preventDefault(); $("dropzone").classList.remove("dragging"); addFiles(event.dataTransfer.files); });

$("start").addEventListener("click", handle(async () => {
  showView("batch");
  if (!state.ready && !$("apiKey").value.trim() && !$("baseUrl").value.startsWith("http://127.0.0.1") && !$("baseUrl").value.startsWith("http://localhost")) {
    openSettings(); toast("填写模型 API Key 后即可开始整理"); return;
  }
  state.busy = true; renderFiles(); $("start").textContent = "正在上传…";
  try {
    const form = new FormData();
    for (const file of state.files) form.append("files", file);
    form.append("config", JSON.stringify(settings())); form.append("title", $("batchTitle").value);
    const result = await (await api("/api/jobs", { method: "POST", body: form })).json();
    state.files = []; inspectFiles(); $("batchTitle").value = "";
    await loadJob(result.id); await loadHistory();
    toast("批次已开始，可查看进度与整理结果");
  } finally { state.busy = false; $("start").textContent = "开始整理 →"; renderFiles(); }
}));
$("demo").addEventListener("click", handle(async () => {
  showView("batch");
  $("demo").disabled = true;
  try {
    const result = await (await api("/api/demo", { method: "POST", body: {} })).json();
    await loadJob(result.id); await loadHistory();
  } finally { $("demo").disabled = false; }
}));
async function loadHistory() {
  const result = await (await api("/api/jobs")).json();
  $("history").innerHTML = result.jobs.length ? result.jobs.map(job => `<button data-job="${job.id}" class="${state.job?.id === job.id ? "selected" : ""}">${esc(job.title)}<small>${esc(job.created.slice(0, 10))}${job.is_demo ? " · 演示" : job.status === "completed" ? " · 已完成" : " · 处理中"}</small></button>`).join("") : '<p class="empty-history">还没有处理记录</p>';
}
$("history").addEventListener("click", handle(async event => {
  const button = event.target.closest("[data-job]");
  if (button) { showView("batch"); await loadJob(button.dataset.job); await loadHistory(); }
}));
$("refreshHistory").addEventListener("click", handle(loadHistory));
$("newBatch").addEventListener("click", () => {
  showView("batch");
  if (state.job) loadJob(state.job.id).catch(error => toast(error.message,true));
  $("uploadCard").scrollIntoView({ behavior: "smooth", block: "start" }); $("batchTitle").focus();
});
function showView(view) {
  state.view = view;
  $("batchWorkspace").hidden = view !== "batch";
  $("databaseSection").hidden = view !== "library";
  $("newBatch").classList.toggle("active",view === "batch");
  $("openDatabase").classList.toggle("active",view === "library");
}
async function refreshLibraryCount() {
  const counts = await (await api("/api/talent/counts")).json();
  $("databaseNavCount").textContent = counts.total;
}
async function loadLibrary() {
  const revision = ++state.libraryRequest;
  const query = new URLSearchParams({q:$("librarySearch").value.trim(),degree:$("libraryDegree").value,page:String(state.libraryPage),deleted:state.libraryDeleted ? "1" : "0"});
  const result = await (await api("/api/talent?" + query)).json();
  if (revision !== state.libraryRequest) return;
  state.library = result; state.libraryPage = result.page;
  $("databaseNavCount").textContent = result.total;
  $("libraryTotal").textContent = result.total; $("libraryMatched").textContent = result.matched;
  $("libraryManual").textContent = result.manual; $("libraryDeleted").textContent = result.deleted_count;
  $("libraryPath").textContent = result.database_path;
  $("libraryShown").textContent = `当前显示 ${result.rows.length} / ${result.matched} 条`;
  $("libraryPageLabel").textContent = `第 ${result.page} / ${result.pages} 页 · 每页 ${result.page_size} 条`;
  $("libraryPrev").disabled = result.page <= 1; $("libraryNext").disabled = result.page >= result.pages;
  $("libraryActiveTab").classList.toggle("active",!state.libraryDeleted);
  $("libraryTrashTab").classList.toggle("active",state.libraryDeleted);
  $("libraryExportXlsx").disabled = $("libraryExportPdf").disabled = state.libraryDeleted || !result.matched;
  $("libraryRows").innerHTML = result.rows.length ? result.rows.map(libraryCandidateRow).join("") : '<tr><td colspan="6" class="empty-row">' + (state.libraryDeleted ? '回收站暂无候选人' : '暂无匹配的候选人。上传真实简历分析后会自动入库，也可以手动新增。') + '</td></tr>';
}
function libraryCandidateRow(row) {
  const data=row.data,info=row.derived;
  const education=data.education.slice(0,3).map(item => `<div class="library-education"><strong>${esc(item.level || "阶段未明确")}</strong> ${esc(item.school || "院校未提供")}<small>${esc(item.major || "专业未提供")}${item.graduation_status ? " · " + esc(item.graduation_status) : ""}</small></div>`).join("");
  const experience=data.experience.slice(0,2).map(item => `<div class="library-experience"><small>${esc(item.employment_type)}</small>${esc(item.company || "公司未提供")}<span>${esc(item.title || "岗位未提供")}</span></div>`).join("");
  return `<tr><td><button class="library-name" data-detail="${row.id}">${esc(data.name || "未提供姓名")}</button>${row.short_remark ? `<span class="candidate-short-remark" style="color:${safeRemarkColor(row.remark_color)}">${esc(row.short_remark)}</span>` : ""}<span class="candidate-source" title="${esc(row.filename)}">${esc(row.filename)}</span><span class="library-source">${esc(row.source_title || "手动录入")}<br>${esc(row.created.slice(0,10))}</span>${!row.deleted_at ? `<button class="row-button remark-shortcut" data-remark="${row.id}">${row.remark ? "查看详细备注" : row.short_remark ? "编辑备注" : "+ 备注"}</button>` : ""}</td><td class="library-contact">${esc(data.phone || "电话未提供")}<br>${esc(data.email || "邮箱未提供")}<small>${esc(data.location || "城市未提供")}${data.target_role ? " · " + esc(data.target_role) : ""}</small></td><td>${education || "未提供教育经历"}${data.education.length > 3 ? `<small class="library-extra">另有 ${data.education.length-3} 段，点击档案查看</small>` : ""}</td><td>${experience || "未提供任职经历"}${data.experience.length > 2 ? `<small class="library-extra">另有 ${data.experience.length-2} 段，点击档案查看</small>` : ""}</td><td class="library-years">工作 ${info.experience_years == null ? "未能计算" : info.experience_years+"年"}${info.years_partial ? '<small>仅已知经历</small>' : ""}<small>实习 ${info.internship_months == null ? "未能计算" : info.internship_months+"个月"}${info.internship_partial ? "（仅已知）" : ""}</small>${info.warnings.length ? `<span class="status-tag review">核对 ${info.warnings.length} 项</span>` : ""}</td><td><div class="library-actions"><button class="row-button" data-detail="${row.id}">${row.deleted_at ? "查看" : "查看 / 编辑"}</button>${row.deleted_at ? `<button class="row-button" data-restore-talent="${row.id}">恢复</button>` : `<button class="row-button danger" data-delete-talent="${row.id}">删除</button>`}</div></td></tr>`;
}
$("openDatabase").addEventListener("click",handle(async () => {showView("library");await loadLibrary();}));
$("libraryRefresh").addEventListener("click",handle(loadLibrary));
for (const [id,deleted] of [["libraryActiveTab",false],["libraryTrashTab",true]]) $(id).addEventListener("click",handle(async () => {state.libraryDeleted=deleted;state.libraryPage=1;await loadLibrary();}));
$("libraryPrev").addEventListener("click",handle(async () => {state.libraryPage--;await loadLibrary();}));
$("libraryNext").addEventListener("click",handle(async () => {state.libraryPage++;await loadLibrary();}));
let librarySearchTimer;
$("librarySearch").addEventListener("input",() => {clearTimeout(librarySearchTimer);state.libraryPage=1;librarySearchTimer=setTimeout(() => loadLibrary().catch(error => toast(error.message,true)),250);});
$("libraryDegree").addEventListener("change",handle(async () => {state.libraryPage=1;await loadLibrary();}));
$("libraryAdd").addEventListener("click",handle(async () => {
  state.detail = await (await api("/api/talent/template")).json();
  Object.assign(state.detail,{origin:"talent",isNew:true,id:null});
  renderDetail(); $("detailDialog").showModal();
}));
async function exportLibrary(format) {
  const button = $(format === "xlsx" ? "libraryExportXlsx" : "libraryExportPdf"), label = button.textContent;
  button.disabled=true;button.textContent="生成中…";
  try {
    const query = new URLSearchParams({q:$("librarySearch").value.trim(),degree:$("libraryDegree").value});
    const response = await api(`/api/talent/export/${format}?${query}`);
    const blob = await response.blob(), url = URL.createObjectURL(blob), anchor = document.createElement("a");
    anchor.href=url;anchor.download=`候选人数据库.${format}`;document.body.appendChild(anchor);anchor.click();anchor.remove();setTimeout(() => URL.revokeObjectURL(url),60000);
    toast("已导出数据库中当前筛选的全部候选人，包含所有分页");
  } finally {button.disabled=state.libraryDeleted || !state.library?.matched;button.textContent=label;}
}
$("libraryExportXlsx").addEventListener("click",handle(() => exportLibrary("xlsx")));
$("libraryExportPdf").addEventListener("click",handle(() => exportLibrary("pdf")));
async function loadJob(id) {
  clearTimeout(state.polling);
  state.job = await (await api("/api/jobs/" + id)).json();
  renderJob();
  await refreshLibraryCount();
  if (state.view === "library") await loadLibrary();
  if (state.job.status !== "completed") {
    state.polling = setTimeout(async () => {
      try { await loadJob(id); if (state.job.status === "completed") { await loadHistory(); toast("批次整理已完成"); } }
      catch (error) { toast(error.message, true); }
    }, 1800);
  }
}
function renderJob() {
  const job = state.job, stats = job.stats, running = job.status !== "completed";
  $("batchSection").hidden = false; $("emptyGuide").hidden = true;
  $("currentTitle").textContent = job.title;
  $("batchMeta").textContent = `${job.created.slice(0, 16).replace("T", " ")} · ${job.model}${running ? " · 处理中" : " · 已完成"}`;
  $("demoBanner").hidden = !job.is_demo;
  $("statTotal").textContent = stats.total; $("tabTotal").textContent = stats.total;
  $("statDone").textContent = stats.done; $("statReview").textContent = stats.review;
  $("statIssues").textContent = stats.failed + stats.duplicates + job.rows.filter(r => r.status === "canceled").length;
  $("statSuccessNote").textContent = stats.done ? "可预览并导出结果" : running ? "等待模型返回" : "可重试未成功文件";
  $("statIssueNote").textContent = `失败 ${stats.failed} · 重复 ${stats.duplicates}`;
  $("cancel").hidden = !running; $("cancel").disabled = job.status === "canceling";
  $("cancel").textContent = job.status === "canceling" ? "正在停止…" : "停止处理";
  $("retry").hidden = running || !job.rows.some(r => ["failed", "canceled"].includes(r.status)) || job.is_demo;
  $("updateEducation").hidden = running || !stats.done || !!job.is_demo;
  $("supplement").hidden = running || !stats.done || !!job.is_demo;
  $("deleteBatch").disabled = running;
  $("exportXlsx").disabled = running; $("exportPdf").disabled = running;
  $("progressPanel").hidden = !running;
  const completed = job.rows.filter(r => !["queued", "processing"].includes(r.status)).length;
  $("progressLabel").textContent = job.status === "canceling" ? "正在停止，等待已发出的请求完成" : "正在读取与整理简历";
  $("progressCount").textContent = `${completed} / ${stats.total}`;
  $("progressBar").style.width = (stats.total ? completed / stats.total * 100 : 0) + "%";
  $("tokenCount").textContent = job.tokens ? `模型用量 ${job.tokens.toLocaleString()} tokens` : "导出包含整批文件";
  renderRows(); renderFiles();
}
function filteredRows() {
  const search = $("search").value.trim().toLocaleLowerCase(), degree = $("degreeFilter").value;
  return state.job.rows.filter(row => {
    if (state.filter === "done" && row.status !== "done") return false;
    if (state.filter === "review" && !(row.status === "done" && row.derived.warnings.length)) return false;
    if (state.filter === "issues" && !["failed", "duplicate", "canceled"].includes(row.status)) return false;
    if (degree && row.derived?.highest_degree !== degree) return false;
    return !search || (JSON.stringify(row.data || {}) + row.filename + row.error + (row.remark || "") + (row.short_remark || "")).toLocaleLowerCase().includes(search);
  });
}
function renderRows() {
  if (!state.job) return;
  const rows = filteredRows();
  $("shownCount").textContent = `显示 ${rows.length} / ${state.job.rows.length} 份`;
  $("rows").innerHTML = rows.length ? rows.map(row => candidateRow(row)).join("") : '<tr><td colspan="9" class="empty-row">没有符合条件的文件</td></tr>';
}
function candidateRow(row, library=false) {
    const data = row.data || {}, info = row.derived || {}, warnings = info.warnings?.length;
    const years = info.experience_years == null ? "未能计算" : `${info.experience_years} 年`;
    const status = row.status === "done" && warnings ? "review" : row.status;
    const label = row.deleted_at ? "回收站" : status === "review" ? "需核对" : library ? (row.source_row_id ? "已入库" : "手动录入") : statusLabels[row.status] || row.status;
    return `<tr><td><div class="candidate-identity"><span class="avatar">${esc(data.name?.slice(0, 1) || "文")}</span><div><strong>${esc(data.name || (row.status === "done" ? "未提供姓名" : "文件记录"))}</strong>${row.short_remark ? `<span class="candidate-short-remark" style="color:${safeRemarkColor(row.remark_color)}">${esc(row.short_remark)}</span>` : ""}<span class="candidate-source" title="${esc(row.filename)}">${esc(row.filename)}</span>${library ? `<span class="library-source">来源：${esc(row.source_title || "手动录入")}<br>${esc(row.created.slice(0,10))}</span>` : ""}${row.status === "done" && !row.deleted_at ? `<button class="row-button remark-shortcut" data-remark="${row.id}">${row.remark ? "查看详细备注" : row.short_remark ? "编辑备注" : "+ 备注"}</button>` : ""}</div></div></td>
      <td>${esc(data.location || "—")}</td><td>${esc(info.highest_degree || "—")}</td><td class="education-column">${(data.education || []).map(item => `<div class="education-line"><strong>${esc(item.level || item.degree || "阶段未提供")}</strong><span>${esc(item.school || "院校未提供")}</span><small>${esc(item.major || "专业未提供")}${item.graduation_status ? " · " + esc(item.graduation_status) : ""}</small></div>`).join("") || "—"}</td><td>${row.status === "done" ? esc("工作 " + years) + `<small class="year-note">实习 ${info.internship_months == null ? "未能计算" : info.internship_months + "个月"}${info.internship_partial ? "（仅已知）" : ""}</small>` : "—"}${info.years_partial ? '<small class="year-note">仅已知经历</small>' : ""}</td>
      <td class="career-column">${(data.experience || []).map((item, i) => `<div class="education-line"><strong>${esc(item.employment_type || (item.is_internship ? "实习" : "工作"))} · ${esc(item.company || "公司未提供")}</strong><span>${esc(item.title || "岗位未提供")}</span><small>${esc(item.start_date || "日期未提供")} — ${esc(item.end_date || "日期未提供")} · ${esc(info.experience_details?.[i]?.label || "未能计算")}</small></div>`).join("") || "—"}</td>
      <td>${row.status === "failed" || row.status === "canceled" ? `<div class="row-error">${esc(row.error)}</div>` : (data.skills || []).slice(0, 4).map(skill => `<span class="skill-tag">${esc(skill)}</span>`).join("") + ((data.skills?.length || 0) > 4 ? `<span class="skill-tag">+${data.skills.length - 4}</span>` : "") || "—"}</td>
      <td><span class="status-tag ${row.deleted_at ? "library-removed" : status}">${esc(label)}</span></td><td>${row.status === "done" ? (library ? `<div class="library-actions"><button class="row-button" data-detail="${row.id}">${row.deleted_at ? "查看" : "查看 / 编辑"}</button>${row.deleted_at ? `<button class="row-button" data-restore-talent="${row.id}">恢复</button>` : `<button class="row-button danger" data-delete-talent="${row.id}">删除</button>`}</div>` : `<button class="row-button" data-detail="${row.id}">查看 ↗</button>`) : ""}</td></tr>`;
}
document.querySelectorAll("[data-filter]").forEach(button => button.addEventListener("click", () => {
  state.filter = button.dataset.filter;
  document.querySelectorAll("[data-filter]").forEach(item => item.classList.toggle("active", item === button)); renderRows();
}));
$("search").addEventListener("input", renderRows); $("degreeFilter").addEventListener("change", renderRows);
$("cancel").addEventListener("click", handle(async () => {
  await api(`/api/jobs/${state.job.id}/cancel`, { method: "POST", body: {} }); await loadJob(state.job.id);
}));
$("retry").addEventListener("click", handle(async () => {
  if (!state.ready && !$("apiKey").value.trim() && !$("baseUrl").value.startsWith("http://")) { openSettings(); return; }
  await api(`/api/jobs/${state.job.id}/retry`, { method: "POST", body: settings() }); await loadJob(state.job.id);
}));
$("updateEducation").addEventListener("click", handle(async () => {
  if (!state.ready && !$("apiKey").value.trim() && !$("baseUrl").value.startsWith("http://")) { openSettings(); return; }
  await api(`/api/jobs/${state.job.id}/education`, { method: "POST", body: settings() });
  await loadJob(state.job.id); toast("正在重新提取完整教育经历，其他已修改的候选人信息会保留");
}));
$("supplement").addEventListener("click", handle(async () => {
  if (!state.ready && !$("apiKey").value.trim() && !$("baseUrl").value.startsWith("http://")) { openSettings(); return; }
  await api(`/api/jobs/${state.job.id}/supplement`, { method: "POST", body: settings() });
  await loadJob(state.job.id); toast("正在从原文补全经历与求职信息；人工修改的已有记录会保留，请核对补全结果");
}));
$("deleteBatch").addEventListener("click", handle(async () => {
  if (!confirm("删除本批次及原始简历文件？已入库的候选人信息和原文文字仍会保留在数据库。批次与原文件删除无法恢复。")) return;
  await api(`/api/jobs/${state.job.id}`, { method: "DELETE" });
  state.job = null; clearTimeout(state.polling); $("batchSection").hidden = true; $("emptyGuide").hidden = false;
  await loadHistory(); renderFiles(); toast("批次已删除");
}));
async function download(format) {
  const button = $(format === "xlsx" ? "exportXlsx" : "exportPdf"), original = button.textContent;
  button.disabled = true; button.textContent = "生成中…";
  try {
    const response = await api(`/api/jobs/${state.job.id}/export/${format}`);
    const blob = await response.blob(), url = URL.createObjectURL(blob), anchor = document.createElement("a");
    anchor.href = url; anchor.download = (format === "xlsx" ? "简历整理" : "简历汇总报告") + (state.job.is_demo ? "_演示" : "") + "." + format;
    document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000);
    toast("文件已生成，包含本批次全部结果");
  } finally { button.disabled = false; button.textContent = original; }
}
$("exportXlsx").addEventListener("click", handle(() => download("xlsx")));
$("exportPdf").addEventListener("click", handle(() => download("pdf")));

function bindCandidateTable(id, library=false) {
$(id).addEventListener("click", handle(async event => {
  const remove = event.target.closest("[data-delete-talent]"), restore = event.target.closest("[data-restore-talent]");
  if (library && (remove || restore)) {
    if (remove && !confirm("将这位候选人移到回收站？可以恢复，原批次记录会保留。")) return;
    await api(`/api/talent/${remove ? remove.dataset.deleteTalent : restore.dataset.restoreTalent}${restore ? "/restore" : ""}`,{method:remove ? "DELETE" : "POST"});
    await loadLibrary();toast(remove ? "已移到回收站，可以恢复" : "候选人已恢复");return;
  }
  const remarkButton = event.target.closest("[data-remark]");
  if (remarkButton) {
    state.remarkTarget = {jobId:library ? null : state.job.id,rowId:remarkButton.dataset.remark,library};
    const row = (library ? state.library.rows : state.job.rows).find(row => row.id === remarkButton.dataset.remark);
    $("remarkName").textContent = (row.data.name || "候选人") + " · 备注";
    $("remarkFields").innerHTML = remarkFields("remark", row);
    $("remarkDialog").showModal(); return;
  }
  const button = event.target.closest("[data-detail]");
  if (!button) return;
  state.detail = await (await api(library ? `/api/talent/${button.dataset.detail}` : `/api/jobs/${state.job.id}/rows/${button.dataset.detail}`)).json();
  state.detail.origin = library ? "talent" : "batch";
  renderDetail(); $("detailDialog").showModal();
}));
}
bindCandidateTable("rows");bindCandidateTable("libraryRows",true);
$("closeRemark").addEventListener("click", () => $("remarkDialog").close());
$("remarkForm").addEventListener("submit", handle(async event => {
  event.preventDefault(); $("saveRemark").disabled = true;
  const target = state.remarkTarget;
  try {
    await api(target.library ? `/api/talent/${target.rowId}/remark` : `/api/jobs/${target.jobId}/rows/${target.rowId}/remark`, {method:"PUT",body:collectRemarks("remark")});
    if (target.library) await loadLibrary();
    else if (state.job?.id === target.jobId) await loadJob(target.jobId);
    $("remarkDialog").close(); toast("备注已保存，Excel 和 PDF 会包含最新备注");
  } finally { $("saveRemark").disabled = false; }
}));
const names = { name: "姓名", phone: "联系电话", email: "邮箱", location: "城市", target_role: "求职意向", summary: "经历摘要", skills: "技能（用中文逗号分隔）", notes: "核对事项（每行一条）", level: "教育阶段", school: "就读院校（该阶段）", college: "学院 / 院系", degree: "学历 / 学位（原文）", major: "专业（该阶段）", study_type: "学习形式", graduation_status: "毕业 / 在读状态", research_direction: "研究方向", academic_details: "学术与学习详情（绩点、排名、奖学金等）", source_text: "本段原文证据", start_date: "开始日期", end_date: "结束日期", company: "公司", title: "职位", description: "内容与职责", education: "完整教育经历", experience: "工作经历", projects: "项目经历" };
Object.assign(names, {target_city:"期望工作城市",job_status:"求职 / 任职状态（原文）",availability:"到岗时间（原文）",claimed_work_years:"自述工作年限（原文）",employment_type:"经历性质",department:"部门",claimed_duration:"自述时长（原文）",achievements:"成果与量化业绩（原文）",role:"本人项目角色",technologies:"项目技术栈",qualifications:"证书、奖项与学术成果",category:"类型（证书 / 奖项 / 论文 / 专利）",issuer:"发证 / 颁奖机构",date:"取得日期",details:"等级、编号与详情",languages:"语言能力",test_score:"考试与成绩",links:"作品集与个人主页",label:"链接名称",url:"网址（原文）"});
function field(key, value, scope = "base", index = "", multiline = false) {
  const attrs = `data-scope="${scope}" data-index="${index}" data-key="${key}"`;
  const label = key === "name" && scope !== "base" ? ({projects:"项目名称",qualifications:"证书 / 奖项 / 成果名称",languages:"语种"}[scope] || "名称") : key === "level" && scope === "languages" ? "语言水平（原文）" : key === "location" && scope === "experience" ? "工作地点" : names[key] || key;
  if (key === "level" && scope === "education") {
    const levels = ["", "大专", "本科", "硕士", "博士", "高中/中专", "研究生（层次未明确）", "其他"];
    if (value && !levels.includes(value)) levels.push(value);
    return `<label>${names.level}<select ${attrs}>${levels.map(level => `<option value="${esc(level)}" ${value === level ? "selected" : ""}>${esc(level || "未提供")}</option>`).join("")}</select></label>`;
  }
  if (key === "employment_type") {
    return `<label>${names[key]}<select ${attrs}>${["工作","实习","兼职","劳务/自由职业","未明确"].map(kind => `<option value="${kind}" ${value === kind ? "selected" : ""}>${kind}</option>`).join("")}</select></label>`;
  }
  return `<label class="${multiline ? "full-field" : ""}">${esc(label)}${multiline ? `<textarea ${attrs} rows="3">${esc(value)}</textarea>` : `<input ${attrs} value="${esc(value)}">`}</label>`;
}
function renderDetail() {
  const row = state.detail, data = row.data;
  $("detailName").textContent = row.isNew ? "新增候选人" : data.name || "未提供姓名";
  $("detailWarnings").textContent = row.isNew ? "" : row.derived.warnings.join("；");
  const editable = row.origin === "talent" ? row.can_edit !== false : state.job?.status === "completed";
  $("detailStatus").textContent = row.isNew ? "填写后直接保存在本地数据库，无需调用 API" : row.deleted_at ? "回收站中的候选人请先恢复后再编辑" : editable ? "保存后数据库与原批次的信息会同步更新" : "原批次正在更新，完成后可以保存修改";
  $("saveCandidate").disabled = !editable;
  $("rawText").textContent = row.raw_text || "没有提取原文";
  $("evidence").innerHTML = data.evidence.length ? data.evidence.map(e => `<div class="evidence-item"><strong>${esc(e.field)}</strong>${esc(e.quote)}</div>`).join("") : '<p class="source-note">没有可定位的原文证据</p>';
  $("editFields").innerHTML = '<div class="candidate-remark-field">' + remarkFields("candidate", row) + '</div><p class="source-note">短备注显示在姓名下方；详细备注在详情中查看，补全简历信息不会覆盖备注。</p>' + '<div class="edit-grid">' + ["name", "phone", "email", "location", "target_role", "target_city", "job_status", "availability", "claimed_work_years"].map(key => field(key, data[key])).join("") + '</div>' +
    field("skills", data.skills.join("、"), "base") + field("summary", data.summary, "base", "", true) +
    renderItems("education", data.education) + ["工作","实习","其他"].map(kind => renderItems("experience", data.experience, kind)).join("") + ["projects","qualifications","languages","links"].map(scope => renderItems(scope, data[scope])).join("") + field("notes", data.notes.join("\n"), "base", "", true);
  $("editFields").querySelector('[data-scope="base"][data-key="name"]').required = !!row.isNew;
  switchDetail("profile");
}
function renderItems(scope, items, kind = "") {
  const keys = {education:["level","school","college","major","degree","study_type","start_date","end_date","graduation_status","research_direction"],experience:["employment_type","company","title","department","location","start_date","end_date","claimed_duration"],projects:["name","role","start_date","end_date","technologies"],qualifications:["category","name","issuer","date"],languages:["name","level","test_score"],links:["label","url"]}[scope];
  const title = kind ? (kind === "其他" ? "兼职、劳务与性质待核对经历" : kind + "经历") : names[scope];
  const selected = items.map((item,index) => ({item,index})).filter(({item}) => !kind || (kind === "其他" ? !["工作","实习"].includes(item.employment_type) : item.employment_type === kind));
  return `<section class="edit-section"><div class="edit-section-heading"><h3>${esc(title)}</h3><button type="button" class="button small" data-add="${scope}" data-kind="${esc(kind)}">+ 添加</button></div>` + selected.map(({item,index}) => {
    const extras = scope === "education" ? ["academic_details","source_text"] : scope === "experience" || scope === "projects" ? ["description","achievements","source_text"] : scope === "qualifications" ? ["details","source_text"] : [];
    const timing = scope === "experience" ? state.detail.derived.experience_details?.[index] : null;
    return `<div class="edit-card"><div class="edit-card-header"><span>${esc(scope === "education" ? (item.level || "阶段未提供") + " / " + (item.school || "院校未提供") : scope === "experience" ? (item.company || "公司未提供") + " / " + (item.title || "岗位未提供") : item.name || item.label || "新记录")}</span><button type="button" class="icon-button danger" data-remove-scope="${scope}" data-remove-index="${index}">移除</button></div>${timing ? `<p class="duration-note">本段时长：${esc(timing.label)}${timing.warning ? " · " + esc(timing.warning) : ""}（保存后重新计算）</p>` : ""}<div class="edit-grid">${keys.map(key => field(key,item[key] || "",scope,index)).join("")}</div>${extras.map(key => field(key,item[key] || "",scope,index,true)).join("")}</div>`;
  }).join("") + (selected.length ? "" : '<p class="source-note">原文未提供或尚未提取，可核对原文后补充。</p>') + (scope === "experience" ? '<p class="source-note">时长按起止月份（含首尾月）计算，同类重叠月份去重；工作与实习分别累计。兼职、劳务和性质未明确的记录单独保留。</p>' : "") + '</section>';
}
function collectDetail() {
  const data = structuredClone(state.detail.data);
  $("editFields").querySelectorAll("[data-key]").forEach(input => {
    const key = input.dataset.key, scope = input.dataset.scope;
    const value = input.type === "checkbox" ? input.checked : input.value.trim();
    if (scope === "base") data[key] = key === "skills" ? value.split(/[、,，;；\n]+/).map(s => s.trim()).filter(Boolean) : key === "notes" ? value.split("\n").map(s => s.trim()).filter(Boolean) : value;
    else { data[scope][Number(input.dataset.index)][key] = value; if (key === "employment_type") data[scope][Number(input.dataset.index)].is_internship = value === "实习"; }
  });
  return data;
}
$("editFields").addEventListener("click", event => {
  const add = event.target.closest("[data-add]"), remove = event.target.closest("[data-remove-scope]");
  if (!add && !remove) return;
  state.detail.data = collectDetail();
  Object.assign(state.detail, collectRemarks("candidate"));
  state.detail.derived.experience_details = [];
  if (add) state.detail.data[add.dataset.add].push(add.dataset.add === "experience" ? { employment_type: ["工作","实习"].includes(add.dataset.kind) ? add.dataset.kind : "未明确", is_internship: add.dataset.kind === "实习" } : {});
  else state.detail.data[remove.dataset.removeScope].splice(Number(remove.dataset.removeIndex), 1);
  renderDetail();
});
$("editForm").addEventListener("submit", handle(async event => {
  event.preventDefault(); $("saveCandidate").disabled = true;
  try {
    const remarks = collectRemarks("candidate");
    const row = state.detail;
    const path = row.origin === "talent" ? row.isNew ? "/api/talent" : `/api/talent/${row.id}` : `/api/jobs/${state.job.id}/rows/${row.id}`;
    await api(path, { method: row.isNew ? "POST" : "PUT", body: {...collectDetail(),_remark:remarks.remark,_short_remark:remarks.short_remark,_remark_color:remarks.remark_color} });
    if (row.origin === "talent") await loadLibrary(); else await loadJob(state.job.id);
    $("detailDialog").close(); toast(row.isNew ? "候选人已新增到本地数据库" : "修改已保存，数据库与报表会使用最新信息");
  } finally { $("saveCandidate").disabled = false; }
}));
function switchDetail(panel) {
  $("profilePanel").hidden = panel !== "profile"; $("sourcePanel").hidden = panel !== "source";
  $("profileTab").classList.toggle("active", panel === "profile"); $("sourceTab").classList.toggle("active", panel === "source");
}
$("profileTab").addEventListener("click", () => switchDetail("profile")); $("sourceTab").addEventListener("click", () => switchDetail("source"));
$("closeDetail").addEventListener("click", () => $("detailDialog").close());

async function init() {
  const defaults = await (await api("/api/config")).json();
  state.maxFiles = defaults.max_files;
  let saved = {}; try { saved = JSON.parse(localStorage.getItem("resumeSettings") || "{}"); } catch {}
  const config = { ...defaults, ...saved };
  $("baseUrl").value = config.base_url; $("model").value = config.model;
  $("jsonMode").checked = config.json_mode !== false;
  $("useOcr").checked = config.use_ocr !== false; $("forceOcr").checked = config.force_ocr === true;
  $("provider").value = Object.keys(presets).find(key => presets[key].base_url === config.base_url) || "custom";
  state.ready = defaults.has_env_key;
  if (state.ready) $("apiKey").placeholder = "已使用环境变量中的 API Key，可填写以覆盖";
  connectionStatus(); await loadHistory(); await refreshLibraryCount();
}
init().catch(error => toast(error.message, true));
