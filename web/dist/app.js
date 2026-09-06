const state = {
  sid: null,
  worldId: "qinghsi",
  saveName: "main",
  worldName: null,
  candidates: [],
  currentCandidateId: null,
  currentTurnId: null,
  currentMessageEl: null,
  worldData: null,
  editMode: false,
};

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return res.json();
  }
  return res.text();
}

function $(sel) {
  return document.querySelector(sel);
}

function addMessage(role, text) {
  const box = document.createElement("div");
  box.className = `message ${role}`;
  const textEl = document.createElement("div");
  textEl.className = "message-text";
  textEl.textContent = text;
  box.appendChild(textEl);
  $("#messages").appendChild(box);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  return box;
}

/* ---------- 候选切换 ---------- */

function clearCandidateControls() {
  if (state.currentMessageEl) {
    const ctrl = state.currentMessageEl.querySelector(".message-controls");
    if (ctrl) ctrl.remove();
  }
  state.candidates = [];
  state.currentCandidateId = null;
  state.currentTurnId = null;
  state.currentMessageEl = null;
}

function renderCandidateMessage() {
  if (!state.candidates.length) {
    clearCandidateControls();
    return;
  }

  const cand =
    state.candidates.find((c) => c.candidate_id === state.currentCandidateId) ||
    state.candidates[0];
  state.currentCandidateId = cand.candidate_id;
  state.currentTurnId = cand.turn_id;

  if (!state.currentMessageEl) {
    state.currentMessageEl = addMessage("npc", "");
  }

  const textEl = state.currentMessageEl.querySelector(".message-text");
  textEl.textContent = cand.prose;

  let ctrl = state.currentMessageEl.querySelector(".message-controls");
  if (!ctrl) {
    ctrl = document.createElement("div");
    ctrl.className = "message-controls";
    state.currentMessageEl.appendChild(ctrl);
  }
  ctrl.innerHTML = "";

  const idx = state.candidates.findIndex((c) => c.candidate_id === cand.candidate_id);

  const prev = document.createElement("button");
  prev.textContent = "◀";
  prev.title = "上一个候选";
  prev.onclick = () => switchCandidate(-1);

  const counter = document.createElement("span");
  counter.className = "counter";
  counter.textContent = `${idx + 1}/${state.candidates.length}`;

  const next = document.createElement("button");
  next.textContent = "▶";
  next.title = "下一个候选";
  next.onclick = () => switchCandidate(1);

  const reroll = document.createElement("button");
  reroll.textContent = "🎲";
  reroll.title = "生成一个新候选";
  reroll.onclick = () => rerollCurrent();

  const adopt = document.createElement("button");
  adopt.className = "adopt";
  adopt.textContent = "✓";
  adopt.title = "采纳当前（静默）";
  adopt.onclick = () => adoptCurrent();

  const discard = document.createElement("button");
  discard.className = "danger";
  discard.textContent = "放弃";
  discard.title = "放弃整个回合";
  discard.onclick = () => discardCurrentTurn();

  ctrl.append(prev, counter, next, reroll, adopt, discard);
}

function switchCandidate(delta) {
  if (!state.candidates.length) return;
  const idx = state.candidates.findIndex((c) => c.candidate_id === state.currentCandidateId);
  const nextIdx = (idx + delta + state.candidates.length) % state.candidates.length;
  state.currentCandidateId = state.candidates[nextIdx].candidate_id;
  renderCandidateMessage();
}

async function rerollCurrent() {
  if (!state.currentTurnId) return;
  setPipelineStatus("正在重抽…");
  try {
    const data = await api(`/api/sessions/${state.sid}/turns/${state.currentTurnId}/reroll`, {
      method: "POST",
      body: JSON.stringify({ mode: "rephrase", note: "" }),
    });
    state.candidates.push(data);
    state.currentCandidateId = data.candidate_id;
    renderCandidateMessage();
  } catch (e) {
    clearPipelineStatus();
    addMessage("npc", `⚠ 重抽失败：${e.message}`);
  } finally {
    clearPipelineStatus();
  }
}

async function adoptCurrent() {
  if (!state.currentCandidateId) return;
  setPipelineStatus("正在采纳…");
  try {
    await api(`/api/sessions/${state.sid}/candidates/${state.currentCandidateId}/adopt`, {
      method: "POST",
    });
    clearCandidateControls();
    await refreshState();
  } catch (e) {
    clearPipelineStatus();
    addMessage("npc", `⚠ 采纳失败：${e.message}`);
  } finally {
    clearPipelineStatus();
  }
}

async function discardCurrentTurn() {
  if (!state.currentTurnId) return;
  await api(`/api/sessions/${state.sid}/turns/${state.currentTurnId}/discard`, {
    method: "POST",
  });
  clearCandidateControls();
  await refreshState();
}

/* ---------- 会话 ---------- */

async function loadEventHistory() {
  if (!state.sid) return;
  const data = await api(`/api/sessions/${state.sid}/ledger/events`);
  const events = data.events || [];
  if (!events.length) return;
  $("#messages").innerHTML = "";
  for (const ev of events) {
    if (ev.player_input) addMessage("player", ev.player_input);
    addMessage("npc", ev.body || "");
  }
}

async function ensureSession() {
  const info = await api(`/api/worlds/${state.worldId}`);
  if (info.saves.includes(state.saveName)) {
    const data = await api(`/api/sessions/open`, {
      method: "POST",
      body: JSON.stringify({ world_id: state.worldId, save_name: state.saveName }),
    });
    state.sid = data.sid;
  } else {
    const data = await api(`/api/sessions`, {
      method: "POST",
      body: JSON.stringify({ world_id: state.worldId, save_name: state.saveName }),
    });
    state.sid = data.sid;
  }
}

async function refreshState() {
  if (!state.sid) return;
  const data = await api(`/api/sessions/${state.sid}/state`);
  $("#world-name").textContent = state.worldName || "-";
  $("#clock").textContent = data.clock || "-";
  $("#scene").textContent = data.scene_name || data.scene || "-";
  if (data.preset) {
    const p = data.preset;
    $("#writer-guidelines-input").value = p.writer_guidelines || "";
    $("#banned-words-input").value = (p.banned_words || []).join(", ");
  }
  renderLeftRail(data);
}

function renderLeftRail(data) {
  const nav = $("#scene-nav");
  nav.innerHTML = "";
  const current = document.createElement("div");
  current.className = "rail-item current";
  current.textContent = `📍 ${data.scene_name || "-"}`;
  nav.appendChild(current);

  for (const adj of data.adjacent || []) {
    const item = document.createElement("div");
    item.className = "rail-item";
    item.textContent = adj.name;
    nav.appendChild(item);
  }

  const present = $("#present-npcs");
  present.innerHTML = "";
  const names = data.present_names || [];
  if (!names.length) {
    present.innerHTML = '<div class="rail-item">无人</div>';
  } else {
    for (const name of names) {
      const item = document.createElement("div");
      item.className = "rail-item";
      item.textContent = name;
      present.appendChild(item);
    }
  }
}

async function syncPendingFromServer() {
  if (!state.sid) return;
  const data = await api(`/api/sessions/${state.sid}/candidates/pending`);
  const pending = data.candidates || [];
  if (!pending.length) {
    clearCandidateControls();
    return;
  }

  const first = pending[0];
  const turnCands = pending.filter((c) => c.turn_id === first.turn_id);
  const currentStillPending = state.currentCandidateId
    ? pending.some((c) => c.candidate_id === state.currentCandidateId)
    : false;

  if (!state.currentMessageEl || !currentStillPending) {
    state.candidates = turnCands;
    state.currentCandidateId = first.candidate_id;
    state.currentTurnId = first.turn_id;
    state.currentMessageEl = null;
    renderCandidateMessage();
  } else {
    state.candidates = turnCands;
    renderCandidateMessage();
  }
}

/* ---------- 发送输入 ---------- */

function parseSSE(text) {
  const events = [];
  for (const block of text.split("\n\n")) {
    if (!block.trim()) continue;
    const lines = block.split("\n");
    let event = "message";
    let data = "";
    for (const line of lines) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (data) {
      try {
        events.push({ event, data: JSON.parse(data) });
      } catch (e) {
        events.push({ event, data });
      }
    }
  }
  return events;
}

function setPipelineStatus(text, active = true) {
  const el = $("#pipeline-status");
  el.innerHTML = active ? `<span class="spinner"></span>${text}` : text;
  el.classList.toggle("active", active);
}

function clearPipelineStatus() {
  setPipelineStatus("", false);
}

async function sendInput(text) {
  setPipelineStatus("正在连接…");
  const body = { input: text };
  if (state.currentCandidateId) {
    body.adopt_candidate_id = state.currentCandidateId;
    clearCandidateControls();
  }

  const res = await fetch(`/api/sessions/${state.sid}/turn`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    clearPipelineStatus();
    addMessage("npc", `⚠ HTTP ${res.status}`);
    await refreshState();
    return;
  }

  // Read the SSE stream incrementally so stage events update the pipeline
  // status in real time instead of appearing all at once at the end.
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      if (block.trim()) handleSSEBlock(block);
    }
  }
  if (buffer.trim()) handleSSEBlock(buffer);
  clearPipelineStatus();
  await refreshState();
}

function handleSSEBlock(block) {
  const lines = block.split("\n");
  let event = "message";
  let data = "";
  for (const line of lines) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  let payload;
  try {
    payload = JSON.parse(data);
  } catch (e) {
    payload = data;
  }
  if (event === "stage") {
    setPipelineStatus(payload.label || payload.stage || "处理中");
  } else if (event === "candidate") {
    clearPipelineStatus();
    state.candidates = [payload];
    state.currentCandidateId = payload.candidate_id;
    state.currentTurnId = payload.turn_id;
    state.currentMessageEl = null;
    renderCandidateMessage();
  } else if (event === "error") {
    clearPipelineStatus();
    addMessage("npc", `⚠ ${payload.message || "错误"}`);
  }
}

/* ---------- 抽屉 ---------- */

function openDrawer(tabName) {
  switchDrawerTab(tabName);
  $("#drawer").classList.add("open");
}

function closeDrawer() {
  $("#drawer").classList.remove("open");
}

function switchDrawerTab(tabName) {
  document.querySelectorAll(".drawer-tabs .tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabName);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${tabName}`);
  });
  if (tabName === "debug") {
    loadDebugTrace();
  } else if (tabName === "player") {
    loadPlayer();
  }
}

async function loadDebugTrace() {
  if (!state.sid) return;
  const data = await api(`/api/sessions/${state.sid}/debug/latest`);
  const trace = data.trace || [];
  $("#debug-output").textContent = trace.length
    ? JSON.stringify(trace, null, 2)
    : "暂无调试数据";
}

/* ---------- 主角资料 ---------- */

async function loadPlayer() {
  if (!state.sid) return;
  const data = await api(`/api/sessions/${state.sid}/player`);
  $("#player-name").value = data.name || "";
  $("#player-appearance").value = data.appearance || "";
  $("#player-persona").value = data.persona || "";
  $("#player-background").value = data.background || "";
}

async function savePlayer() {
  await api(`/api/sessions/${state.sid}/player`, {
    method: "PUT",
    body: JSON.stringify({
      name: $("#player-name").value,
      appearance: $("#player-appearance").value,
      persona: $("#player-persona").value,
      background: $("#player-background").value,
    }),
  });
  alert("主角资料已保存");
}

/* ---------- 导演对话 ---------- */

function addDirectorMsg(role, text) {
  const box = document.createElement("div");
  box.className = `director-msg ${role}`;
  box.textContent = text;
  $("#director-chat").appendChild(box);
  $("#director-chat").scrollTop = $("#director-chat").scrollHeight;
}

async function loadDirectorHistory() {
  if (!state.sid) return;
  const data = await api(`/api/sessions/${state.sid}/director/history`);
  $("#director-chat").innerHTML = "";
  for (const item of data.history || []) {
    addDirectorMsg(item.role === "user" ? "user" : "assistant", item.content);
  }
}

async function sendDirectorMessage() {
  const input = $("#director-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addDirectorMsg("user", text);
  const data = await api(`/api/sessions/${state.sid}/director`, {
    method: "POST",
    body: JSON.stringify({ topic: "chat", message: text }),
  });
  addDirectorMsg("assistant", data.reply || "（导演没有回复）");
  if (data.pending_action) {
    renderPendingAction(data.pending_action);
  }
}

async function renderPendingAction(action) {
  const box = document.createElement("div");
  box.className = "director-msg pending-action";
  const desc = describeAction(action);
  const label = document.createElement("div");
  label.textContent = `待确认操作：${desc}`;
  box.appendChild(label);
  const actions = document.createElement("div");
  actions.className = "pending-action-buttons";
  const confirm = document.createElement("button");
  confirm.textContent = "确认执行";
  confirm.className = "danger";
  confirm.onclick = async () => {
    box.remove();
    try {
      await api(`/api/sessions/${state.sid}/director`, {
        method: "POST",
        body: JSON.stringify({ topic: "confirm", action }),
      });
      addDirectorMsg("assistant", `✓ 已执行：${describeAction(action)}`);
      await refreshState();
    } catch (e) {
      addDirectorMsg("assistant", `⚠ 执行失败：${e.message}`);
    }
  };
  const cancel = document.createElement("button");
  cancel.textContent = "取消";
  cancel.onclick = () => {
    box.remove();
    addDirectorMsg("assistant", "已取消该操作");
  };
  actions.appendChild(confirm);
  actions.appendChild(cancel);
  box.appendChild(actions);
  $("#director-chat").appendChild(box);
  $("#director-chat").scrollTop = $("#director-chat").scrollHeight;
}

function describeAction(action) {
  const p = action.payload || {};
  switch (action.type) {
    case "override":
      return `静默覆写：${p.subject || "?"} 在 ${p.location || "?"}`;
    case "set_actor":
      return `角色档位：${p.npc_id || "?"} ${p.has_actor === false ? "取消配" : "配"} Actor`;
    case "inject_memory":
      return `记忆注入：给 ${p.npc_id || "?"} 注入记忆`;
    case "access_rejudge":
      return `事件改判：${p.event_id || "?"} → ${p.known_by ? "私密" : "公开"}`;
    case "amend_card":
      return `补卡事务：${p.npc_id || "?"} 增补人物卡`;
    default:
      return `${action.type || "?"} ${JSON.stringify(p)}`;
  }
}

/* ---------- 预设 ---------- */

async function savePreset() {
  await api(`/api/presets`, {
    method: "PUT",
    body: JSON.stringify({
      writer_guidelines: $("#writer-guidelines-input").value,
      banned_words: splitList($("#banned-words-input").value),
    }),
  });
  alert("编剧准则已保存");
}

/* ---------- 系统设置 ---------- */

async function loadSettings() {
  const data = await api(`/api/settings`);
  $("#setting-api-url").value = data.llm_base_url || "";
  $("#setting-api-key").value = data.llm_api_key || "";
  $("#setting-model-main").value = data.model_main || "";
  $("#setting-model-cheap").value = data.model_cheap || "";

  const fontSize = localStorage.getItem("aiworld_font_size") || "14";
  const theme = localStorage.getItem("aiworld_theme") || "dark";
  $("#setting-font-size").value = fontSize;
  $("#setting-theme").value = theme;
  applyFontSize(fontSize);
  applyTheme(theme);
  await loadUsage();
}

async function loadUsage() {
  const data = await api(`/api/settings/usage`);
  $("#usage-calls").textContent = data.calls || 0;
  $("#usage-prompt").textContent = data.prompt_tokens || 0;
  $("#usage-completion").textContent = data.completion_tokens || 0;
  $("#usage-total").textContent = data.total_tokens || 0;
}

async function saveSettings() {
  await api(`/api/settings`, {
    method: "PUT",
    body: JSON.stringify({
      llm_base_url: $("#setting-api-url").value,
      llm_api_key: $("#setting-api-key").value,
      model_main: $("#setting-model-main").value,
      model_cheap: $("#setting-model-cheap").value,
    }),
  });

  const fontSize = $("#setting-font-size").value;
  const theme = $("#setting-theme").value;
  localStorage.setItem("aiworld_font_size", fontSize);
  localStorage.setItem("aiworld_theme", theme);
  applyFontSize(fontSize);
  applyTheme(theme);
  alert("系统设置已保存");
}

function applyFontSize(size) {
  document.documentElement.style.fontSize = `${size}px`;
}

function applyTheme(theme) {
  document.body.classList.toggle("light", theme === "light");
}

/* ---------- 世界浏览器 ---------- */

function openWorldModal() {
  $("#world-modal").classList.add("open");
  switchWorldTab("list");
  loadWorldList();
}

function closeWorldModal() {
  $("#world-modal").classList.remove("open");
}

function switchWorldTab(tabName) {
  document.querySelectorAll(".modal-tabs .tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabName);
  });
  document.querySelectorAll(".world-tab").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `world-tab-${tabName}`);
  });
  if (tabName !== "list" && !state.worldData) {
    loadWorldBrowser();
  } else if (tabName !== "list" && state.worldData && state.editMode) {
    renderWorldEdit();
  }
}

async function loadWorldList() {
  const data = await api(`/api/worlds`);
  const wrap = $("#world-tab-list");
  wrap.innerHTML = "<h3>世界列表</h3>";
  const worlds = data.worlds || [];
  if (!worlds.length) {
    wrap.innerHTML += '<div class="empty">暂无世界，请导入资产包。</div>';
    return;
  }
  for (const world of worlds) {
    const item = document.createElement("div");
    item.className = "item world-list-item";

    const label = document.createElement("div");
    label.innerHTML = `<strong>${world.name || world.id}</strong> <span class="muted">${world.id}</span>`;
    item.appendChild(label);

    const actions = document.createElement("div");
    actions.className = "world-list-actions";

    const openBtn = document.createElement("button");
    openBtn.textContent = "打开";
    openBtn.onclick = () => switchToWorld(world.id);
    actions.appendChild(openBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "danger";
    deleteBtn.textContent = "删除";
    deleteBtn.onclick = () => deleteWorld(world.id);
    actions.appendChild(deleteBtn);

    item.appendChild(actions);
    wrap.appendChild(item);
  }
}

async function switchToWorld(worldId) {
  state.worldId = worldId;
  state.worldName = null;
  state.worldData = null;
  setEditModeUI(false);
  await ensureSession();
  const info = await api(`/api/sessions/${state.sid}`);
  state.worldName = info.world;
  $("#world-name").textContent = info.world;
  await loadEventHistory();
  await refreshState();
  await syncPendingFromServer();
  await loadDirectorHistory();
  await loadWorldBrowser();
  switchWorldTab("overview");
}

async function deleteWorld(worldId) {
  if (worldId === state.worldId) {
    alert("不能删除当前正在使用的世界，请先切换到其他世界再删除。");
    return;
  }
  if (!confirm(`确定删除世界“${worldId}”？它的存档也会一起清除。`)) return;
  await api(`/api/worlds/${worldId}`, { method: "DELETE" });
  await loadWorldList();
}

async function loadWorldBrowser() {
  const data = await api(`/api/sessions/${state.sid}/world`);
  state.worldData = data;

  const overview = $("#world-tab-overview");
  overview.innerHTML = "";
  const durations = data.overview.default_durations || {};
  const durationLabels = {
    move_per_edge_min: "移动耗时（分钟/条相邻边）",
    action_default_min: "普通行动耗时（分钟）",
  };
  const durationItems = Object.entries(durations)
    .map(([key, value]) => `<li>${durationLabels[key] || key}：${value} 分钟</li>`)
    .join("");
  overview.innerHTML = `
    <h3>${data.overview.name || data.overview.id}</h3>
    <pre>${(data.overview.summary || []).join("\n")}</pre>
    ${data.overview.opening ? `<p><strong>开场白</strong></p><pre>${escapeHtml(data.overview.opening)}</pre>` : ""}
    <p><strong>默认耗时</strong></p>
    <ul>${durationItems || "<li>无</li>"}</ul>
  `;

  const lore = $("#world-tab-lore");
  lore.innerHTML = "<h3>世界书</h3>";
  for (const entry of data.lorebook || []) {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `<strong>${entry.summary || entry.id}</strong><br>${entry.body || ""}`;
    lore.appendChild(div);
  }

  const scenes = $("#world-tab-scenes");
  scenes.innerHTML = "<h3>场景</h3>";
  for (const scene of data.scenes || []) {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `<strong>${scene.name}</strong> (${scene.id})<br>${scene.perceivable || ""}<br>邻接：${(scene.adjacent || []).join(", ")}`;
    scenes.appendChild(div);
  }

  const npcs = $("#world-tab-npcs");
  npcs.innerHTML = "<h3>人物</h3>";
  for (const npc of Object.values(data.npcs || {})) {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `<strong>${npc.name}</strong> (${npc.id})<br>${npc.persona || ""}`;
    npcs.appendChild(div);
  }

  const axes = $("#world-tab-axes");
  axes.innerHTML = "<h3>数值属性</h3>";
  const axisList = data.axes || [];
  if (!axisList.length) {
    axes.innerHTML += '<div class="empty">暂无数值属性（关系系统二期启用）</div>';
  }
  for (const axis of axisList) {
    const div = document.createElement("div");
    div.className = "item";
    div.textContent = `${axis.label || axis.id} (${axis.range?.[0] ?? "-"} ~ ${axis.range?.[1] ?? "-"})`;
    axes.appendChild(div);
  }

  const events = $("#world-tab-events");
  events.innerHTML = "<h3>事件日志流</h3>";
  (data.events || []).forEach((ev, index) => {
    const div = document.createElement("div");
    div.className = "item";
    const inputLine = ev.player_input ? `\n玩家：${ev.player_input}` : "";
    div.textContent = `#${index + 1} ${formatEventSummary(ev, data.scenes || [], data.npcs || {})}${inputLine}`;
    events.appendChild(div);
  });
}

function formatEventSummary(ev, scenes, npcs) {
  const scene = scenes.find((s) => s.id === ev.location);
  const location = scene ? scene.name : ev.location || "";
  const names = (ev.participants || [])
    .map((id) => (id === "player" ? "你" : npcs[id]?.name || id))
    .join("、");
  const locTag = location ? ` [${location}]` : "";
  const whoTag = names ? `（在场：${names}）` : "";
  const summary = ev.summary || (ev.body || "").replace(/\s+/g, " ").slice(0, 40);
  return `${ev.at || ""}${locTag} ${summary}${whoTag}`;
}

function setEditModeUI(active) {
  state.editMode = active;
  $("#toggle-edit").textContent = active ? "退出编辑" : "编辑模式";
  $("#save-world-edit").style.display = active ? "" : "none";
  $("#save-as-world").style.display = active ? "" : "none";
}

function toggleEditMode() {
  const next = !state.editMode;
  setEditModeUI(next);
  if (next) {
    renderWorldEdit();
  } else {
    loadWorldBrowser();
  }
}

function renderWorldEdit() {
  if (!state.worldData) return;
  const data = state.worldData;
  renderEditOverview(data);
  renderEditLore(data);
  renderEditScenes(data);
  renderEditNpcs(data);
  renderEditAxes(data);
  bindEditEvents();
}

function renderEditOverview(data) {
  const ov = data.overview || {};
  $("#world-tab-overview").innerHTML = `
    <h3>概览</h3>
    <div class="overview-layout">
      <div class="overview-left">
        <div class="form-section">
          <h4>基础信息</h4>
          <div class="field">
            <label>世界 ID</label>
            <input class="edit-field" data-field="id" value="${escapeHtml(ov.id || "")}" disabled />
          </div>
          <div class="field">
            <label>名称</label>
            <input class="edit-field" data-field="name" value="${escapeHtml(ov.name || "")}" />
          </div>
        </div>

        <div class="form-section">
          <h4>默认耗时</h4>
          <div class="field">
            <label>移动耗时（分钟/条相邻边）</label>
            <input class="edit-field" data-field="move" type="number" value="${ov.default_durations?.move_per_edge_min ?? 10}" />
          </div>
          <div class="field">
            <label>普通行动耗时（分钟）</label>
            <input class="edit-field" data-field="action" type="number" value="${ov.default_durations?.action_default_min ?? 30}" />
          </div>
        </div>
      </div>

      <div class="overview-right">
        <div class="form-section overview-summary-section">
          <h4>开场白（新建存档/重置后成为事件日志第一条，不随重置消失）</h4>
          <textarea class="edit-field overview-summary" data-field="opening" rows="4">${escapeHtml(ov.opening || "")}</textarea>
        </div>
        <div class="form-section overview-summary-section">
          <h4>概要</h4>
          <textarea class="edit-field overview-summary" data-field="summary" rows="8">${escapeHtml((ov.summary || []).join("\n"))}</textarea>
        </div>
      </div>
    </div>
  `;
}

function renderEditLore(data) {
  const items = data.lorebook || [];
  $("#world-tab-lore").innerHTML = `
    <h3>世界书</h3>
    <div class="edit-list" id="edit-lore-list">
      ${items.map((item, i) => loreCard(item, i)).join("")}
    </div>
    <button id="add-lore">添加世界书条目</button>
  `;
}

function loreCard(item, i) {
  return `
    <div class="edit-card" data-index="${i}">
      <div class="form-grid">
        <div class="field">
          <label>ID</label>
          <input class="edit-field" data-field="id" value="${escapeHtml(item.id || "")}" />
        </div>
        <div class="field">
          <label>标签（逗号分隔）</label>
          <input class="edit-field" data-field="tags" value="${escapeHtml((item.tags || []).join(", "))}" />
        </div>
      </div>
      <div class="field full">
        <label>摘要</label>
        <input class="edit-field" data-field="summary" value="${escapeHtml(item.summary || "")}" />
      </div>
      <div class="field full">
        <label>正文</label>
        <textarea class="edit-field" data-field="body" rows="3">${escapeHtml(item.body || "")}</textarea>
      </div>
      <button class="danger remove-item">删除</button>
    </div>
  `;
}

function renderEditScenes(data) {
  const items = data.scenes || [];
  $("#world-tab-scenes").innerHTML = `
    <h3>场景</h3>
    <div class="edit-list" id="edit-scene-list">
      ${items.map((item, i) => sceneCard(item, i)).join("")}
    </div>
    <button id="add-scene">添加场景</button>
  `;
}

function sceneCard(item, i) {
  return `
    <div class="edit-card" data-index="${i}">
      <div class="form-grid">
        <div class="field">
          <label>ID</label>
          <input class="edit-field" data-field="id" value="${escapeHtml(item.id || "")}" />
        </div>
        <div class="field">
          <label>名称</label>
          <input class="edit-field" data-field="name" value="${escapeHtml(item.name || "")}" />
        </div>
      </div>
      <div class="form-grid">
        <div class="field">
          <label>别名（逗号分隔）</label>
          <input class="edit-field" data-field="aliases" value="${escapeHtml((item.aliases || []).join(", "))}" />
        </div>
        <div class="field">
          <label>标签（逗号分隔）</label>
          <input class="edit-field" data-field="tags" value="${escapeHtml((item.tags || []).join(", "))}" />
        </div>
      </div>
      <div class="field full">
        <label>可感知描述</label>
        <textarea class="edit-field" data-field="perceivable" rows="2">${escapeHtml(item.perceivable || "")}</textarea>
      </div>
      <div class="form-grid">
        <div class="field">
          <label>开放时段</label>
          <input class="edit-field" data-field="open_hours" value="${escapeHtml(item.open_hours || "全天")}" />
        </div>
        <div class="field">
          <label>邻接（逗号分隔）</label>
          <input class="edit-field" data-field="adjacent" value="${escapeHtml((item.adjacent || []).join(", "))}" />
        </div>
      </div>
      <button class="danger remove-item">删除</button>
    </div>
  `;
}

function renderEditNpcs(data) {
  const entries = Object.entries(data.npcs || {});
  $("#world-tab-npcs").innerHTML = `
    <h3>人物</h3>
    <div class="edit-list" id="edit-npc-list">
      ${entries.map(([id, card]) => npcCard(id, card)).join("")}
    </div>
    <button id="add-npc">添加人物</button>
  `;
}

function npcCard(id, card) {
  const c = card || {};
  return `
    <div class="edit-card">
      <div class="form-grid">
        <div class="field">
          <label>ID</label>
          <input class="edit-field" data-field="id" value="${escapeHtml(id || "")}" />
        </div>
        <div class="field">
          <label>名称</label>
          <input class="edit-field" data-field="name" value="${escapeHtml(c.name || "")}" />
        </div>
      </div>
      <div class="field full">
        <label>外貌</label>
        <textarea class="edit-field" data-field="appearance" rows="2">${escapeHtml(c.appearance || "")}</textarea>
      </div>
      <div class="field full">
        <label>人格</label>
        <textarea class="edit-field" data-field="persona" rows="3">${escapeHtml(c.persona || "")}</textarea>
      </div>
      <div class="field full">
        <label>幕后注（作者底牌：无人知道的真相，仅编剧可读，永不进任何切片）</label>
        <textarea class="edit-field" data-field="private_note" rows="2">${escapeHtml(c.private_note || "")}</textarea>
      </div>
      <div class="field full">
        <label>自知隐秘（该角色自己知道的秘密，进他自己的 Actor 切片，如"他爸欠了赌债，最不愿提"）</label>
        <textarea class="edit-field" data-field="personal_secrets" rows="2">${escapeHtml(c.personal_secrets || "")}</textarea>
      </div>
      <div class="form-grid">
        <div class="field">
          <label><input class="edit-field" data-field="has_actor" type="checkbox" ${c.has_actor ? "checked" : ""} /> 使用 Actor</label>
        </div>
      </div>
      <button class="danger remove-item">删除</button>
    </div>
  `;
}

function renderEditAxes(data) {
  const items = data.axes || [];
  $("#world-tab-axes").innerHTML = `
    <h3>数值属性</h3>
    <div class="edit-list" id="edit-axis-list">
      ${items.map((item, i) => axisCard(item, i)).join("")}
    </div>
    <button id="add-axis">添加数值属性</button>
  `;
}

function axisCard(item, i) {
  const range = item.range || [-100, 100];
  return `
    <div class="edit-card" data-index="${i}">
      <div class="form-grid">
        <div class="field">
          <label>ID</label>
          <input class="edit-field" data-field="id" value="${escapeHtml(item.id || "")}" />
        </div>
        <div class="field">
          <label>名称</label>
          <input class="edit-field" data-field="label" value="${escapeHtml(item.label || "")}" />
        </div>
      </div>
      <div class="form-grid">
        <div class="field">
          <label>标签（逗号分隔）</label>
          <input class="edit-field" data-field="tags" value="${escapeHtml((item.tags || []).join(", "))}" />
        </div>
        <div class="field">
          <label>目标实体</label>
          <input class="edit-field" data-field="target" value="${escapeHtml(item.target || "")}" />
        </div>
      </div>
      <div class="form-grid three">
        <div class="field">
          <label>最小值</label>
          <input class="edit-field" data-field="min" type="number" value="${range[0] ?? -100}" />
        </div>
        <div class="field">
          <label>最大值</label>
          <input class="edit-field" data-field="max" type="number" value="${range[1] ?? 100}" />
        </div>
        <div class="field">
          <label>初始值</label>
          <input class="edit-field" data-field="init" type="number" value="${item.init ?? 0}" />
        </div>
      </div>
      <div class="form-grid">
        <div class="field">
          <label><input class="edit-field" data-field="visible" type="checkbox" ${item.visible ? "checked" : ""} /> 可见</label>
        </div>
        <div class="field">
          <label><input class="edit-field" data-field="track_cause" type="checkbox" ${item.track_cause ? "checked" : ""} /> 记录变化原因</label>
        </div>
      </div>
      <button class="danger remove-item">删除</button>
    </div>
  `;
}

function bindEditEvents() {
  document.querySelectorAll(".edit-list").forEach((list) => {
    list.onclick = (e) => {
      const btn = e.target.closest(".remove-item");
      if (btn) btn.closest(".edit-card").remove();
    };
  });

  const addLore = $("#add-lore");
  if (addLore) addLore.onclick = () => $("#edit-lore-list").insertAdjacentHTML("beforeend", loreCard({}, 999));
  const addScene = $("#add-scene");
  if (addScene) addScene.onclick = () => $("#edit-scene-list").insertAdjacentHTML("beforeend", sceneCard({}, 999));
  const addNpc = $("#add-npc");
  if (addNpc) addNpc.onclick = () => $("#edit-npc-list").insertAdjacentHTML("beforeend", npcCard("", {}));
  const addAxis = $("#add-axis");
  if (addAxis) addAxis.onclick = () => $("#edit-axis-list").insertAdjacentHTML("beforeend", axisCard({}, 999));
}

function splitList(str) {
  return String(str || "").split(/[,，\n]/).map((s) => s.trim()).filter(Boolean);
}

function splitLines(str) {
  return String(str || "").split("\n").map((s) => s.trim()).filter(Boolean);
}

function readOverview() {
  const tab = $("#world-tab-overview");
  const val = (field) => tab.querySelector(`[data-field="${field}"]`)?.value ?? "";
  return {
    id: val("id"),
    name: val("name"),
    opening: val("opening"),
    summary: splitLines(val("summary")),
    default_durations: {
      move_per_edge_min: Number(val("move")) || 10,
      action_default_min: Number(val("action")) || 30,
    },
  };
}

function readLore() {
  return Array.from(document.querySelectorAll("#edit-lore-list .edit-card")).map((card) => ({
    id: card.querySelector('[data-field="id"]')?.value ?? "",
    tags: splitList(card.querySelector('[data-field="tags"]')?.value),
    summary: card.querySelector('[data-field="summary"]')?.value ?? "",
    body: card.querySelector('[data-field="body"]')?.value ?? "",
  }));
}

function readScenes() {
  return Array.from(document.querySelectorAll("#edit-scene-list .edit-card")).map((card) => ({
    id: card.querySelector('[data-field="id"]')?.value ?? "",
    name: card.querySelector('[data-field="name"]')?.value ?? "",
    aliases: splitList(card.querySelector('[data-field="aliases"]')?.value),
    tags: splitList(card.querySelector('[data-field="tags"]')?.value),
    perceivable: card.querySelector('[data-field="perceivable"]')?.value ?? "",
    open_hours: card.querySelector('[data-field="open_hours"]')?.value ?? "全天",
    adjacent: splitList(card.querySelector('[data-field="adjacent"]')?.value),
  }));
}

function readNpcs() {
  const result = {};
  document.querySelectorAll("#edit-npc-list .edit-card").forEach((card) => {
    const id = card.querySelector('[data-field="id"]')?.value ?? "";
    if (!id) return;
    result[id] = {
      id,
      name: card.querySelector('[data-field="name"]')?.value ?? "",
      appearance: card.querySelector('[data-field="appearance"]')?.value ?? "",
      persona: card.querySelector('[data-field="persona"]')?.value ?? "",
      private_note: card.querySelector('[data-field="private_note"]')?.value ?? "",
      personal_secrets: card.querySelector('[data-field="personal_secrets"]')?.value ?? "",
      has_actor: !!card.querySelector('[data-field="has_actor"]')?.checked,
    };
  });
  return result;
}

function readAxes() {
  return Array.from(document.querySelectorAll("#edit-axis-list .edit-card")).map((card) => ({
    id: card.querySelector('[data-field="id"]')?.value ?? "",
    label: card.querySelector('[data-field="label"]')?.value ?? "",
    tags: splitList(card.querySelector('[data-field="tags"]')?.value),
    target: card.querySelector('[data-field="target"]')?.value || null,
    range: [Number(card.querySelector('[data-field="min"]')?.value) || 0, Number(card.querySelector('[data-field="max"]')?.value) || 0],
    init: Number(card.querySelector('[data-field="init"]')?.value) || 0,
    visible: !!card.querySelector('[data-field="visible"]')?.checked,
    track_cause: !!card.querySelector('[data-field="track_cause"]')?.checked,
  }));
}

function collectWorldEditData() {
  return {
    overview: readOverview(),
    lorebook: readLore(),
    scenes: readScenes(),
    npcs: readNpcs(),
    axes: readAxes(),
  };
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function saveWorldEdit() {
  let data;
  try {
    data = collectWorldEditData();
  } catch (e) {
    return;
  }
  try {
    await api(`/api/sessions/${state.sid}/world`, {
      method: "PUT",
      body: JSON.stringify(data),
    });
    alert("已保存到当前存档的世界实例");
    setEditModeUI(false);
    await loadWorldBrowser();
  } catch (e) {
    alert(`保存失败：${e.message}`);
  }
}

async function saveAsWorld() {
  const newId = prompt("请输入新世界 ID（英文/数字/下划线）");
  if (!newId || !newId.trim()) return;
  let data;
  try {
    data = collectWorldEditData();
  } catch (e) {
    return;
  }
  try {
    const res = await api(`/api/sessions/${state.sid}/world/save-as`, {
      method: "POST",
      body: JSON.stringify({ new_world_id: newId.trim(), ...data }),
    });
    alert(`已把当前世界（含演化）沉淀为新世界资产包：${res.world_id}`);
    setEditModeUI(false);
    await loadWorldList();
    switchWorldTab("list");
  } catch (e) {
    alert(`另存失败：${e.message}`);
  }
}

async function refreshWorldModal() {
  if (state.editMode) {
    if (!confirm("刷新会丢失未保存的编辑，确定吗？")) return;
    setEditModeUI(false);
  }
  const activeTab = document.querySelector(".modal-tabs .tab.active")?.dataset.tab || "list";
  if (activeTab === "list") {
    await loadWorldList();
  } else {
    await loadWorldBrowser();
  }
}

async function exportWorld() {
  const url = `/api/sessions/${state.sid}/world/export`;
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function importWorld(file) {
  if (!file) return;
  const content = await readFileAsBase64(file);
  const res = await fetch(`/api/sessions/${state.sid}/world/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, content }),
  });
  if (!res.ok) {
    const text = await res.text();
    alert(`导入失败：${text}`);
    return;
  }
  const data = await res.json();
  alert(`已导入世界：${data.world_id}`);
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      const base64 = String(result).split(",")[1] || "";
      resolve(base64);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function resetWorld() {
  if (!confirm("确定重置当前世界存档？事件、候选、设置都会被清空。")) return;
  await api(`/api/sessions/${state.sid}/reset`, { method: "POST" });
  clearCandidateControls();
  await refreshState();
  await syncPendingFromServer();
  alert("世界已重置");
}

/* ---------- 初始化 ---------- */

async function init() {
  try {
    await ensureSession();
    const info = await api(`/api/sessions/${state.sid}`);
    state.worldName = info.world;
    $("#world-name").textContent = info.world;
    await loadEventHistory();
    await refreshState();
    await syncPendingFromServer();
    await loadDirectorHistory();
    await loadSettings();
  } catch (e) {
    addMessage("npc", `初始化失败：${e.message}`);
  }

  $("#input-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#input").value.trim();
    if (!input) return;
    addMessage("player", input);
    $("#input").value = "";
    await sendInput(input);
  });

  document.querySelectorAll(".topbar-actions button[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => openDrawer(btn.dataset.tab));
  });
  $("#open-world").addEventListener("click", openWorldModal);
  $("#close-drawer").addEventListener("click", closeDrawer);

  document.querySelectorAll(".drawer-tabs .tab").forEach((btn) => {
    btn.addEventListener("click", () => switchDrawerTab(btn.dataset.tab));
  });

  $("#director-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await sendDirectorMessage();
  });
  $("#save-preset").addEventListener("click", savePreset);
  $("#save-player").addEventListener("click", savePlayer);
  $("#save-settings").addEventListener("click", saveSettings);
  $("#refresh-usage").addEventListener("click", loadUsage);
  $("#refresh-debug").addEventListener("click", loadDebugTrace);

  $("#close-world").addEventListener("click", closeWorldModal);
  document.querySelectorAll(".modal-tabs .tab").forEach((btn) => {
    btn.addEventListener("click", () => switchWorldTab(btn.dataset.tab));
  });
  $("#export-world").addEventListener("click", exportWorld);
  $("#refresh-world").addEventListener("click", refreshWorldModal);
  $("#toggle-edit").addEventListener("click", toggleEditMode);
  $("#save-world-edit").addEventListener("click", saveWorldEdit);
  $("#save-as-world").addEventListener("click", saveAsWorld);
  $("#import-world").addEventListener("change", (e) => importWorld(e.target.files[0]));
  $("#reset-world").addEventListener("click", resetWorld);
}

init();