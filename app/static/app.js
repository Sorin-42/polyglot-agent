/* Polyglot Agent – front end */
"use strict";

const TOKEN = window.PA_TOKEN;
const $ = (id) => document.getElementById(id);
let settings = {}, meta = {}, ws = null, busy = false, attachments = [];
let chatMeta = { id: "", project_id: null, title: "", saved: false };
let projects = [], chats = [], usage = {}, context = {}, sessionCost = 0;
const items = new Map();          // id -> {el, data, render?}
const renderQueue = new Set();

/* ------------------------------------------------------------------ i18n */
const STR = {
  en: {
    language: "Language", memory: "Memory", newChat: "New chat", settings: "Settings", screen: "Screen", chats: "Chats",
    welcomeTitle: "What can I help with?",
    welcomeBody: "Ask anything — PCB design, debugging, research, everyday questions — or ask me to operate your computer.",
    attach: "Attach image", placeholder: "Message… (Enter to send, Shift+Enter for a new line)",
    send: "Send", stop: "Stop", liveScreen: "Live screen", noScreen: "No screenshot yet",
    failsafe: "Emergency stop: press Stop, or move the mouse into a screen corner.",
    newFile: "New file", save: "Save", delete: "Delete", confirmDelete: "Confirm delete?",
    agent: "Agent", translation: "Translation", tools: "Tools", toolsNote: "Tool changes apply to the next new chat.",
    idle: "Idle", translating_in: "Translating", thinking: "Thinking", acting: "Working",
    translating_out: "Translating reply", waiting_approval: "Needs your approval", retrying: "Retrying",
    offline: "Reconnecting…", thinkingLabel: "Thinking", activity: "Activity", steps: "steps",
    showEnglish: "Show English", hideEnglish: "Hide English", englishSent: "Sent to agent as",
    approveTitle_shell: "Run this PowerShell command?", approveTitle_file: "Allow this file change?",
    approveTitle_computer: "Allow this action?", approveTitle_folder: "Allow reading files here?",
    approve: "Allow", deny: "Deny", allowFolder: "Allow this folder", allowOnce: "Just this file", folder: "Folder",
    approved: "Answered", memUpdated: "Memory updated", memUpdating: "Updating memory…",
    saved: "Saved", copy: "Copy", copied: "Copied",
    chooseLanguage: "Choose your language",
    chooseLanguageBody: "You write and read in this language; the agent works in English behind the scenes. Change it any time from the top bar.",
    choose: "Choose…",
    translateMem: "Translate", showOriginal: "Show original", translatingMem: "Translating…",
    memTranslatedNote: "Translation for reading only. Memory is stored in English for the agent; switch to the original to edit.",
    tidyMem: "Tidy up", tidyingMem: "Tidying…", clearMem: "Clear all", undoClean: "Undo",
    undoTitle: "Restore memory from before the last tidy-up or clear",
    memTidied: "Memory tidied", memCleared: "Memory cleared", memRestored: "Memory restored",
    memCleanNote: "New chats use this memory; chats already started keep the memory they began with.",
    memBusy: "Wait until the current request and memory update have finished, then try again.",
    newProject: "New project", projects: "Projects", chatsSection: "Chats", project: "Project",
    projectName: "Name", projectInstructions: "Instructions for the agent (optional)",
    instructionsHint: "e.g. 4-layer board, JLCPCB rules, KiCad 9. Answer with metric units.",
    readableFolders: "Folders the agent may read without asking", add: "Add",
    projectNote: "Instructions and folders apply to new chats in this project.", deleteProject: "Delete project",
    noFolders: "None yet — granted automatically the first time you allow a folder.",
    noChats: "No chats yet", untitled: "New chat", newChatIn: "New chat in this project", editProject: "Edit project",
    tokenUsage: "Token usage", context: "Context", left: "left", thisChat: "This chat", input: "Input",
    cached: "Cached", output: "Output", cost: "Cost", total: "Total", sessionTotal: "All chats since launch",
    row_agent: "Agent", row_translator: "Translator", row_memory: "Memory review",
    costNote: "Input = new tokens (incl. cache writes); Cached = cache reads (~10% price). Cost is an estimate.",
    turnLabel: "This request",
    quitApp: "Quit app", quitting: "Polyglot Agent has quit. You can close this window.",
    windowLegend: "Window", windowMode: "Open the app in", windowNote: "Applies the next time you start the app.",
    win_app: "Built-in window (WebView2, recommended)", win_edge: "Microsoft Edge window",
    win_chrome: "Google Chrome window", win_browser: "Default browser tab (e.g. Firefox)",
    shareLabel: "Screen sharing", shareWindow: "Active app", shareScreen: "Full screen",
    shareHint: "What the agent's screenshots include. The taskbar is never shared.",
    approveTitle_screen: "Let the agent see your full screen? (Otherwise it only sees the active app.)",
    compacting: "Compacting context", compacted: "Context compacted: earlier messages were summarized",
    compactFailed: "Auto compact failed; continuing with the full context",
    compactHint: "Click to see the summary the agent continues from (in English)", row_compact: "Auto compact",
    // settings form (option values without an entry here are shown as-is: low, medium, ask, auto…)
    set_api_key: "Anthropic API key", set_base_url: "Base URL", keySet: "set", keyNotSet: "Not set (or set ANTHROPIC_API_KEY)",
    set_model: "Model", set_effort: "Effort", set_max_tokens: "Max tokens / reply", set_max_steps: "Max steps / request",
    set_show_thinking: "Show thinking summaries",
    set_cache_note: "Changing model or effort mid-chat starts a fresh prompt cache for that chat.",
    set_translator_model: "Translator model", set_translator_effort: "Translator effort",
    set_show_original: "Show English originals",
    set_computer_use: "Computer control (screen, mouse, keyboard)", set_capture_mode: "Screenshots",
    set_capture_note: "Active app = the focused window only · Full screen = the whole screen. The taskbar is never included.",
    set_confirm_actions: "Ask before every mouse/keyboard action",
    set_esc_note: "While Claude controls the computer, a border and Claude's pointer are shown. Press Esc on your keyboard at any time to stop it.",
    set_monitor: "Display to control", disp_primary: "main display", disp_left: "on the left",
    disp_right: "on the right", disp_above: "above", disp_below: "below", set_screenshot_edge: "Screenshot long edge (px)", set_shell_mode: "PowerShell commands",
    set_file_mode: "File edits", set_file_note: "Reading files asks once per folder.", set_web_search: "Web search",
    set_memory_enabled: "Persistent memory", set_memory_pass: "Review & update memory after each request",
    set_memory_model: "Memory reviewer model", set_context: "Context",
    set_auto_compact: "Auto compact long chats", set_compact_trigger: "Compact when the context reaches (tokens)",
    set_compact_note: "Older messages are summarized so the chat can keep going. The summary replaces them for the agent; you still see the whole chat.",
    set_clear_trigger: "Clear old tool results after (input tokens)", set_keep_tools: "Keep most recent tool results",
    // folders and the data folder
    browse: "Browse…", change: "Change…", cancel: "Cancel", addFolder: "Add folder…",
    pickReadTitle: "Choose a folder the agent may read",
    pickAllowTitle: "Choose the folder to allow (it must contain the file)",
    folderNotContaining: "That folder doesn't contain this file; pick it or a folder above it.",
    dataTitle: "Where should Polyglot Agent keep your data?",
    dataBody: "Your memory, chats, projects and settings (including your API key) are saved in this folder.",
    pickDataTitle: "Choose a folder for Polyglot Agent's data", useFolder: "Use this folder",
    dataExisting: "This folder already has Polyglot Agent data. The app will use it.",
    dataNew: "Your data will be saved here.",
    dataCopy: "Your current data will be copied here. The old folder is kept; you can delete it yourself.",
    dataCurrent: "This is the folder in use now.",
    dataMissing: "The data folder used last time wasn't found:",
    dataLegend: "Data folder", dataOpen: "Open",
    dataSettingsNote: "Memory, chats, projects and settings are stored here.",
  },
  zh: {
    language: "语言", memory: "记忆", newChat: "新对话", settings: "设置", screen: "屏幕", chats: "对话列表",
    welcomeTitle: "需要我帮你做什么？",
    welcomeBody: "随便问——PCB 设计、调试代码、查资料、日常问题——也可以让我直接操作你的电脑。",
    attach: "添加图片", placeholder: "输入消息…（Enter 发送，Shift+Enter 换行）",
    send: "发送", stop: "停止", liveScreen: "实时屏幕", noScreen: "暂无截图",
    failsafe: "紧急停止：点击“停止”，或把鼠标移到屏幕角落。",
    newFile: "新建文件", save: "保存", delete: "删除", confirmDelete: "确认删除？",
    agent: "智能体", translation: "翻译", tools: "工具", toolsNote: "工具设置在下一个新对话中生效。",
    idle: "空闲", translating_in: "翻译中", thinking: "思考中", acting: "执行中",
    translating_out: "翻译回复中", waiting_approval: "等待你的确认", retrying: "重试中",
    offline: "重新连接中…", thinkingLabel: "思考过程", activity: "操作记录", steps: "步",
    showEnglish: "显示英文原文", hideEnglish: "隐藏英文原文", englishSent: "发送给智能体的英文",
    approveTitle_shell: "运行这条 PowerShell 命令？", approveTitle_file: "允许修改这个文件？",
    approveTitle_computer: "允许这个操作？", approveTitle_folder: "允许读取这里的文件？",
    approve: "允许", deny: "拒绝", allowFolder: "允许整个文件夹", allowOnce: "仅此文件", folder: "文件夹",
    approved: "已处理", memUpdated: "记忆已更新", memUpdating: "正在更新记忆…",
    saved: "已保存", copy: "复制", copied: "已复制",
    chooseLanguage: "选择你的语言",
    chooseLanguageBody: "你用这种语言输入和阅读；智能体在后台使用英文工作。之后可随时在顶部栏更改。",
    choose: "请选择…",
    translateMem: "翻译", showOriginal: "显示原文", translatingMem: "翻译中…",
    memTranslatedNote: "译文仅供阅读。记忆以英文保存供智能体使用；如需编辑请切换到原文。",
    tidyMem: "整理", tidyingMem: "整理中…", clearMem: "全部清除", undoClean: "撤销",
    undoTitle: "恢复到上次整理或清除之前的记忆",
    memTidied: "记忆已整理", memCleared: "记忆已清除", memRestored: "记忆已恢复",
    memCleanNote: "新对话会使用这份记忆；已开始的对话仍保留开始时的记忆。",
    memBusy: "请等当前请求和记忆更新完成后再试。",
    newProject: "新项目", projects: "项目", chatsSection: "对话", project: "项目",
    projectName: "名称", projectInstructions: "给智能体的说明（可选）",
    instructionsHint: "例如：四层板、嘉立创工艺、KiCad 9，回答使用公制单位。",
    readableFolders: "智能体无需询问即可读取的文件夹", add: "添加",
    projectNote: "说明和文件夹会应用到这个项目中的新对话。", deleteProject: "删除项目",
    noFolders: "暂无——第一次允许某个文件夹时会自动加入。",
    noChats: "还没有对话", untitled: "新对话", newChatIn: "在此项目中新建对话", editProject: "编辑项目",
    tokenUsage: "Token 用量", context: "上下文", left: "剩余", thisChat: "本对话", input: "输入",
    cached: "缓存命中", output: "输出", cost: "费用", total: "合计", sessionTotal: "启动以来所有对话",
    row_agent: "智能体", row_translator: "翻译", row_memory: "记忆整理",
    costNote: "输入 = 新 token（含缓存写入）；缓存命中按约 10% 计价。费用为估算。",
    turnLabel: "本次请求",
    quitApp: "退出程序", quitting: "Polyglot Agent 已退出，可以关闭此窗口。",
    windowLegend: "窗口", windowMode: "在以下位置打开应用", windowNote: "下次启动应用时生效。",
    win_app: "内置窗口（WebView2，推荐）", win_edge: "Microsoft Edge 窗口",
    win_chrome: "Google Chrome 窗口", win_browser: "默认浏览器标签页（如 Firefox）",
    shareLabel: "屏幕共享", shareWindow: "仅当前应用", shareScreen: "全屏",
    shareHint: "智能体截图包含的范围。任务栏永远不会被共享。",
    approveTitle_screen: "允许智能体查看整个屏幕？（否则只能看到当前应用。）",
    compacting: "正在压缩上下文", compacted: "上下文已自动压缩：较早的消息已总结为摘要",
    compactFailed: "自动压缩失败，继续使用完整上下文",
    compactHint: "点击查看智能体接下来依据的摘要（英文）", row_compact: "自动压缩",
    set_api_key: "Anthropic API 密钥", set_base_url: "API 地址（Base URL）", keySet: "已设置",
    keyNotSet: "未设置（也可以设置环境变量 ANTHROPIC_API_KEY）",
    set_model: "模型", set_effort: "推理强度", set_max_tokens: "每次回复最多 token 数", set_max_steps: "每个请求最多步数",
    set_show_thinking: "显示思考摘要",
    set_cache_note: "在对话中途更换模型或推理强度，会为这个对话重新建立提示缓存。",
    set_translator_model: "翻译模型", set_translator_effort: "翻译推理强度",
    set_show_original: "显示英文原文",
    set_computer_use: "电脑控制（屏幕、鼠标、键盘）", set_capture_mode: "截图范围",
    set_capture_note: "仅当前应用 = 只截取当前聚焦的窗口 · 全屏 = 整个屏幕。任务栏永远不会被截取。",
    set_confirm_actions: "每次鼠标/键盘操作前都先询问",
    set_esc_note: "Claude 控制电脑时，屏幕四周会显示边框和 Claude 的指针。随时按键盘上的 Esc 即可让它停止。",
    set_monitor: "要控制的显示器", disp_primary: "主显示器", disp_left: "左侧",
    disp_right: "右侧", disp_above: "上方", disp_below: "下方", set_screenshot_edge: "截图长边（像素）", set_shell_mode: "PowerShell 命令",
    set_file_mode: "文件修改", set_file_note: "读取文件时，每个文件夹只询问一次。", set_web_search: "网络搜索",
    set_memory_enabled: "长期记忆", set_memory_pass: "每次请求后检查并更新记忆",
    set_memory_model: "记忆整理模型", set_context: "上下文",
    set_auto_compact: "自动压缩长对话", set_compact_trigger: "上下文达到多少 token 时压缩",
    set_compact_note: "较早的消息会被总结成摘要，让对话可以继续。智能体只看到摘要，你仍能看到完整的对话。",
    set_clear_trigger: "输入超过多少 token 后清除旧的工具结果", set_keep_tools: "保留最近几次工具结果",
    effort_low: "低", effort_medium: "中", effort_high: "高", effort_xhigh: "超高", effort_max: "最高",
    mode_ask: "先询问", mode_auto: "自动", mode_off: "关闭",
    browse: "浏览…", change: "更改…", cancel: "取消", addFolder: "添加文件夹…",
    pickReadTitle: "选择智能体可以读取的文件夹",
    pickAllowTitle: "选择要允许的文件夹（必须包含这个文件）",
    folderNotContaining: "这个文件夹不包含该文件，请选择它所在的文件夹或其上级文件夹。",
    dataTitle: "Polyglot Agent 的数据保存在哪里？",
    dataBody: "你的记忆、对话、项目和设置（包括 API 密钥）都会保存在这个文件夹里。",
    pickDataTitle: "选择 Polyglot Agent 的数据文件夹", useFolder: "使用这个文件夹",
    dataExisting: "这个文件夹里已有 Polyglot Agent 的数据，应用会直接使用它。",
    dataNew: "你的数据将保存在这里。",
    dataCopy: "当前的数据会复制到这里。原文件夹会保留，你可以自行删除。",
    dataCurrent: "这就是当前正在使用的文件夹。",
    dataMissing: "找不到上次使用的数据文件夹：",
    dataLegend: "数据文件夹", dataOpen: "打开",
    dataSettingsNote: "记忆、对话、项目和设置都保存在这里。",
  },
};
// The UI follows the language the user chose; nothing is detected from the browser or OS.
function uiLang() {
  const l = settings.language || "";
  return l === "简体中文" || l === "繁體中文" ? "zh" : "en";
}
const t = (k) => (STR[uiLang()] && STR[uiLang()][k]) || STR.en[k] || k;
function applyI18n() {
  document.documentElement.lang = uiLang() === "zh" ? "zh-CN" : "en";
  document.querySelectorAll("[data-i18n]").forEach((e) => (e.textContent = t(e.dataset.i18n)));
  document.querySelectorAll("[data-i18n-placeholder]").forEach((e) => (e.placeholder = t(e.dataset.i18nPlaceholder)));
  document.querySelectorAll("[data-i18n-title]").forEach((e) => (e.title = t(e.dataset.i18nTitle)));
}

/* --------------------------------------------------------------- helpers */
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } },
};
async function api(method, path, body) {
  const r = await fetch(path, {
    method, headers: { "X-Token": TOKEN, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(j.error || r.statusText); e.status = r.status; throw e; }
  return j;
}
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
const S = (v) => (v === undefined || v === null ? "" : String(v));
const J = (v) => (Array.isArray(v) ? v.join(", ") : S(v));
function fmtK(n) {
  n = Number(n) || 0;
  if (n < 1000) return String(n);
  if (n < 1e6) return (n / 1000).toFixed(n < 1e4 ? 1 : 0) + "k";
  return (n / 1e6).toFixed(2).replace(/\.?0+$/, "") + "M";
}
const money = (c) => "$" + (Number(c) || 0).toFixed(c >= 1 ? 2 : 4);
function twoClick(btn, label, action) {
  if (btn._armed) { btn._armed = false; btn.textContent = label; btn.classList.remove("armed"); action(); return; }
  btn._armed = true; btn.classList.add("armed");
  const old = btn.textContent; btn.textContent = t("confirmDelete");
  setTimeout(() => { if (btn._armed) { btn._armed = false; btn.textContent = old; btn.classList.remove("armed"); } }, 3000);
}

// Native folder dialog, opened by the server (a web page can't see real paths). null = cancelled.
async function pickFolder(title, initial) {
  try { return (await api("POST", "/api/pick-folder", { title, initial: initial || "" })).path || null; }
  catch (err) { alertNotice(err.message); return null; }
}
// Is `path` inside `folder`? Windows paths: case-insensitive, either slash.
function pathWithin(path, folder) {
  const norm = (p) => String(p).replace(/\//g, "\\").replace(/\\+$/, "").toLowerCase();
  const f = norm(folder), p = norm(path);
  return !!f && (p === f || p.startsWith(f + "\\"));
}

const messagesEl = $("messages");
function nearBottom() { return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 120; }
function scrollDown(force) { if (force || nearBottom()) requestAnimationFrame(() => (messagesEl.scrollTop = messagesEl.scrollHeight)); }
function append(node) {
  const stick = nearBottom();
  $("empty").hidden = true;
  messagesEl.appendChild(node);
  if (stick) scrollDown(true);
}

if (window.marked) marked.setOptions({ gfm: true, breaks: false });
// Model output is untrusted (it may echo web pages or files): no styles, no
// forms, and no remote images (an <img src=https://…> could leak data).
const PURIFY = {
  FORBID_TAGS: ["style", "form", "input", "button", "textarea", "select", "iframe", "svg", "math"],
  FORBID_ATTR: ["style"],
};
if (window.DOMPurify) {
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "IMG" && !String(node.getAttribute("src") || "").startsWith("data:image/")) {
      node.removeAttribute("src");
      node.setAttribute("alt", "[image blocked] " + (node.getAttribute("alt") || ""));
    }
  });
}
function renderMd(target, text) {
  let html;
  try { html = window.DOMPurify ? DOMPurify.sanitize(marked.parse(text || ""), PURIFY) : null; } catch { html = null; }
  if (html === null) { target.textContent = text || ""; return; }
  target.innerHTML = html;
  target.querySelectorAll("a").forEach((a) => { a.target = "_blank"; a.rel = "noopener noreferrer"; });
  target.querySelectorAll("pre").forEach((pre) => {
    const b = el("button", "copy-btn", t("copy"));
    b.type = "button";
    b.onclick = () => {
      const code = pre.querySelector("code");
      navigator.clipboard.writeText(code ? code.innerText : pre.innerText).then(() => {
        b.textContent = t("copied"); setTimeout(() => (b.textContent = t("copy")), 1200);
      });
    };
    pre.appendChild(b);
  });
}
function scheduleRender(id) {
  renderQueue.add(id);
  if (renderQueue.size === 1) requestAnimationFrame(flushRender);
}
function flushRender() {
  const stick = nearBottom();
  for (const id of renderQueue) {
    const it = items.get(id);
    if (it && it.render) it.render();
  }
  renderQueue.clear();
  if (stick) scrollDown(true);
}
function openViewer(src) { $("viewerImg").src = src; $("viewer").hidden = false; }
$("viewer").onclick = () => ($("viewer").hidden = true);

/* -------------------------------------------------------------- renderers */
function renderUser(d) {
  const wrap = el("div", "msg user");
  const b = el("div", "bubble", d.original);
  if (d.images && d.images.length) {
    const imgs = el("div", "imgs");
    d.images.forEach((src) => {
      const im = el("img"); im.src = "data:image/jpeg;base64," + src; im.onclick = () => openViewer(im.src);
      imgs.appendChild(im);
    });
    b.appendChild(imgs);
  }
  wrap.appendChild(b);
  if (d.translated && d.english) {
    const o = el("div", "orig orig-en");
    o.appendChild(el("b", "", t("englishSent") + ": "));
    o.appendChild(document.createTextNode(d.english));
    o.hidden = !settings.show_original;
    wrap.appendChild(o);
  }
  items.set(d.id, { el: wrap, data: d });
  append(wrap);
}

function renderAssistant(d) {
  const wrap = el("div", "msg assistant");
  const main = el("div", "md");
  const enBox = el("div", "en-orig md");
  const toggle = el("button", "en-toggle");
  const sources = el("div", "sources");
  toggle.type = "button";
  wrap.append(main);
  const it = { el: wrap, data: d, showEn: !!settings.show_original };
  if (d.translated) {
    wrap.append(toggle, enBox);
    toggle.onclick = () => { it.showEn = !it.showEn; it.render(); };
  }
  wrap.append(sources);
  const dots = '<span class="typing"><span></span><span></span><span></span></span>';
  it.render = () => {
    const x = it.data;
    if (x.translated) {
      if (x.translation) renderMd(main, x.translation);
      else if (x.done) renderMd(main, x.english);
      else main.innerHTML = dots;
      enBox.hidden = !it.showEn;
      toggle.textContent = it.showEn ? t("hideEnglish") : t("showEnglish");
      toggle.hidden = !x.english;
      if (it.showEn) renderMd(enBox, x.english);
    } else if (x.english) renderMd(main, x.english);
    else main.innerHTML = dots;
    sources.replaceChildren();
    (x.sources || []).forEach((s) => {
      const a = el("a", "", s.title || s.url); a.href = s.url; a.target = "_blank"; a.rel = "noopener noreferrer"; a.title = s.url;
      sources.appendChild(a);
    });
  };
  items.set(d.id, it);
  append(wrap);
  it.render();
}

function renderThinking(d) {
  const det = el("details", "thinking");
  const body = el("div", "body", d.text || "");
  det.append(el("summary", "", t("thinkingLabel")), body);
  const it = { el: det, data: d, render: () => (body.textContent = it.data.text) };
  items.set(d.id, it);
  append(det);
}

function currentGroup() {
  const last = messagesEl.lastElementChild;
  if (last && last.classList.contains("activity")) return last;
  const det = el("details", "activity");
  det.open = true;
  det.append(el("summary"), el("div", "actions"));
  det._count = 0;
  append(det);
  return det;
}
function actionLabel(d) {
  try { return actionLabelRaw(d); } catch { return S(d.name); }
}
function actionLabelRaw(d) {
  const i = d.input && typeof d.input === "object" ? d.input : {};
  const c = i.coordinate ? ` (${J(i.coordinate)})` : "";
  switch (d.kind) {
    case "computer":
      switch (d.name) {
        case "type": return `⌨ type “${S(i.text).slice(0, 80)}”`;
        case "key": return `⌨ ${S(i.text)}${i.repeat > 1 ? " ×" + i.repeat : ""}`;
        case "hold_key": return `⌨ hold ${S(i.text)} ${S(i.duration)}s`;
        case "scroll": return `🖱 scroll ${S(i.scroll_direction)} ×${S(i.scroll_amount || 3)}${c}`;
        case "screenshot": return "📷 screenshot";
        case "zoom": return `🔍 zoom [${J(i.region)}]`;
        case "wait": return `⏱ wait ${S(i.duration || 1)}s`;
        case "left_click_drag": return `🖱 drag (${J(i.start_coordinate)}) → (${J(i.coordinate)})`;
        case "windows": return `🪟 ${S(i.action)} ${S(i.title || i.mode)}`;
        default: return `🖱 ${S(d.name)}${c}${i.text ? " +" + S(i.text) : ""}`;
      }
    case "shell": return `💻 ${S(i.command).split("\n")[0].slice(0, 120)}`;
    case "editor": return `📄 ${S(d.name).replace("file_", "")} ${S(i.path)}`;
    case "web": return `🌐 ${S(i.query || d.name)}`;
    default: return S(d.name);
  }
}
function renderAction(d) {
  const grp = currentGroup();
  const row = el("div", "act " + (d.state || "running"));
  const st = el("span", "st");
  const label = el("span", "label", actionLabel(d));
  label.title = label.textContent;
  row.append(st, label);
  grp.querySelector(".actions").appendChild(row);
  grp._count += 1;
  const it = { el: row, data: d, group: grp };
  it.render = () => {
    const x = it.data;
    row.className = "act " + (x.state || "running");
    st.textContent = x.state === "ok" ? "✓" : x.state === "error" ? "✕" : "◌";
    row.querySelectorAll(".detail, img").forEach((n) => n.remove());
    if (x.thumb) {
      const im = el("img"); im.src = "data:image/jpeg;base64," + x.thumb; im.onclick = () => openViewer(im.src);
      row.appendChild(im);
    }
    const showText = x.text && (x.kind === "shell" || x.kind === "web" || x.state === "error" ||
      x.name === "cursor_position" || x.name === "windows" || (x.name === "screenshot" && !x.thumb));
    if (showText) row.appendChild(el("div", "detail", x.text));
    grp.querySelector("summary").textContent = `${t("activity")} · ${grp._count} ${t("steps")} — ${actionLabel(x)}`;
  };
  items.set(d.id, it);
  it.render();
  scrollDown();
}
function setScreen(thumb) {
  const box = $("screenBox");
  box.replaceChildren();
  const im = el("img"); im.src = "data:image/jpeg;base64," + thumb; im.onclick = () => openViewer(im.src);
  box.appendChild(im);
  $("screenTime").textContent = new Date().toLocaleTimeString();
}
function renderNotice(d) {
  const n = el("div", "notice " + (d.level || ""), d.text);
  items.set(d.id, { el: n, data: d });
  append(n);
}
function renderCompacted(d) {
  const det = el("details", "compacted" + (d.ok ? "" : " failed"));
  const sum = el("summary", "", t(d.ok ? "compacted" : "compactFailed"));
  det.appendChild(sum);
  if (d.ok) {
    sum.title = t("compactHint");
    det.appendChild(el("div", "body", d.summary || ""));
  } else sum.onclick = (e) => e.preventDefault();   // nothing to show
  items.set(d.id, { el: det, data: d });
  append(det);
}
function inTokens(u) { return (u.input_tokens || 0) + (u.cache_creation_input_tokens || 0) + (u.cache_read_input_tokens || 0); }
function renderTurnUsage(d) {
  const u = d.usage || {};
  const parts = [];
  if (u.agent) {
    const tot = inTokens(u.agent);
    const hit = tot ? Math.round((100 * (u.agent.cache_read_input_tokens || 0)) / tot) : 0;
    parts.push(`${t("row_agent")} ${fmtK(tot)} ${t("input")} (${hit}% ${t("cached")}) · ${fmtK(u.agent.output_tokens)} ${t("output")}`);
  }
  if (u.translator) parts.push(`${t("row_translator")} ${fmtK(inTokens(u.translator) + (u.translator.output_tokens || 0))}`);
  if (u.memory) parts.push(`${t("row_memory")} ${fmtK(inTokens(u.memory) + (u.memory.output_tokens || 0))}`);
  if (u.compact) parts.push(`${t("row_compact")} ${fmtK(inTokens(u.compact) + (u.compact.output_tokens || 0))}`);
  const cost = Object.values(u).reduce((a, x) => a + (x.cost || 0), 0);
  parts.push("≈ " + money(cost));
  const line = el("div", "turn-usage", "Σ " + parts.join("  ·  "));
  items.set(d.id, { el: line, data: d });
  append(line);
}
function renderApproval(d) {
  const card = el("div", "approval");
  card.appendChild(el("strong", "", t("approveTitle_" + d.kind)));
  if (d.detail) card.appendChild(el("pre", "", d.detail));
  const row = el("div", "row");
  const answer = (payload) => {
    send({ type: "approval", id: d.id, ...payload });
    card.classList.add("done");
    row.replaceChildren(el("span", "muted small", t("approved")));
  };
  if (d.kind === "folder") {
    // The suggested folder can be widened (e.g. to the project root) with the folder picker.
    let folder = d.folder || "";
    const fr = el("div", "folder-row");
    const shown = el("span", "mono folder-path", folder);
    const change = el("button", "ghost", t("change")); change.type = "button";
    const warn = el("div", "small folder-warn");
    change.onclick = async () => {
      const p = await pickFolder(t("pickAllowTitle"), folder);
      if (!p) return;
      if (!pathWithin(d.detail || "", p)) { warn.textContent = t("folderNotContaining"); return; }
      folder = p; shown.textContent = p; warn.textContent = "";
    };
    fr.append(el("span", "", t("folder") + ":"), shown, change);
    card.append(fr, warn);
    const all = el("button", "primary", t("allowFolder")); all.type = "button";
    const once = el("button", "", t("allowOnce")); once.type = "button";
    const no = el("button", "", t("deny")); no.type = "button";
    all.onclick = () => answer({ approve: true, scope: "folder", folder });
    once.onclick = () => answer({ approve: true, scope: "once" });
    no.onclick = () => answer({ approve: false });
    row.append(all, once, no);
    setTimeout(() => all.focus(), 0);
  } else {
    const yes = el("button", "primary", t("approve")); yes.type = "button";
    const no = el("button", "", t("deny")); no.type = "button";
    yes.onclick = () => answer({ approve: true });
    no.onclick = () => answer({ approve: false });
    row.append(yes, no);
    setTimeout(() => yes.focus(), 0);
  }
  card.appendChild(row);
  items.set(d.id, { el: card, data: d });
  append(card);
  scrollDown(true);
}
function renderHistoryItem(d) {
  if (d.type === "user") renderUser(d);
  else if (d.type === "assistant") renderAssistant(Object.assign({}, d, { done: true }));
  else if (d.type === "thinking") { if (d.text) renderThinking(d); }
  else if (d.type === "action") renderAction(d);
  else if (d.type === "notice") renderNotice(d);
  else if (d.type === "turn_usage") renderTurnUsage(d);
  else if (d.type === "compacted") renderCompacted(d);
}
function clearChat() {
  items.clear();
  messagesEl.querySelectorAll(":scope > :not(#empty)").forEach((n) => n.remove());
  $("empty").hidden = false;
}
function loadHistory(history) {
  clearChat();
  (history || []).forEach((d) => { try { renderHistoryItem(d); } catch (e) { console.warn("history item", e); } });
  document.querySelectorAll("details.activity").forEach((d) => (d.open = false));
  scrollDown(true);
}

/* ----------------------------------------------------- usage / context */
function renderCtx() {
  const used = context.used || 0, win = context.window || 0;
  const pct = win ? Math.min(100, (100 * used) / win) : 0;
  const fill = $("ctxFill");
  fill.style.width = pct.toFixed(1) + "%";
  fill.className = pct > 80 ? "high" : pct > 50 ? "mid" : "";
  $("ctxText").textContent = win ? `${fmtK(used)} / ${fmtK(win)}` : "—";
  $("btnUsage").title = win ? `${t("context")}: ${fmtK(used)} / ${fmtK(win)} — ${fmtK(win - used)} ${t("left")}` : t("tokenUsage");
  if (!$("usagePop").hidden) renderUsagePanel();
}
function renderUsagePanel() {
  const body = $("usageBody");
  body.replaceChildren();
  const used = context.used || 0, win = context.window || 0;
  if (win) {
    const line = el("div", "ctx-line");
    line.append(el("span", "big", `${fmtK(used)} / ${fmtK(win)}`));
    line.append(document.createTextNode(`  ${t("context")} · ${fmtK(win - used)} ${t("left")} (${(100 - (100 * used) / win).toFixed(1)}%)`));
    body.appendChild(line);
  }
  const tbl = el("table", "usage-table");
  const head = el("tr");
  ["", t("input"), t("cached"), t("output"), t("cost")].forEach((h) => head.appendChild(el("th", "", h)));
  tbl.appendChild(head);
  const tot = { i: 0, c: 0, o: 0, cost: 0 };
  ["agent", "compact", "translator", "memory"].forEach((cat) => {
    const u = usage[cat];
    if (!u) return;
    const i = (u.input_tokens || 0) + (u.cache_creation_input_tokens || 0), c = u.cache_read_input_tokens || 0, o = u.output_tokens || 0;
    tot.i += i; tot.c += c; tot.o += o; tot.cost += u.cost || 0;
    const tr = el("tr");
    const hit = i + c ? ` (${Math.round((100 * c) / (i + c))}%)` : "";
    [t("row_" + cat), fmtK(i), fmtK(c) + hit, fmtK(o), money(u.cost)].forEach((v) => tr.appendChild(el("td", "", v)));
    tbl.appendChild(tr);
  });
  const tr = el("tr", "total");
  [t("total") + " · " + t("thisChat"), fmtK(tot.i), fmtK(tot.c), fmtK(tot.o), money(tot.cost)].forEach((v) => tr.appendChild(el("td", "", v)));
  tbl.appendChild(tr);
  body.appendChild(tbl);
  body.appendChild(el("p", "muted small", `${t("sessionTotal")}: ≈ ${money(sessionCost)}`));
  body.appendChild(el("p", "muted small", t("costNote")));
}
function setUsage(ev) {
  if (ev.usage) usage = ev.usage;
  if (ev.context) context = ev.context;
  if (typeof ev.session_cost === "number") sessionCost = ev.session_cost;
  renderCtx();
}
$("btnUsage").onclick = () => { const p = $("usagePop"); p.hidden = !p.hidden; if (!p.hidden) renderUsagePanel(); };
$("usageClose").onclick = () => ($("usagePop").hidden = true);

/* -------------------------------------------------------------- sidebar */
let expanded = new Set(store.get("pa.expanded", []));
function projectById(id) { return projects.find((p) => p.id === id); }
function renderCrumb() {
  const c = $("crumb");
  c.replaceChildren();
  const p = projectById(chatMeta.project_id);
  if (p) { c.appendChild(el("b", "", p.name)); c.appendChild(document.createTextNode(" › ")); }
  c.appendChild(document.createTextNode(chatMeta.title || t("untitled")));
  document.title = (chatMeta.title ? chatMeta.title + " · " : "") + (meta.app_name || "Polyglot Agent");
}
function chatRow(c) {
  const li = el("li", c.id === chatMeta.id ? "active" : "");
  li.appendChild(el("span", "t", c.title || t("untitled")));
  li.title = c.title || "";
  if (c.id) {
    const del = el("button", "del", "✕"); del.type = "button";
    del.onclick = (e) => {
      e.stopPropagation();
      twoClick(del, "✕", () => api("DELETE", "/api/chats/" + c.id).catch((err) => alertNotice(err.message)));
    };
    li.appendChild(del);
    li.onclick = () => { if (c.id !== chatMeta.id) send({ type: "open_chat", id: c.id }); closeSidebarMobile(); };
  }
  return li;
}
function renderSidebar() {
  const pl = $("projectList");
  pl.replaceChildren();
  const pending = !chatMeta.saved ? { id: "", title: t("untitled"), project_id: chatMeta.project_id } : null;
  projects.forEach((p) => {
    const box = el("div", "proj");
    const head = el("div", "proj-head");
    const open = expanded.has(p.id) || p.id === chatMeta.project_id;
    head.append(el("span", "caret", open ? "▾" : "▸"), el("span", "name", p.name));
    const plus = el("button", "ghost", "＋"); plus.type = "button"; plus.title = t("newChatIn");
    plus.onclick = (e) => { e.stopPropagation(); send({ type: "new_chat", project_id: p.id }); closeSidebarMobile(); };
    const gear = el("button", "ghost", "⚙"); gear.type = "button"; gear.title = t("editProject");
    gear.onclick = (e) => { e.stopPropagation(); openProjectModal(p); };
    head.append(plus, gear);
    head.onclick = () => {
      if (expanded.has(p.id)) expanded.delete(p.id); else expanded.add(p.id);
      store.set("pa.expanded", [...expanded]); renderSidebar();
    };
    box.appendChild(head);
    if (open) {
      const ul = el("ul", "chat-list");
      if (pending && pending.project_id === p.id) ul.appendChild(Object.assign(chatRow(pending), { className: "active" }));
      const list = chats.filter((c) => c.project_id === p.id);
      list.forEach((c) => ul.appendChild(chatRow(c)));
      if (!list.length && !(pending && pending.project_id === p.id)) ul.appendChild(el("li", "empty-row", t("noChats")));
      box.appendChild(ul);
    }
    pl.appendChild(box);
  });
  const rl = $("randomList");
  rl.replaceChildren();
  if (pending && !projectById(pending.project_id)) rl.appendChild(Object.assign(chatRow(pending), { className: "active" }));
  chats.filter((c) => !c.project_id || !projectById(c.project_id)).forEach((c) => rl.appendChild(chatRow(c)));
  renderCrumb();
}
function setSidebarData(ev) {
  if (ev.projects) projects = ev.projects;
  if (ev.chats) chats = ev.chats;
  renderSidebar();
}
const sidebar = $("sidebar");
sidebar.hidden = window.innerWidth <= 760 ? true : !store.get("pa.sidebar", true);
$("btnSidebar").onclick = () => { sidebar.hidden = !sidebar.hidden; if (window.innerWidth > 760) store.set("pa.sidebar", !sidebar.hidden); };
function closeSidebarMobile() { if (window.innerWidth <= 760) sidebar.hidden = true; }
$("btnNewChat").onclick = () => { send({ type: "new_chat", project_id: null }); closeSidebarMobile(); };
$("btnNewProject").onclick = () => openProjectModal(null);
function alertNotice(text) { renderNotice({ id: "n" + Date.now(), level: "error", text }); }

/* ------------------------------------------------------- project editor */
let editingProject = null, editFolders = [];
const pform = $("projectForm");
function renderFolderList() {
  const ul = $("folderList");
  ul.replaceChildren();
  if (!editFolders.length) ul.appendChild(el("li", "none", t("noFolders")));
  editFolders.forEach((f, i) => {
    const li = el("li"); li.appendChild(el("span", "", f)); li.title = f;
    const x = el("button", "", "✕"); x.type = "button";
    x.onclick = () => { editFolders.splice(i, 1); renderFolderList(); };
    li.appendChild(x); ul.appendChild(li);
  });
}
function openProjectModal(p) {
  editingProject = p;
  pform.elements.name.value = p ? p.name : "";
  pform.elements.instructions.value = p ? p.instructions || "" : "";
  editFolders = p ? [...(p.folders || [])] : [];
  $("projDelete").hidden = !p;
  $("projDelete").textContent = t("deleteProject");
  $("projectTitle").textContent = p ? p.name : t("newProject");
  renderFolderList();
  $("projectModal").hidden = false;
  pform.elements.name.focus();
}
$("folderBrowse").onclick = async () => {
  const p = await pickFolder(t("pickReadTitle"), editFolders[editFolders.length - 1] || "");
  if (p && !editFolders.includes(p)) editFolders.push(p);
  renderFolderList();
};
$("projClose").onclick = () => ($("projectModal").hidden = true);
pform.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = { name: pform.elements.name.value.trim(), instructions: pform.elements.instructions.value, folders: editFolders };
  try {
    if (editingProject) await api("PUT", "/api/projects/" + editingProject.id, body);
    else {
      const p = await api("POST", "/api/projects", body);
      expanded.add(p.id); store.set("pa.expanded", [...expanded]);
      send({ type: "new_chat", project_id: p.id });
    }
    $("projectModal").hidden = true;
  } catch (err) { alertNotice(err.message); }
});
$("projDelete").onclick = () => twoClick($("projDelete"), t("deleteProject"), async () => {
  try { await api("DELETE", "/api/projects/" + editingProject.id); $("projectModal").hidden = true; }
  catch (err) { alertNotice(err.message); }
});

/* ----------------------------------------------------------- status/busy */
function setStatus(state, detail) {
  const pill = $("statusPill");
  pill.className = "pill " + (state === "idle" ? "idle" : state === "waiting_approval" ? "wait" : "busy");
  $("statusText").textContent = t(state) + (detail ? ` · ${detail}` : "");
  setBusy(state !== "idle" && state !== "offline");
}
function setBusy(b) {
  busy = b;
  $("btnSend").hidden = b;
  $("btnStop").hidden = !b;
}
let memBadgeTimer = null;
function memBadge(text, sticky) {
  const b = $("memBadge");
  b.textContent = "◆ " + text; b.hidden = false; b.title = text;
  clearTimeout(memBadgeTimer);
  if (!sticky) memBadgeTimer = setTimeout(() => (b.hidden = true), 6000);
}

/* ------------------------------------------------------------- websocket */
const outbox = [];
function send(obj) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
  else if (obj.type === "approval" || obj.type === "stop") outbox.push(obj);  // deliver after reconnect
}
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws?token=${encodeURIComponent(TOKEN)}`);
  ws.onopen = () => { while (outbox.length) ws.send(JSON.stringify(outbox.shift())); };
  ws.onmessage = (m) => handle(JSON.parse(m.data));
  ws.onclose = () => { setStatus("offline"); setTimeout(connect, 1500); };
}
function handle(ev) {
  const it = items.get(ev.id);
  switch (ev.type) {
    case "hello":
      settings = ev.settings; meta = ev.meta;
      applyI18n();
      if (ev.setup) { openDataModal("setup", ev.setup); break; }   // first start: choose a data folder
      if (dataMode === "setup") $("dataModal").hidden = true;
      dataMode = null;
      chatMeta = ev.chat || chatMeta;
      fillLang(); fillSettingsForm(); renderShareSwitch();
      loadHistory(ev.history);
      (ev.pending_approvals || []).forEach(renderApproval);
      setSidebarData(ev); setUsage(ev);
      setStatus(ev.busy ? ev.state || "acting" : "idle");
      break;
    case "chat_opened":
      chatMeta = ev.chat;
      loadHistory(ev.history);
      $("screenBox").replaceChildren(el("span", "muted", t("noScreen")));
      setUsage(ev); renderSidebar();
      $("input").focus();
      break;
    case "chat_meta": chatMeta = ev.chat; renderSidebar(); break;
    case "sidebar": setSidebarData(ev); break;
    case "settings":
      settings = ev.settings; applyI18n(); fillLang(); fillSettingsForm(); renderSidebar(); renderCtx(); renderShareSwitch();
      document.querySelectorAll(".orig-en").forEach((o) => (o.hidden = !settings.show_original));
      if (!$("memoryDrawer").hidden) applyMemView();
      break;
    case "status": setStatus(ev.state, ev.detail); break;
    case "user_message": renderUser(ev); break;
    case "assistant_start":
      renderAssistant({ id: ev.id, english: "", translation: "", translated: ev.translated, sources: [] });
      break;
    case "assistant_delta": if (it) { it.data.english += ev.text; scheduleRender(ev.id); } break;
    case "assistant_final": if (it) { it.data.english = ev.english; it.data.sources = ev.sources; scheduleRender(ev.id); } break;
    case "translation_delta": if (it) { it.data.translation += ev.text; scheduleRender(ev.id); } break;
    case "translation_final": if (it) { it.data.translation = ev.text; it.data.done = true; scheduleRender(ev.id); } break;
    case "thinking_start": renderThinking({ id: ev.id, text: "" }); break;
    case "thinking_delta": if (it) { it.data.text += ev.text; scheduleRender(ev.id); } break;
    case "thinking_final": if (it && !ev.text.trim()) { it.el.remove(); items.delete(ev.id); } break;
    case "action": renderAction(ev); break;
    case "action_result":
      if (it) { Object.assign(it.data, ev); it.render(); }
      if (ev.thumb && ev.name === "screenshot") setScreen(ev.thumb);
      break;
    case "discard": if (it) { it.el.remove(); items.delete(ev.id); } break;
    case "notice": renderNotice(ev); break;
    case "compacted": renderCompacted(ev); break;
    case "turn_usage": renderTurnUsage(ev); break;
    case "usage": setUsage(ev); break;
    case "approval_request": renderApproval(ev); break;
    case "approval_resolved":
      if (it && !it.el.classList.contains("done")) {
        it.el.classList.add("done");
        const row = it.el.querySelector(".row");
        if (row) row.replaceChildren(el("span", "muted small", t("approved")));
      }
      break;
    case "memory_op":
      memBadge(`${t("memUpdated")}: ${ev.path}`);
      if (!$("memoryDrawer").hidden) loadMemory(ev.path);
      break;
    case "memory_pass":
      if (ev.state === "running") memBadge(t("memUpdating"), true);
      else if (ev.summary && !/^no changes/i.test(ev.summary)) memBadge(`${t("memUpdated")}: ${ev.summary}`);
      else $("memBadge").hidden = true;
      break;
    case "turn_done":
      if (ev.chat) chatMeta = ev.chat;
      setUsage(ev); renderSidebar();
      document.querySelectorAll("details.activity").forEach((d) => (d.open = false));
      break;
  }
}

/* -------------------------------------------------------------- composer */
const input = $("input");
// Grow with the text. The box is border-box, so its height must include the border,
// or the text overflows by 2px and a scrollbar appears. Scroll only past the cap.
function autosize() {
  input.style.height = "auto";
  const want = input.scrollHeight + (input.offsetHeight - input.clientHeight);
  const max = window.innerHeight * 0.4;
  input.style.height = Math.min(want, max) + "px";
  input.style.overflowY = want > max ? "auto" : "hidden";
}
input.addEventListener("input", autosize);
window.addEventListener("resize", autosize);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault();
    $("composer").requestSubmit();
  }
});
$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  if (busy) return;
  if (!settings.language) { renderLangGate(); return; }
  const text = input.value;
  if (!text.trim() && !attachments.length) return;
  send({ type: "send", text, images: attachments.map((a) => ({ media_type: a.type, data: a.data })) });
  input.value = ""; autosize();
  attachments = []; renderAttachments();
  scrollDown(true);
});
$("btnStop").onclick = () => send({ type: "stop" });
$("btnAttach").onclick = () => $("fileInput").click();
$("fileInput").onchange = (e) => { [...e.target.files].forEach(addFile); e.target.value = ""; };
input.addEventListener("paste", (e) => {
  const files = [...((e.clipboardData || {}).items || [])].filter((i) => i.type.startsWith("image/")).map((i) => i.getAsFile());
  if (files.length) { e.preventDefault(); files.forEach(addFile); }
});
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => {
  e.preventDefault();
  [...((e.dataTransfer || {}).files || [])].filter((f) => f.type.startsWith("image/")).forEach(addFile);
});
function addFile(f) {
  if (!f || attachments.length >= 6) return;
  const r = new FileReader();
  r.onload = () => {
    const [head, data] = String(r.result).split(",");
    attachments.push({ type: head.slice(5, head.indexOf(";")), data, url: r.result });
    renderAttachments();
  };
  r.readAsDataURL(f);
}
function renderAttachments() {
  const box = $("attachments");
  box.replaceChildren();
  attachments.forEach((a, i) => {
    const w = el("div", "att"); const im = el("img"); im.src = a.url;
    const x = el("button", "", "✕"); x.type = "button"; x.onclick = () => { attachments.splice(i, 1); renderAttachments(); };
    w.append(im, x); box.appendChild(w);
  });
}

/* ---------------------------------------------------------------- header */
function fillLang() {
  const sel = $("langSelect");
  sel.replaceChildren();
  if (!settings.language) {
    const o = el("option", "", t("choose")); o.value = ""; o.disabled = true; sel.appendChild(o);
  }
  (meta.languages || []).forEach((l) => { const o = el("option", "", l); o.value = l; sel.appendChild(o); });
  sel.value = settings.language || "";
  renderLangGate();
}
async function chooseLanguage(lang) {
  try { await api("POST", "/api/settings", { language: lang }); }
  catch (err) { alertNotice(err.message); }
}
$("langSelect").onchange = (e) => chooseLanguage(e.target.value);
// Until a language is chosen, show the picker and keep the composer locked.
function renderLangGate() {
  const need = !settings.language;
  $("langModal").hidden = !need;
  input.disabled = need;
  if (!need) return;
  const grid = $("langGrid");
  grid.replaceChildren();
  (meta.languages || []).forEach((l) => {
    const b = el("button", "", l); b.type = "button"; b.onclick = () => chooseLanguage(l);
    grid.appendChild(b);
  });
}
const screenPanel = $("screenPanel");
screenPanel.hidden = !store.get("pa.screen", true);
$("btnScreen").onclick = () => { screenPanel.hidden = !screenPanel.hidden; store.set("pa.screen", !screenPanel.hidden); };

/* ------------------------------------------------- screen-sharing switch */
function renderShareSwitch() {
  const mode = settings.capture_mode === "screen" ? "screen" : "window";
  const sw = $("shareSwitch");
  sw.title = t("shareHint");
  sw.querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.mode === mode)));
  sw.parentElement.hidden = settings.computer_use === false;
}
$("shareSwitch").querySelectorAll("button").forEach((b) => {
  b.onclick = async () => {
    if (settings.capture_mode === b.dataset.mode) return;
    settings.capture_mode = b.dataset.mode; renderShareSwitch();          // instant feedback
    try { await api("POST", "/api/settings", { capture_mode: b.dataset.mode }); }
    catch (err) { alertNotice(err.message); }
  };
});

/* -------------------------------------------------------------- settings */
const form = $("settingsForm");
function fillSelect(name, values) {
  const s = form.elements[name]; if (!s) return;
  s.replaceChildren(); values.forEach((v) => { const o = el("option", "", v); o.value = v; s.appendChild(o); });
}
// Option text in the UI language; the value sent to the server stays the API value.
function labelOptions(name, text) {
  [...(form.elements[name] || { options: [] }).options].forEach((o) => (o.textContent = text(o.value)));
}
const optText = (prefix) => (v) => (STR[uiLang()] || {})[prefix + v] || STR.en[prefix + v] || v;
// Connected displays: size and where each sits relative to the main one. With one display
// there is nothing to choose, so the row is hidden.
function fillDisplays() {
  const displays = meta.displays || [];
  const sel = form.elements.monitor;
  sel.replaceChildren();
  displays.forEach((d) => {
    const o = el("option", "", `${d.index}. ${d.width} × ${d.height} · ${t("disp_" + d.position)}`);
    o.value = d.index; sel.appendChild(o);
  });
  $("monitorRow").hidden = displays.length <= 1;
}
function fillSettingsForm() {
  fillSelect("effort", meta.efforts || []); fillSelect("translator_effort", meta.efforts || []);
  fillSelect("shell_mode", meta.approval_modes || []); fillSelect("file_tools_mode", meta.approval_modes || []);
  fillSelect("capture_mode", meta.capture_modes || ["window", "screen"]);
  fillSelect("window_mode", meta.window_modes || ["app"]);
  ["effort", "translator_effort"].forEach((n) => labelOptions(n, optText("effort_")));
  ["shell_mode", "file_tools_mode"].forEach((n) => labelOptions(n, optText("mode_")));
  labelOptions("capture_mode", (v) => t(v === "screen" ? "shareScreen" : "shareWindow"));
  labelOptions("window_mode", (v) => t("win_" + v));
  fillDisplays();
  const dl = $("modelList"); dl.replaceChildren(); (meta.models || []).forEach((m) => { const o = el("option"); o.value = m; dl.appendChild(o); });
  for (const [k, v] of Object.entries(settings)) {
    const f = form.elements[k]; if (!f || k === "api_key") continue;
    if (f.type === "checkbox") f.checked = !!v; else f.value = v;
  }
  // 0 (or a display that's gone) means the main display
  const displays = meta.displays || [];
  if (!displays.some((d) => d.index === settings.monitor)) {
    const main = displays.find((d) => d.position === "primary") || displays[0];
    form.elements.monitor.value = main ? main.index : "";
  }
  form.elements.api_key.value = "";
  $("keyHint").textContent = settings.api_key_set ? `✓ ${settings.api_key_hint || t("keySet")}` : t("keyNotSet");
  $("dataDirText").textContent = meta.data_dir || "";
  $("dataChangeBtn").hidden = !!meta.data_dir_fixed;   // fixed by POLYGLOT_DATA_DIR
}
$("btnSettings").onclick = async () => {
  try { meta = (await api("GET", "/api/settings")).meta; } catch { /* keep what we have */ }   // displays may have changed
  fillSettingsForm(); $("btnQuit").hidden = !meta.desktop; $("settingsModal").hidden = false;
};
$("btnQuit").onclick = () => twoClick($("btnQuit"), t("quitApp"), async () => {
  try { await api("POST", "/api/quit"); } catch { /* server is going away */ }
  $("settingsModal").hidden = true;
  document.body.replaceChildren(el("div", "empty", t("quitting")));
  window.close();
});
$("setClose").onclick = () => ($("settingsModal").hidden = true);
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const upd = {};
  [...form.elements].forEach((f) => {
    if (!f.name) return;
    if (f.type === "checkbox") upd[f.name] = f.checked;
    else if (f.type === "number") upd[f.name] = Number(f.value);
    else if (f.name !== "api_key" || f.value.trim()) upd[f.name] = f.value.trim();
  });
  try {
    await api("POST", "/api/settings", upd);
    $("setStatus").textContent = t("saved");
    setTimeout(() => { $("setStatus").textContent = ""; $("settingsModal").hidden = true; }, 600);
  } catch (err) { $("setStatus").textContent = err.message; }
});

/* ----------------------------------------------------------- data folder */
// "setup": first start, nothing works until a folder is chosen. "change": from Settings.
let dataMode = null, dataPicked = "";
async function showDataTarget(path) {
  const j = await api("POST", "/api/data-dir/preview", { path });
  dataPicked = path;
  $("dataPath").textContent = j.target;
  $("dataNote").textContent = j.current ? t("dataCurrent") : j.existing ? t("dataExisting")
    : dataMode === "change" ? t("dataCopy") : t("dataNew");
}
function openDataModal(mode, setup) {
  dataMode = mode;
  $("dataCancel").hidden = mode === "setup";
  $("dataLangs").hidden = mode !== "setup" || !!settings.language;
  $("langModal").hidden = true;
  input.disabled = mode === "setup" || !settings.language;
  $("dataModal").hidden = false;
  const start = mode === "setup" ? setup.suggested : meta.data_dir;
  showDataTarget(start).then(() => {
    if (setup && setup.missing) $("dataNote").textContent = `${t("dataMissing")} ${setup.missing}`;
  }).catch((err) => ($("dataNote").textContent = err.message));
}
$("dataBrowse").onclick = async () => {
  const p = await pickFolder(t("pickDataTitle"), dataPicked);
  if (p) showDataTarget(p).catch((err) => ($("dataNote").textContent = err.message));
};
$("dataUse").onclick = async () => {
  const btn = $("dataUse");
  btn.disabled = true;
  try {
    await api("POST", "/api/data-dir", { path: dataPicked });
    $("dataModal").hidden = true;    // the server sends a fresh "hello" for the new folder
    dataMode = null;
  } catch (err) { $("dataNote").textContent = err.message; }
  finally { btn.disabled = false; }
};
$("dataCancel").onclick = () => { $("dataModal").hidden = true; dataMode = null; renderLangGate(); };
$("dataChangeBtn").onclick = () => { $("settingsModal").hidden = true; openDataModal("change"); };
$("dataOpenBtn").onclick = () => api("POST", "/api/data-dir/open").catch((err) => alertNotice(err.message));

/* ---------------------------------------------------------------- memory */
let memCurrent = "";
async function loadMemory(flashPath) {
  const j = await api("GET", "/api/memory");
  $("memDir").textContent = j.dir;
  const list = $("memList"); list.replaceChildren();
  j.files.forEach((f) => {
    const li = el("li", f.path === memCurrent ? "active" : "", f.path.replace("/memories/", ""));
    li.title = `${f.path} · ${f.size} B`;
    li.onclick = () => openMemFile(f.path);
    if (f.path === flashPath) { li.classList.add("flash"); setTimeout(() => li.classList.remove("flash"), 1500); }
    list.appendChild(li);
  });
  if (flashPath && flashPath === memCurrent) openMemFile(memCurrent);
  memUndoInfo = j.undo || null; renderMemUndo();
  return j;
}
async function openMemFile(path) {
  try {
    const j = await api("GET", "/api/memory/file?path=" + encodeURIComponent(path));
    memCurrent = path; $("memPath").value = path; $("memText").value = j.content; $("memStatus").textContent = "";
    document.querySelectorAll("#memList li").forEach((li) => li.classList.toggle("active", li.title.startsWith(path + " ")));
    applyMemView();
  } catch (err) { $("memStatus").textContent = err.message; }
}
$("btnMemory").onclick = () => { $("memoryDrawer").hidden = false; loadMemory(); applyMemView(); };
$("memClose").onclick = () => ($("memoryDrawer").hidden = true);
$("memNew").onclick = () => {
  memCurrent = ""; $("memPath").value = "/memories/"; $("memText").value = ""; applyMemView(); $("memPath").focus();
};

/* One-click translation. Memory stays English on disk (the agent reads it); this
   shows a read-only translation in the user's language. Once switched on, it stays
   on for every file opened, until "Show original" is clicked. */
let memTrOn = store.get("pa.memTranslate", false), memTrSeq = 0;
function memCanTranslate() { return !!settings.language && settings.language !== "English"; }
function setMemTranslated(on) {
  $("memText").hidden = on; $("memView").hidden = !on; $("memSave").disabled = on;
  if (!on && $("memStatus").textContent === t("memTranslatedNote")) $("memStatus").textContent = "";
}
async function applyMemView() {
  const btn = $("memTranslate"), seq = ++memTrSeq, text = $("memText").value;
  const label = () => { btn.disabled = false; btn.textContent = memTrOn ? t("showOriginal") : t("translateMem"); };
  btn.hidden = !memCanTranslate();
  btn.title = settings.language ? `${t("translateMem")} → ${settings.language}` : "";
  label();
  if (!memTrOn || !memCanTranslate() || !text.trim()) { setMemTranslated(false); return; }
  btn.disabled = true; btn.textContent = t("translatingMem");
  try {
    const j = await api("POST", "/api/memory/translate", { content: text });
    if (seq !== memTrSeq) return;                 // another file was opened meanwhile
    renderMd($("memView"), j.text);
    setMemTranslated(true);
    $("memStatus").textContent = t("memTranslatedNote");
  } catch (err) {
    if (seq !== memTrSeq) return;
    memTrOn = false; store.set("pa.memTranslate", false);
    setMemTranslated(false);
    $("memStatus").textContent = err.message;
  } finally {
    if (seq === memTrSeq) label();
  }
}
$("memTranslate").onclick = () => { memTrOn = !memTrOn; store.set("pa.memTranslate", memTrOn); applyMemView(); };
$("memSave").onclick = async () => {
  try {
    await api("PUT", "/api/memory/file", { path: $("memPath").value.trim(), content: $("memText").value });
    memCurrent = $("memPath").value.trim(); $("memStatus").textContent = t("saved"); loadMemory();
  } catch (err) { $("memStatus").textContent = err.message; }
};
$("memDelete").onclick = () => twoClick($("memDelete"), t("delete"), async () => {
  try {
    await api("DELETE", "/api/memory/file?path=" + encodeURIComponent($("memPath").value.trim()));
    memCurrent = ""; $("memText").value = ""; $("memPath").value = ""; applyMemView(); loadMemory();
  } catch (err) { $("memStatus").textContent = err.message; }
});

/* Clean-up: "Tidy up" (AI merges duplicates, drops stale lines) and "Clear all".
   The server backs memory up before either, and "Undo" puts the last backup back. */
let memUndoInfo = null;
function renderMemUndo() {
  const b = $("memUndo");
  b.hidden = !memUndoInfo;
  if (memUndoInfo) b.title = `${t("undoTitle")} (${new Date(memUndoInfo.time * 1000).toLocaleString()})`;
}
function memBanner(text, isError, note) {
  const b = $("memBanner");
  b.replaceChildren(el("span", "", text || ""));
  if (note) b.appendChild(el("div", "muted small", note));
  b.hidden = !text; b.classList.toggle("error", !!isError);
}
async function memClean(btn, path, busyLabel, doneText) {
  const btns = [$("memUndo"), $("memTidy"), $("memClear")];
  const label = btn.textContent;
  btns.forEach((b) => (b.disabled = true));
  btn.textContent = busyLabel; memBanner("");
  try {
    const j = await api("POST", path);
    memBanner(doneText(j), false, t("memCleanNote"));
    const tree = await loadMemory();
    if (memCurrent && !tree.files.some((f) => f.path === memCurrent)) {
      memCurrent = ""; $("memPath").value = ""; $("memText").value = ""; applyMemView();
    } else if (memCurrent) openMemFile(memCurrent);
  } catch (err) {
    memBanner(err.status === 409 ? t("memBusy") : err.message, true);
  } finally {
    btn.textContent = label;
    btns.forEach((b) => (b.disabled = false));
  }
}
$("memTidy").onclick = () => memClean($("memTidy"), "/api/memory/tidy", t("tidyingMem"),
  (j) => `${t("memTidied")}: ${j.summary}`);
$("memClear").onclick = () => twoClick($("memClear"), t("clearMem"),
  () => memClean($("memClear"), "/api/memory/clear", t("clearMem"), () => t("memCleared")));
$("memUndo").onclick = () => memClean($("memUndo"), "/api/memory/undo", t("undoClean"), () => t("memRestored"));

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    ["viewer", "settingsModal", "memoryDrawer", "projectModal", "usagePop"].forEach((id) => ($(id).hidden = true));
    if (dataMode === "change") $("dataCancel").click();
  }
});

applyI18n();
connect();
setInterval(() => send({ type: "ping" }), 25000);
