const state = {
  sid: null,
  worldId: null,
  saveName: "main",
  worldName: null,
  candidates: [],
  currentCandidateId: null,
  currentTurnId: null,
  currentMessageEl: null,
  worldData: null,
  // 工作台：就地编辑（无编辑模式开关）。worldBaseline = 上次保存/载入时的表单快照，
  // worldDirty 靠重算对比得出，改了什么一目了然、关窗前还能拦一道。
  worldBaseline: null,
  worldDirty: false,
  worldProblems: [],
  worldTab: "overview",
  worldPanelOpen: false,
  worldDraft: null,
  goals: [],
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
    loadDebugTrace();  // 重抽过程也进调试面板
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
  if (info.has_save) {
    const data = await api(`/api/sessions/open`, {
      method: "POST",
      body: JSON.stringify({ world_id: state.worldId }),
    });
    state.sid = data.sid;
  } else {
    const data = await api(`/api/sessions`, {
      method: "POST",
      body: JSON.stringify({ world_id: state.worldId }),
    });
    state.sid = data.sid;
  }
}

// 时钟为什么跳了这么久：把 save.last_settlement 翻成人话，挂在顶栏时钟上
// （hover 可见）。纯解释性展示，字段缺失就静默不挂 title。
function settlementTooltip(s) {
  if (!s || !s.clock_before) return "";
  const stamp = (v) => String(v || "-").slice(0, 16).replace("T", " ");
  const sourceLabel =
    {
      clock_to: "正文明确到点 → 审计对钟",
      clock_to_rule: "玩家明确跳到某时刻 → 规则对钟",
      audit: "审计估时",
      rule: "规则兜底（审计没给时长）",
      none: "未推进",
    }[s.source] || s.source;
  const lines = [`时间结算：${stamp(s.clock_before)} → ${stamp(s.clock_after)}`, `依据：${sourceLabel}`];
  const bits = [];
  if (s.settle_rule) bits.push(`规则意图 ${stamp(s.settle_rule)}${s.source === "clock_to_rule" ? "" : "（未采用）"}`);
  if (s.delta_rule) bits.push(`规则 ${s.delta_rule} 分${s.source === "rule" ? "" : "（未采用）"}`);
  if (s.delta_audit_raw != null) {
    bits.push(
      s.audit_capped
        ? `审计估 ${s.delta_audit_raw} 分（保险丝截到 ${s.delta_audit}）`
        : `审计 ${s.delta_audit} 分`
    );
  }
  if (s.delta_applied) bits.push(`实际推进 ${s.delta_applied} 分`);
  if (bits.length) lines.push(bits.join(" / "));
  if (s.clock_to_audit && s.source === "clock_to") lines.push(`对钟目标：${stamp(s.clock_to_audit)}`);
  if (s.clock_to_rejected) lines.push(`已拒绝审计对钟：${s.clock_to_rejected}`);
  if (s.settle_rule_rejected) lines.push(`已拒绝规则对钟：${s.settle_rule_rejected}`);
  if (s.audit_negative) lines.push("审计给了负数时长，已按 0 处理");
  if (s.error) lines.push(`⚠ ${s.error}`);
  if (s.audit_error) lines.push(`⚠ 本次审计失败：${s.audit_error}`);
  return lines.join("\n");
}

async function refreshState() {
  if (!state.sid) return;
  const data = await api(`/api/sessions/${state.sid}/state`);
  $("#world-name").textContent = state.worldName || "-";
  $("#clock").textContent = data.clock || "-";
  const tip = settlementTooltip(data.last_settlement);
  if (tip) $("#clock").title = tip;
  else $("#clock").removeAttribute("title");
  $("#scene").textContent = data.scene_id || data.scene || "-";
  if (data.preset) {
    const p = data.preset;
    $("#writer-guidelines-input").value = p.writer_guidelines || "";
    $("#style-sample-input").value = p.style_sample || "";
    $("#banned-words-input").value = (p.banned_words || []).join(", ");
  }
  renderLeftRail(data);
  renderStateChange(data);
  renderStatePanel(data);
  renderStateBadge(data);
}

function renderLeftRail(data) {
  const nav = $("#scene-nav");
  nav.innerHTML = "";
  const current = document.createElement("div");
  current.className = "rail-item current";
  current.textContent = `📍 ${data.scene_id || "-"}`;
  nav.appendChild(current);

  // 去过的最近 3 个场景（id 即中文名），不筛选邻接
  for (const recent of data.recent_scenes || []) {
    if (recent === data.scene_id) continue;
    const item = document.createElement("div");
    item.className = "rail-item";
    item.textContent = recent;
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

  const goals = $("#state-goals");
  goals.innerHTML = "";
  const goalList = data.goals || [];
  state.goals = goalList;
  if (!goalList.length) {
    goals.innerHTML = '<li class="muted">暂无（找导演设立）</li>';
  } else {
    // 两级树（2026-09-12）：大目标 = 章节（带 x/y 进度），子目标缩进；孤儿支线单列。
    const owner = (g) => (g.subject_name && g.subject_name !== "玩家" ? `·${g.subject_name}` : "");
    const bigs = goalList.filter((g) => g.kind === "big");
    const loose = goalList.filter(
      (g) => g.kind !== "big" && !bigs.some((b) => b.id === g.big_goal_id)
    );
    for (const b of bigs) {
      const kids = goalList.filter((g) => g.big_goal_id === b.id);
      const done = kids.filter((k) => k.status === "done").length;
      const li = document.createElement("li");
      li.className = "goal-big";
      li.textContent = `【主线${owner(b)}】${b.text}${kids.length ? `　(${done}/${kids.length})` : ""}`;
      goals.appendChild(li);
      for (const k of kids) {
        const sub = document.createElement("li");
        sub.className = k.status === "done" ? "goal-sub done" : "goal-sub";
        sub.textContent = `└【支线${owner(k)}】${k.text}`;
        goals.appendChild(sub);
      }
    }
    if (loose.length) {
      const head = document.createElement("li");
      head.className = "goal-loose";
      head.textContent = "未挂靠支线";
      goals.appendChild(head);
      for (const g of loose) {
        const li = document.createElement("li");
        li.className = "goal-sub";
        li.textContent = `【支线${owner(g)}】${g.text}`;
        goals.appendChild(li);
      }
    }
  }
}

/* ---------- 角色状态 · 长期事实（Step 2b，2026-09-14） ---------- */

function stampClock(v) {
  return String(v || "-").slice(0, 16).replace("T", " ");
}

function stTag(text) {
  const s = document.createElement("span");
  s.className = "st-tag";
  s.textContent = text;
  return s;
}

function stRow(name, text, right, cls) {
  const row = document.createElement("div");
  row.className = "st-row" + (cls ? ` ${cls}` : "");
  if (name) {
    const who = document.createElement("span");
    who.className = "st-who";
    who.textContent = name;
    row.appendChild(who);
  }
  const body = document.createElement("span");
  body.className = "st-text";
  body.textContent = text;
  row.appendChild(body);
  if (right) row.appendChild(right);
  return row;
}

// 撤销控件：点 × → 原地变成「确认 / 取消」（不弹窗）。撤销是破坏性动作，但它留痕、
// 且状态还能重新获得，一道轻确认就够；反过来若点完行直接消失，玩家会怀疑自己是不是
// 点错了、撤掉的到底是哪条。
function stRevoke(stateId) {
  const box = document.createElement("span");
  box.className = "st-act";
  const x = document.createElement("span");
  x.className = "st-x";
  x.textContent = "×";
  x.title = "撤销这条状态（编剧此后不再认为他有；正文与事件日志不动）";
  x.onclick = () => {
    const ask = document.createElement("span");
    ask.className = "st-confirm";
    ask.textContent = "撤销？";
    const yes = document.createElement("button");
    yes.className = "danger";
    yes.textContent = "确认";
    yes.onclick = async () => {
      yes.disabled = true;
      try {
        await api(`/api/sessions/${state.sid}/states/${stateId}/revoke`, {
          method: "POST",
        });
        await refreshState();
      } catch (e) {
        ask.textContent = `撤销失败：${e.message}`;
      }
    };
    const no = document.createElement("button");
    no.textContent = "取消";
    no.onclick = () => ask.replaceWith(x);
    ask.appendChild(yes);
    ask.appendChild(no);
    x.replaceWith(ask);
  };
  box.appendChild(x);
  return box;
}

// 本回合变化（左侧栏）：只列**最近一次采纳**带来的增删。新增的给撤销入口；
// 已结束 / 已到期的条目已经不在存档里了，灰字标注即可（撤不掉也不需要撤）；
// skipped 是内部诊断（审计报歪了 / 找不到 id），不露给玩家。
function stateChangeRows(change) {
  const revoked = new Set((change.revoked || []).map((r) => r.id));
  const rows = [];
  for (const it of change.added || []) {
    rows.push({ ...it, kind: "added", revoked: revoked.has(it.id) });
  }
  for (const it of change.removed || []) rows.push({ ...it, kind: "removed" });
  for (const it of change.expired || []) rows.push({ ...it, kind: "expired" });
  return rows;
}

function renderStateChange(data) {
  const box = $("#state-change");
  const change = data.last_state_change || {};
  const rows = stateChangeRows(change);
  box.innerHTML = "";
  if (!rows.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const head = document.createElement("h2");
  head.textContent = "本回合变化";
  box.appendChild(head);
  const at = document.createElement("div");
  at.className = "st-when";
  at.textContent = `${stampClock(change.at)} 采纳后`;
  box.appendChild(at);
  for (const row of rows) {
    if (row.kind === "added" && !row.revoked) {
      box.appendChild(stRow(row.npc_id, row.text, stRevoke(row.id)));
      continue;
    }
    // 已撤销的留在原地变灰（不是消失）：玩家刚点完，行立刻没了会让人怀疑点错了。
    const tag = row.revoked ? "已撤销" : row.kind === "expired" ? "已到期结束" : "已结束";
    box.appendChild(stRow(row.npc_id, row.text, stTag(tag), "grey"));
  }
}

// 状态徽章（顶栏）：**编剧这一轮实际读到**的条数——只算主角与在场者、且只算
// 注入上限内的（不在场者的状态、以及超出上限被截断的，编剧都看不到，计进去就
// 是在骗玩家）。点开抽屉看全量与明细。
function renderStateBadge(data) {
  const view = data.state_view || [];
  const n = view.reduce(
    (sum, entry) =>
      entry.is_player || entry.present ? sum + (entry.visible || []).length : sum,
    0
  );
  const badge = $("#state-badge");
  if (badge) badge.textContent = `状态 ${n}`;
}

// 状态面板（抽屉「状态」页）：**当前全量**。数据由引擎侧生成（`/state` 的
// state_view，复用的就是注入提示词那套上限与截断），所以面板上"编剧能看到哪几条"
// 与提示词字面同源——前端不自己算 cap，只负责分组显示、灰显与撤销入口。
function renderStatePanel(data) {
  const box = $("#states-panel");
  const view = data.state_view || [];
  box.innerHTML = "";
  if (!view.length) {
    box.innerHTML = '<div class="empty">还没有任何状态。</div>';
    return;
  }
  for (const entry of view) {
    const group = document.createElement("div");
    group.className = "st-group";
    const head = document.createElement("div");
    head.className = "st-group-head";
    head.textContent = entry.is_player ? `${entry.name}（你）` : entry.name;
    if (entry.retired) head.appendChild(stTag("已退场"));
    else if (!entry.present) head.appendChild(stTag("不在场 · 编剧看不到"));
    group.appendChild(head);

    // 灰显只有一种含义：**编剧这一轮读不到这条**。四个来源都归到它——超出注入
    // 上限、不在场、已退场、已到期。视觉语言统一了，玩家不用去猜哪种灰是哪种意思。
    const wholeGroupGrey = entry.retired || !(entry.is_player || entry.present);
    const items = [
      ...(entry.visible || []).map((it) => ({ it, grey: wholeGroupGrey, tag: "" })),
      ...(entry.hidden || []).map((it) => ({ it, grey: true, tag: "" })),
      // 已到期（backend 的 expired_at 非空）：条目被标记失效、编剧读不到，但**条目
      // 还在存档里、还带撤销入口**——旧语义是到点直接删掉，玩家连撤的对象都没有。
      ...(entry.expired || []).map((it) => ({ it, grey: true, tag: "已到期" })),
    ];
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "st-empty";
      empty.textContent = "此刻没有任何状态";
      group.appendChild(empty);
    }
    for (const { it, grey, tag } of items) {
      const text = it.until ? `${it.text}（至 ${String(it.until).slice(0, 10)}）` : it.text;
      const right = document.createElement("span");
      right.className = "st-act";
      // public=false = 这条状态外人看不出来（"其实色盲"没人知道，"左腿瘸了"人尽皆知）
      // ——面板上标出来，省得玩家疑惑 NPC 为什么没反应。
      // 注意：状态只写**现状**，成因（"为什么"）在它来源的那条事件里，不在状态上。
      if (tag) right.appendChild(stTag(tag));
      if (!it.public) right.appendChild(stTag("外人看不出"));
      right.appendChild(stRevoke(it.id));
      group.appendChild(stRow("", text, right, grey ? "grey" : ""));
    }
    if ((entry.hidden || []).length) {
      const note = document.createElement("div");
      note.className = "st-note";
      note.textContent = `以上 ${entry.hidden.length} 条灰显的超出注入上限，编剧看不到；撤掉其中一条，后面的立刻补上。`;
      group.appendChild(note);
    }
    box.appendChild(group);
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
    updateUsageStatus(payload);
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
  } else if (tabName === "settings") {
    loadSettings();
  }
}

async function loadDebugTrace() {
  if (!state.sid) return;
  const data = await api(`/api/sessions/${state.sid}/debug/latest`);
  const trace = data.trace || [];
  const box = $("#debug-output");
  if (!trace.length) {
    box.textContent = "暂无调试数据";
    return;
  }
  box.innerHTML = trace
    .map((entry, i) => debugEntryCard(entry, trace.length - i))
    .join("");
}

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

const SLOW_CALL_MS = 30000; // 单笔 LLM 调用超过 30s 打「慢」标签

function durationLabel(ms) {
  if (!Number.isFinite(ms) || ms < 0) return "";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

function debugEntryCard(entry, seq) {
  const label = entry.label || "未标注调用";
  const ms = Number(entry.duration_ms);
  const durText = durationLabel(ms);
  const meta = [entry.type, entry.model, `temp ${entry.temperature}`, durText ? `耗时 ${durText}` : ""]
    .filter(Boolean)
    .map(esc)
    .join(" · ");
  const slowBadge = ms >= SLOW_CALL_MS ? `<span class="dbg-slow-badge">慢</span>` : "";
  const errBadge = entry.error ? `<span class="dbg-err-badge">出错</span>` : "";
  const head = `<span class="dbg-seq">#${seq}</span><span class="dbg-label">${esc(label)}</span><span class="dbg-meta">${meta}</span>${slowBadge}${errBadge}`;

  const msgs = (entry.messages || [])
    .map((m) => {
      const role = esc(m.role || "?");
      return `<div class="dbg-msg"><span class="dbg-role dbg-role-${esc(m.role)}">${role}</span><div class="dbg-content">${esc(m.content)}</div></div>`;
    })
    .join("");

  let outHtml = "";
  if (entry.output !== undefined) {
    const out = entry.output;
    if (out && typeof out === "object" && out.prose !== undefined) {
      const questions = Array.isArray(out.actor_questions) && out.actor_questions.length
        ? `<div class="dbg-sub">深抉择上缴</div><div class="dbg-content">${esc(JSON.stringify(out.actor_questions, null, 2))}</div>`
        : "";
      outHtml = `<div class="dbg-sub">输出</div>
        <div class="dbg-sub">summary</div><div class="dbg-content">${esc(out.summary || "（无）")}</div>
        <div class="dbg-sub">prose（正文）</div><div class="dbg-content">${esc(out.prose)}</div>${questions}`;
    } else {
      outHtml = `<div class="dbg-sub">输出</div><div class="dbg-content dbg-json">${esc(JSON.stringify(out, null, 2))}</div>`;
    }
  }
  const errHtml = entry.error
    ? `<div class="dbg-sub dbg-err">错误</div><div class="dbg-content dbg-err">${esc(entry.error)}</div>`
    : "";

  return `<details class="dbg-card"><summary>${head}</summary>${msgs}${outHtml}${errHtml}</details>`;
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

function updateUsageStatus(data) {
  const el = $("#usage-status");
  if (!el) return;
  const u = data.usage || {};
  const c = data.cache || {};
  const total = (c.hits || 0) + (c.misses || 0);
  const rate = total ? Math.round(((c.hits || 0) / total) * 100) : 0;
  const effLabel = { low: "低", high: "高", max: "最高" }[data.reasoning_effort] || "自动";
  el.textContent =
    `本轮：输入 ${u.prompt_tokens || 0} · 输出 ${u.completion_tokens || 0} token` +
    ` · ${u.calls || 0} 次调用 · 缓存命中 ${rate}%（${c.hits || 0}/${total}）` +
    ` · ${data.model || "-"} · 推理：${effLabel}`;
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
  // 设立小目标时允许当场挂到一个大目标下（2026-09-12）——否则玩家没有
  // 任何 UI 入口建立父子关系，big_goal_id 只能靠手打 id。
  const p = action.payload || {};
  let parentSelect = null;
  if (action.type === "set_goal" && p.status !== "abandoned" && (p.kind || "small") !== "big") {
    const bigs = (state.goals || []).filter((g) => g.kind === "big");
    if (bigs.length) {
      const row = document.createElement("div");
      row.className = "goal-parent-row";
      const lab = document.createElement("label");
      lab.textContent = "挂到主线：";
      parentSelect = document.createElement("select");
      const none = document.createElement("option");
      none.value = "";
      none.textContent = "（不挂靠）";
      parentSelect.appendChild(none);
      for (const b of bigs) {
        const opt = document.createElement("option");
        opt.value = b.id;
        opt.textContent = b.text;
        if (p.big_goal_id === b.id) opt.selected = true;
        parentSelect.appendChild(opt);
      }
      parentSelect.onchange = () => {
        p.big_goal_id = parentSelect.value || null;
        label.textContent = `待确认操作：${describeAction(action)}`;
      };
      row.appendChild(lab);
      row.appendChild(parentSelect);
      box.appendChild(row);
    }
  }
  const actions = document.createElement("div");
  actions.className = "pending-action-buttons";
  const confirm = document.createElement("button");
  confirm.textContent = "确认执行";
  confirm.className = "danger";
  confirm.onclick = async () => {
    box.remove();
    if (parentSelect) p.big_goal_id = parentSelect.value || null;
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
    case "inject_memory":
      return `记忆注入：给 ${p.npc_id || "?"} 注入记忆`;
    case "access_rejudge":
      return `事件改判：${p.event_id || "?"} → ${p.known_by ? "私密" : "公开"}`;
    case "retire":
      return `角色退场：${p.npc_id || "?"} 永久离开舞台（不可逆）`;
    case "set_goal": {
      if (p.status === "abandoned") return `废弃剧情目标：${p.goal_id || "?"}`;
      const kind = p.kind === "big" ? "大目标/主线" : "小目标/支线";
      let parent = "";
      if (p.big_goal_id) {
        const b = (state.goals || []).find((g) => g.id === p.big_goal_id);
        parent = `，挂到「${b ? b.text : p.big_goal_id}」`;
      }
      const owner = p.subject ? `（归属：${p.subject}）` : "";
      return `设立${kind}${owner}：「${p.text || "?"}」${parent}`;
    }
    case "state_add": {
      const until = p.until ? `（至 ${String(p.until).slice(0, 10)}）` : "";
      const open = p.public ? "外在可见" : "外在看不出";
      return `状态新增：给 ${p.npc_id || "?"} 加「${p.text || "?"}」${until}（${open}）`;
    }
    case "state_revoke": {
      const who = p.npc_id ? `${p.npc_id} 的` : "";
      const which = p.text || p.state_id || "?";
      return `状态撤销：撤掉${who}「${which}」（正文与事件日志不动）`;
    }
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
      style_sample: $("#style-sample-input").value,
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
  $("#setting-reasoning").value = data.reasoning_effort || "auto";
  $("#setting-qc-enabled").checked = data.qc_enabled !== false;

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
      reasoning_effort: $("#setting-reasoning").value,
      qc_enabled: $("#setting-qc-enabled").checked,
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

/* ---------- 世界工作台 ---------- */

function openWorldModal() {
  $("#world-modal").classList.add("open");
  closeWorldPanel();
  // 已载入过就原样展示（未保存改动保留）；否则才拉取。
  if (!state.worldData) {
    loadWorldBrowser();
  } else {
    updateWorldContext();
    updateSaveBar();
  }
}

function closeWorldModal() {
  if (state.worldDirty && !confirm("有未保存的改动，关闭后会丢失。确定关闭？")) return;
  closeWorldPanel();
  $("#world-modal").classList.remove("open");
}

function toggleWorldPanel() {
  if (state.worldPanelOpen) {
    closeWorldPanel();
    return;
  }
  state.worldPanelOpen = true;
  $("#wb-world-panel").classList.add("open");
  loadWorldList();
}

function closeWorldPanel() {
  state.worldPanelOpen = false;
  $("#wb-world-panel").classList.remove("open");
}

function switchWorldTab(tabName) {
  // 各标签页的 DOM 常驻（只是 display 切换），切页不丢未保存改动。
  state.worldTab = tabName;
  document.querySelectorAll(".modal-tabs .tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabName);
  });
  document.querySelectorAll(".world-tab").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `world-tab-${tabName}`);
  });
  if (!state.worldData) loadWorldBrowser();
  // 世界书↔人物 之间有一处交叉引用（归属条目），切页时对齐一次：
  // 人物名可能刚改（下拉选项旧了），归属可能刚改（人物卡提示旧了）。
  if (tabName === "lore") syncLoreSubjectOptions();
  if (tabName === "npcs") refreshNpcLoreHints();
  updateSaveBar();
}

/* ---------- 新建世界（L1 种子 / L2 一句话草稿） ---------- */

function renderNewWorldForm() {
  return `
    <div class="new-world">
      <div class="nw-row">
        <label>世界 ID
          <input id="nw-id" placeholder="qingshi2" /></label>
        <label>世界名<input id="nw-name" placeholder="青石镇" /></label>
      </div>
      <div class="nw-row">
        <label>主角名
          <input id="nw-player" placeholder="主角真名，如 刘星" /></label>
        <label>开局场景<input id="nw-scene" placeholder="留空则用「起点」" /></label>
      </div>
      <label>世界生成提示
        <textarea id="nw-premise" rows="3" placeholder="例：九十年代县城高中暑假，我和同桌朱明、王蓉在小镇上晃荡。"></textarea></label>
      <label>开场白
        <textarea id="nw-opening" rows="2"></textarea></label>
      <div class="nw-actions">
        <button id="nw-draft" class="primary">AI 起草</button>
        <button id="nw-create" class="primary">创建空白世界</button>
      </div>
      <div id="nw-preview"></div>
    </div>
  `;
}

function readNewWorldForm() {
  const val = (id) => ($(id) ? $(id).value.trim() : "");
  return {
    world_id: val("#nw-id"),
    name: val("#nw-name"),
    player_name: val("#nw-player"),
    start_scene: val("#nw-scene"),
    opening: val("#nw-opening"),
    premise: val("#nw-premise"),
  };
}

function bindNewWorldEvents() {
  const draftBtn = $("#nw-draft");
  if (draftBtn) draftBtn.onclick = draftNewWorld;
  const createBtn = $("#nw-create");
  if (createBtn) createBtn.onclick = createBlankWorld;
}

async function draftNewWorld() {
  const form = readNewWorldForm();
  if (!form.premise) {
    alert("先写世界生成提示。");
    return;
  }
  if (!form.world_id) {
    alert("先填世界 ID（英文/数字/下划线）。");
    return;
  }
  const btn = $("#nw-draft");
  btn.disabled = true;
  btn.textContent = "起草中…";
  try {
    const res = await api(`/api/worlds/draft`, {
      method: "POST",
      body: JSON.stringify(form),
    });
    state.worldDraft = res;
    renderDraftPreview(res);
  } catch (e) {
    alert(`起草失败：${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "AI 起草";
  }
}

function renderDraftPreview(res) {
  const box = $("#nw-preview");
  const assets = res.assets || {};
  const ov = assets.overview || {};
  const scenes = assets.scenes || [];
  const lore = assets.lorebook || [];
  const cards = Object.values(assets.npcs || {});
  const players = cards.filter((n) => n.is_player);
  const others = cards.filter((n) => !n.is_player);
  const problems = res.problems || [];
  const nameOf = (n) => escapeHtml(n.id) + (n.has_actor ? " ·Actor" : "");
  box.innerHTML = `
    <div class="draft-preview">
      <div class="draft-head">
        <strong>草稿预览</strong>
        <span class="muted">${escapeHtml(ov.name || "")} · 主角 ${players.map((p) => escapeHtml(p.id)).join("、") || "（缺）"} · 开局场景 ${escapeHtml(ov.start_scene || "")}</span>
      </div>
      ${problems.length
        ? `<div class="warn-text">待修：${escapeHtml(problems.join("；"))}</div>`
        : '<div class="ok-text">已通过引擎校验，可以落盘。</div>'}
      <div class="draft-block"><b>概要</b><pre>${escapeHtml((ov.summary || []).join("\n"))}</pre></div>
      <div class="draft-block"><b>开场白</b><pre>${escapeHtml(ov.opening || "")}</pre></div>
      <div class="draft-block"><b>场景（${scenes.length}）</b><div>${scenes.map((s) => `<span class="chip">${escapeHtml(s.id)}</span>`).join("")}</div></div>
      <div class="draft-block"><b>人物（${others.length}）</b><div>${others.map((n) => `<span class="chip">${nameOf(n)}</span>`).join("")}</div></div>
      <div class="draft-block"><b>世界书（${lore.length}）</b><div>${lore.map((l) => `<span class="chip">${escapeHtml(l.id)}</span>`).join("")}</div></div>
      <div class="nw-actions">
        <button id="nw-create-draft" class="primary">按草稿创建世界</button>
        <button id="nw-discard">丢弃草稿</button>
      </div>
    </div>
  `;
  $("#nw-create-draft").onclick = createWorldFromDraft;
  $("#nw-discard").onclick = () => {
    state.worldDraft = null;
    box.innerHTML = "";
  };
}

/** 表单里手填的值是硬约束：盖在草稿上（含主角改名——人物表键就是主角名）。 */
function applyFormOverrides(payload, form) {
  payload.overview = payload.overview || {};
  if (form.name) payload.overview.name = form.name;
  if (form.start_scene) payload.overview.start_scene = form.start_scene;
  if (form.opening) payload.overview.opening = form.opening;
  const npcs = payload.npcs || {};
  const player = Object.values(npcs).find((card) => card.is_player);
  if (player && form.player_name && form.player_name !== player.id) {
    delete npcs[player.id];
    player.id = form.player_name;
    npcs[form.player_name] = player;
  }
  return payload;
}

async function createBlankWorld() {
  const form = readNewWorldForm();
  if (!form.world_id) {
    alert("先填世界 ID（英文/数字/下划线）。");
    return;
  }
  if (!form.player_name) {
    alert("先填主角名——事件日志按此名记录。");
    return;
  }
  await postNewWorld(form);
}

async function createWorldFromDraft() {
  if (!state.worldDraft) return;
  const form = readNewWorldForm();
  if (!form.world_id) {
    alert("先填世界 ID。");
    return;
  }
  const payload = JSON.parse(JSON.stringify(state.worldDraft.assets || {}));
  await postNewWorld({
    world_id: form.world_id,
    name: form.name,
    player_name: form.player_name,
    payload: applyFormOverrides(payload, form),
  });
}

async function postNewWorld(body) {
  try {
    const res = await api(`/api/worlds/new`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    const problems = res.problems || [];
    alert(
      `已创建世界：${res.world_id}` +
        (problems.length ? `\n\n提示：\n${problems.join("\n")}` : "")
    );
    state.worldDraft = null;
    await loadWorldList();
    if (confirm(`打开「${res.world_id}」开始玩？`)) {
      await switchToWorld(res.world_id);
      closeWorldModal();
    }
  } catch (e) {
    alert(`创建失败：${e.message}`);
  }
}

async function checkWorldAssets(worldId) {
  try {
    const res = await api(`/api/worlds/${encodeURIComponent(worldId)}/check`);
    if (res.ok) {
      alert(`世界「${worldId}」校验通过。`);
      return;
    }
    alert(`世界「${worldId}」有 ${res.problems.length} 处待修：\n\n${res.problems.join("\n")}`);
  } catch (e) {
    alert(`校验失败：${e.message}`);
  }
}

async function loadWorldList() {
  const data = await api(`/api/worlds`);
  const worlds = data.worlds || [];

  const panel = $("#wb-world-panel");
  panel.innerHTML = `
    <div class="wb-panel-head">
      <strong>世界列表</strong>
      <span class="muted">点「打开」接着玩（当前世界标着「当前」；一个世界就是一份存档）</span>
      <span class="wb-panel-actions">
        <button id="wb-new-world-toggle">新建世界</button>
        <label class="import-label">导入为新世界<input id="import-world" type="file" accept=".zip" hidden /></label>
      </span>
    </div>
    <div id="wb-new-world-body" hidden>${renderNewWorldForm()}</div>
    <div id="world-list-items"></div>
  `;

  $("#wb-new-world-toggle").onclick = () => {
    const body = $("#wb-new-world-body");
    body.hidden = !body.hidden;
    $("#wb-new-world-toggle").textContent = body.hidden ? "新建世界" : "收起表单";
  };
  $("#import-world").onchange = (e) => importWorld(e.target.files[0]);
  bindNewWorldEvents();

  const box = $("#world-list-items");
  if (!worlds.length) {
    box.innerHTML =
      '<div class="empty">暂无世界：展开「新建世界」填一句世界生成提示让 AI 起草，或直接创建一个空白世界。</div>';
  }
  for (const world of worlds) {
    const item = document.createElement("div");
    item.className = "item world-list-item";

    const label = document.createElement("div");
    const warn = world.ok === false ? ' <span class="warn-tag">待修</span>' : "";
    const current = world.id === state.worldId ? ' <span class="player-tag">当前</span>' : "";
    const clock = world.clock
      ? ` <span class="muted">${escapeHtml(String(world.clock).slice(0, 16).replace("T", " "))}</span>`
      : "";
    label.innerHTML = `<strong>${escapeHtml(world.name || world.id)}</strong> <span class="muted">${escapeHtml(world.id)}</span>${clock}${current}${warn}`;
    if (world.ok === false && (world.problems || []).length) {
      const tip = document.createElement("div");
      tip.className = "warn-text";
      tip.textContent = (world.problems || []).join("；");
      label.appendChild(tip);
    }
    item.appendChild(label);

    const actions = document.createElement("div");
    actions.className = "world-list-actions";

    const openBtn = document.createElement("button");
    openBtn.textContent = "打开";
    openBtn.onclick = () => switchToWorld(world.id);
    actions.appendChild(openBtn);

    const checkBtn = document.createElement("button");
    checkBtn.textContent = "校验";
    checkBtn.onclick = () => checkWorldAssets(world.id);
    actions.appendChild(checkBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "danger";
    deleteBtn.textContent = "删除";
    deleteBtn.onclick = () => deleteWorld(world.id);
    actions.appendChild(deleteBtn);

    item.appendChild(actions);
    box.appendChild(item);
  }
}

async function switchToWorld(worldId) {
  if (worldId === state.worldId && state.sid) {
    closeWorldPanel();
    return;
  }
  if (state.worldDirty && !confirm("有未保存的改动，切换世界会丢弃。继续切换？")) return;
  state.worldId = worldId;
  localStorage.setItem("aiworld_world_id", worldId);
  state.worldName = null;
  state.worldData = null;
  closeWorldPanel();
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
  updateWorldContext();
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

/** 标题栏上下文：世界名 · 目录名——说明"我在编辑谁"（世界=存档，没有第二层）。 */
async function updateWorldContext() {
  const el = $("#wb-context");
  if (!el) return;
  try {
    const info = await api(`/api/sessions/${state.sid}`);
    state.worldName = info.world;
    el.textContent = `${info.world} · ${state.worldId}`;
  } catch (e) {
    el.textContent = state.worldId || "";
  }
}

async function loadWorldBrowser() {
  const data = await api(`/api/sessions/${state.sid}/world`);
  state.worldData = data;
  renderWorldTabs(data);
  updateWorldContext();
  await refreshProblems();
}

function renderWorldTabs(data) {
  // 就地编辑：每个「世界资产」标签页只有一种样子（表单）——浏览态/编辑态
  // 两套渲染合并成一套（2026-09-13 工作台改版，消灭「编辑模式」开关）。
  renderEditOverview(data);
  renderEditLore(data);
  renderEditScenes(data);
  renderEditNpcs(data);
  renderEditAxes(data);
  renderEventsTab(data);
  bindEditEvents();
  bindDirtyTracking();
  resetBaseline();
}

function renderEventsTab(data) {
  const events = $("#world-tab-events");
  events.innerHTML =
    '<h3>事件日志（本存档，只读）</h3><p class="hint">公开/私密改判请用导演窗口的「事件访问改判」。</p>';
  const list = data.events || [];
  if (!list.length) {
    events.innerHTML += '<div class="empty">还没有事件。</div>';
  }
  list.forEach((ev, index) => {
    const div = document.createElement("div");
    div.className = "item";
    const inputLine = ev.player_input ? `\n玩家：${ev.player_input}` : "";
    div.textContent = `#${index + 1} ${formatEventSummary(ev, data.scenes || [], data.npcs || {})}${inputLine}`;
    events.appendChild(div);
  });
}

function formatEventSummary(ev, scenes, npcs) {
  const location = ev.location || "";
  // participants/known_by 里记的就是角色名（主角也是人名之一，2026-09-13）——直接显示
  const names = (ev.participants || []).join("、");
  const locTag = location ? ` [${location}]` : "";
  const whoTag = names ? `（在场：${names}）` : "";
  const privTag = ev.known_by
    ? `【私密·仅${(ev.known_by || []).join("、")}】`
    : "【公开】";
  const summary = ev.summary || (ev.body || "").replace(/\s+/g, " ").slice(0, 40);
  return `${ev.at || ""}${locTag} ${summary}${whoTag} ${privTag}`;
}

/* ---------- 脏检查与保存条 ---------- */

const ASSET_TABS = ["overview", "lore", "scenes", "npcs", "axes"];

function snapshotWorld() {
  try {
    return JSON.stringify(collectWorldEditData());
  } catch (e) {
    return "";
  }
}

function resetBaseline() {
  state.worldBaseline = snapshotWorld();
  state.worldDirty = false;
  updateSaveBar();
}

function refreshDirty() {
  state.worldDirty =
    state.worldBaseline !== null && snapshotWorld() !== state.worldBaseline;
  updateSaveBar();
}

function bindDirtyTracking() {
  const modal = $("#world-modal");
  modal.oninput = refreshDirty;
  modal.onchange = refreshDirty;
}

/** 保存条：改动写到哪里、有多少待修，一眼可见（此前这些一个字都没说）。 */
function updateSaveBar() {
  const bar = $("#wb-savebar");
  if (!bar) return;
  const onAssetTab = ASSET_TABS.includes(state.worldTab);
  bar.hidden = !onAssetTab;
  if (!onAssetTab) return;
  const status = $("#wb-status");
  const problems = state.worldProblems || [];
  const bits = [];
  if (state.worldDirty) bits.push("● 有未保存的改动");
  if (problems.length) bits.push(`世界有 ${problems.length} 处待修`);
  if (bits.length) {
    status.textContent = bits.join(" · ");
    status.className = "wb-status warn";
    status.title = problems.length ? problems.join("\n") : "";
  } else {
    status.textContent = "所有改动直接保存到这个世界，即时生效";
    status.className = "wb-status";
    status.title = "";
  }
  $("#wb-save").disabled = !state.worldDirty;
  $("#wb-discard").disabled = !state.worldDirty;
}

/** 载入时与保存后各查一次：待修的是"磁盘上已保存的世界"，不是表单里的半成品。 */
async function refreshProblems() {
  try {
    const res = await api(`/api/sessions/${state.sid}/world/check`);
    state.worldProblems = res.problems || [];
  } catch (e) {
    state.worldProblems = [];
  }
  updateSaveBar();
}

async function saveWorldEdit() {
  let data;
  try {
    data = collectWorldEditData();
  } catch (e) {
    return;
  }
  try {
    const res = await api(`/api/sessions/${state.sid}/world`, {
      method: "PUT",
      body: JSON.stringify(data),
    });
    state.worldProblems = res.problems || [];
    await loadWorldBrowser();
    if (state.worldProblems.length) {
      alert(
        `已保存到本存档，但有 ${state.worldProblems.length} 处待修：\n\n${state.worldProblems.join("\n")}`
      );
    }
  } catch (e) {
    alert(`保存失败：${e.message}`);
  }
}

function discardWorldEdits() {
  if (!state.worldDirty) return;
  if (!confirm("放弃未保存的改动，恢复到已保存的样子？")) return;
  loadWorldBrowser();
}

async function checkSessionWorld() {
  try {
    const res = await api(`/api/sessions/${state.sid}/world/check`);
    state.worldProblems = res.problems || [];
    updateSaveBar();
    if (res.ok) {
      alert("世界校验通过。");
      return;
    }
    alert(
      `世界有 ${res.problems.length} 处待修：\n\n${res.problems.join("\n")}`
    );
  } catch (e) {
    alert(`校验失败：${e.message}`);
  }
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
          <h4>世界钟与记忆</h4>
          <div class="field">
            <label>世界钟起点（ISO 时间，空=引擎默认 2026-07-14T08:00:00；新建存档/重置后回到此时刻）</label>
            <input class="edit-field" data-field="start_time" type="text" placeholder="2026-07-14T08:00:00" value="${escapeHtml(ov.start_time || "")}" />
          </div>
          <div class="field">
            <label>开局场景（场景中文名；留空 = 取场景表第一个。主角自此开场，之后由事件流水推导其位置）</label>
            <input class="edit-field" data-field="start_scene" type="text" placeholder="主街" value="${escapeHtml(ov.start_scene || "")}" />
          </div>
          <div class="field">
            <label>记忆回溯条数上限（experiences 每次回看的最大事件条数）</label>
            <input class="edit-field" data-field="memory_limit" type="number" value="${ov.memory_limit ?? 50}" />
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
  const npcIds = Object.keys(data.npcs || {});
  const counts = {};
  items.forEach((it) => {
    const s = (it.subject || "").trim();
    if (s) counts[s] = (counts[s] || 0) + 1;
  });
  $("#world-tab-lore").innerHTML = `
    <h3>世界书</h3>
    <div class="md-pane">
      <div class="md-side">
        <input class="md-search" placeholder="搜索条目…" />
        <div class="md-items"></div>
        <button id="add-lore" class="md-add">＋ 添加世界书条目</button>
      </div>
      <div class="edit-list" id="edit-lore-list">
        ${items.map((item, i) => loreCard(item, i, npcIds, counts)).join("")}
      </div>
    </div>`;
  mdRebuild("edit-lore-list");
  // 归属：把渲染时的值落到卡上（之后只在用户改选时更新）——保存/徽标/计数一律读它，
  // select 只当视图，避免"原生下拉内部选中态掉了导致存空"。
  document.querySelectorAll("#edit-lore-list .edit-card").forEach((card) => {
    if (card.dataset.subject === undefined) {
      card.dataset.subject = card.querySelector('[data-field="subject"]')?.value ?? "";
    }
  });
  // 给每个下拉打上"人物名集合"指纹，之后只在人物改名/增删时才重建选项
  syncLoreSubjectOptions();
}

const LORE_SUBJECT_MAX = 2; // 每个归属角色最多 2 条（与后端 LORE_SUBJECT_CAP 一致）

/** 归属条目的「已挂」计数：key = 角色名，value = 条数。 */
function loreSubjectCounts() {
  const counts = {};
  document.querySelectorAll("#edit-lore-list .edit-card").forEach((card) => {
    const v = card.dataset.subject ?? card.querySelector('[data-field="subject"]')?.value ?? "";
    if (v) counts[v] = (counts[v] || 0) + 1;
  });
  return counts;
}

/** 把当前脚本版本显示在世界工作台标题旁。
 *
 *  用途：改前端后用户"刷新了没"这件事必须可见——不然改了代码对方还跑着旧
 *  JS，两边一起猜。版本号取自 <script src="app.js?v=NN"> 的查询串。
 */
function showFrontendVersion() {
  const el = $("#wb-version");
  if (!el) return;
  const tag = document.querySelector('script[src*="app.js"]');
  const m = tag && tag.getAttribute("src").match(/[?&]v=(\d+)/);
  el.textContent = m ? `前端 v${m[1]}` : "";
}

/** 人物名清单：优先读实时 DOM（改名即时反映），退回载入时的数据。 */
function npcIdList() {
  const live = Array.from(document.querySelectorAll("#edit-npc-list .edit-card"))
    .map((c) => c.querySelector('[data-field="id"]')?.value?.trim())
    .filter(Boolean);
  if (live.length) return live;
  return Object.keys(state.worldData?.npcs || {});
}

function subjectOptions(selected, npcIds, counts) {
  const opts = ['<option value="">（无 · 纯关键词触发）</option>'];
  if (selected && !npcIds.includes(selected)) {
    // 改名/删人后的悬空引用：留着可见，不然数据会静默丢掉
    opts.push(
      `<option value="${escapeHtml(selected)}" selected>${escapeHtml(selected)}（不在人物表）</option>`
    );
  }
  npcIds.forEach((name) => {
    const full = (counts[name] || 0) >= LORE_SUBJECT_MAX && name !== selected;
    opts.push(
      `<option value="${escapeHtml(name)}"${name === selected ? " selected" : ""}${full ? " disabled" : ""}>` +
        `${escapeHtml(name)}${full ? `（已满 ${LORE_SUBJECT_MAX} 条）` : ""}</option>`
    );
  });
  return opts.join("");
}

/** 归属下拉的选项同步。
 *
 *  ⚠️ 坑（2026-09-13 实测踩中，症状："选了归属，保存后归属消失"）：对 `<select>`
 *  重建 innerHTML 会**丢掉当前选中值**——select 的 dirty value flag 一旦置位（用户
 *  选过一次），新插入的 option 上的 `selected` 属性就不再被采纳，重建即回到无选中
 *  （value 变 ""）。所以规则有三条：
 *
 *  1. **重建后必须把值写回**：`sel.value = cur`；
 *  2. 只在"人物名集合"变化时才重建（改名/增删人物），选值变化只改禁用态；
 *  3. 绝不在 mousedown（用户正要拉开下拉）时重建——那会顶掉刚弹出的菜单。
 */
function syncLoreSubjectOptions({ rebuild = false } = {}) {
  const cards = Array.from(document.querySelectorAll("#edit-lore-list .edit-card"));
  if (!cards.length) return;
  const npcIds = npcIdList();
  const known = new Set(npcIds);
  const counts = loreSubjectCounts();
  const nameSet = JSON.stringify(npcIds);
  cards.forEach((card) => {
    const sel = card.querySelector('[data-field="subject"]');
    if (!sel) return;
    // 以卡上记的值为准（select 只是个视图；它内部选中态会掉，见上方注释）
    const cur = card.dataset.subject ?? sel.value ?? "";
    if (rebuild || sel.dataset.npcset !== nameSet) {
      sel.innerHTML = subjectOptions(cur, npcIds, counts);
      sel.value = cur; // ← 关键：重建 innerHTML 会丢选中，显式写回
      sel.dataset.npcset = nameSet;
    }
    // 禁用态与"已满"文案随选值即时更新（只动 option 属性，不重建、不动选中）
    Array.from(sel.options).forEach((opt) => {
      if (!opt.value || !known.has(opt.value)) return; // 跳过"无"与"不在人物表"
      const full = (counts[opt.value] || 0) >= LORE_SUBJECT_MAX && opt.value !== cur;
      opt.disabled = full;
      const label = full ? `${opt.value}（已满 ${LORE_SUBJECT_MAX} 条）` : opt.value;
      if (opt.textContent !== label) opt.textContent = label;
    });
  });
}

function loreCard(item, i, npcIds, counts) {
  return `
    <div class="edit-card" data-index="${i}">
      <div class="form-grid">
        <div class="field">
          <label>ID</label>
          <input class="edit-field" data-field="id" value="${escapeHtml(item.id || "")}" />
        </div>
        <div class="field">
          <label>归属角色</label>
          <select class="edit-field" data-field="subject">${subjectOptions(
            item.subject || "",
            npcIds || [],
            counts || {}
          )}</select>
        </div>
      </div>
      <div class="field full">
        <label>关键词（逗号分隔，命中玩家输入/已采纳正文时触发本条）</label>
        <input class="edit-field" data-field="keywords" value="${escapeHtml((item.keywords || []).join(", "))}" />
      </div>
      <div class="field full">
        <label>正文（命中后全量注入编剧提示词）</label>
        <textarea class="edit-field" data-field="body" rows="3">${escapeHtml(item.body || "")}</textarea>
      </div>
      <div class="field full">
        <label><input class="edit-field" data-field="always_on" type="checkbox" ${item.always_on ? "checked" : ""} /> 常驻（每轮必注入，不需要关键词触发，不占触发名额上限）</label>
      </div>
      <button class="danger remove-item">删除</button>
    </div>
  `;
}

function renderEditScenes(data) {
  const items = data.scenes || [];
  $("#world-tab-scenes").innerHTML = `
    <h3>场景</h3>
    <div class="md-pane">
      <div class="md-side">
        <input class="md-search" placeholder="搜索场景…" />
        <div class="md-items"></div>
        <button id="add-scene" class="md-add">＋ 添加场景</button>
      </div>
      <div class="edit-list" id="edit-scene-list">
        ${items.map((item, i) => sceneCard(item, i)).join("")}
      </div>
    </div>`;
  mdRebuild("edit-scene-list");
}

function sceneCard(item, i) {
  return `
    <div class="edit-card" data-index="${i}">
      <div class="form-grid">
        <div class="field">
          <label>名称（即 ID，中文名；事件 location 与显示名共用此键）</label>
          <input class="edit-field" data-field="id" value="${escapeHtml(item.id || "")}" />
        </div>
        <div class="field">
          <label>别名（逗号分隔；变体名，供移动解析与 id 纠偏）</label>
          <input class="edit-field" data-field="aliases" value="${escapeHtml((item.aliases || []).join(", "))}" />
        </div>
      </div>
      <div class="form-grid">
        <div class="field">
          <label>消息域 region（地域名，如"镇上"；该场景事件只被同域 NPC 听闻；空 = 全域公共区）</label>
          <input class="edit-field" data-field="region" value="${escapeHtml(item.region || "")}" />
        </div>
      </div>
      <div class="field full">
        <label>可感知描述</label>
        <textarea class="edit-field" data-field="perceivable" rows="2">${escapeHtml(item.perceivable || "")}</textarea>
      </div>
      <button class="danger remove-item">删除</button>
    </div>
  `;
}

function renderEditNpcs(data) {
  const entries = Object.entries(data.npcs || {});
  $("#world-tab-npcs").innerHTML = `
    <h3>人物</h3>
    <div class="md-pane">
      <div class="md-side">
        <input class="md-search" placeholder="搜索人物…" />
        <div class="md-items"></div>
        <button id="add-npc" class="md-add">＋ 添加人物</button>
      </div>
      <div class="edit-list" id="edit-npc-list">
        ${entries.map(([id, card]) => npcCard(id, card)).join("")}
      </div>
    </div>`;
  mdRebuild("edit-npc-list");
  refreshNpcLoreHints();
}

/** 人物卡上的只读提示：这个人挂着哪些归属世界书条目（在场即注入）。
    人物信息 = 卡 + 归属条目，这里让"缺一侧"一眼可见。 */
function refreshNpcLoreHints() {
  const owned = {};
  document.querySelectorAll("#edit-lore-list .edit-card").forEach((card) => {
    const subject = (
      card.dataset.subject ?? card.querySelector('[data-field="subject"]')?.value ?? ""
    ).trim();
    if (!subject) return;
    const id = card.querySelector('[data-field="id"]')?.value?.trim() || "（未命名条目）";
    (owned[subject] ||= []).push(id);
  });
  document.querySelectorAll("#edit-npc-list .edit-card").forEach((card) => {
    const hint = card.querySelector(".npc-lore-hint");
    if (!hint) return;
    const name = card.querySelector('[data-field="id"]')?.value?.trim() || "";
    const mine = owned[name] || [];
    if (mine.length) {
      hint.className = "npc-lore-hint";
      hint.innerHTML = `归属世界书条目（在场即注入）：${mine.map(escapeHtml).join("、")}`;
    } else {
      hint.className = "npc-lore-hint empty";
      hint.innerHTML = "归属世界书条目：无";
    }
  });
}

function npcCard(id, card) {
  const c = card || {};
  const isPlayer = !!c.is_player;
  return `
    <div class="edit-card${isPlayer ? " is-player" : ""}">
      <div class="form-grid">
        <div class="field">
          <label>姓名（即 ID，中文名；participants 与显示名共用此键）</label>
          <input class="edit-field" data-field="id" value="${escapeHtml(id || "")}" />
        </div>
        <div class="field">
          <label>听域（逗号分隔地域名；决定该 NPC 听说过哪些区域的公开旧事；空 = 按亲历事件推导）</label>
          <input class="edit-field" data-field="region" value="${escapeHtml((c.region || []).join(", "))}" />
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
        <label>幕后注</label>
        <textarea class="edit-field" data-field="private_note" rows="2">${escapeHtml(c.private_note || "")}</textarea>
      </div>
      <div class="field full">
        <label>自知隐秘</label>
        <textarea class="edit-field" data-field="personal_secrets" rows="2">${escapeHtml(c.personal_secrets || "")}</textarea>
      </div>
      <div class="form-grid">
        <div class="field">
          <label><input class="edit-field" data-field="has_actor" type="checkbox" ${c.has_actor ? "checked" : ""} /> 使用 Actor</label>
        </div>
        <div class="field">
          <label><input class="edit-field" data-field="is_player" type="checkbox" ${isPlayer ? "checked" : ""} /> <span class="player-check">主角（人物表有且仅有一个）</span></label>
        </div>
      </div>
      <div class="npc-lore-hint empty"></div>
      ${
        isPlayer
          ? '<div class="player-lock">主角 · 不可删除（换主角：在另一张卡上勾选「主角」）</div>'
          : '<button class="danger remove-item">删除</button>'
      }
    </div>
  `;
}

function renderEditAxes(data) {
  const items = data.axes || [];
  $("#world-tab-axes").innerHTML = `
    <h3>数值轴</h3>
    <div class="md-pane">
      <div class="md-side">
        <input class="md-search" placeholder="搜索数值轴…" />
        <div class="md-items"></div>
        <button id="add-axis" class="md-add">＋ 添加数值轴</button>
      </div>
      <div class="edit-list" id="edit-axis-list">
        ${items.map((item, i) => axisCard(item, i)).join("")}
      </div>
    </div>`;
  mdRebuild("edit-axis-list");
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

/* ---------- 主从布局（左列表 + 右单卡）助手 ----------
   所有 .edit-card 常驻 DOM，只是隐藏未选中的——各 read 函数与
   collectWorldEditData / 脏跟踪因此完全不用改；这里只负责摘要列表的构建、选中与过滤。 */
function mdBadgeFor(kind, card) {
  const badge = (cls, text) => `<span class="md-badge${cls ? " " + cls : ""}">${text}</span>`;
  if (kind === "npc") {
    if (card.querySelector('[data-field="is_player"]')?.checked) return badge("gold", "主角");
    if (card.querySelector('[data-field="has_actor"]')?.checked) return badge("", "Actor");
  } else if (kind === "lore") {
    const subject = (
      card.dataset.subject ?? card.querySelector('[data-field="subject"]')?.value ?? ""
    ).trim();
    if (subject) return badge("gold", `归属·${escapeHtml(subject)}`);
    if (card.querySelector('[data-field="always_on"]')?.checked) return badge("", "常驻");
    const kw = splitList(card.querySelector('[data-field="keywords"]')?.value).length;
    if (kw) return badge("", `${kw} 关键词`);
  } else if (kind === "scene") {
    const region = card.querySelector('[data-field="region"]')?.value?.trim();
    if (region) return badge("", escapeHtml(region));
  }
  return "";
}

function mdRebuild(listId) {
  const list = $("#" + listId);
  const pane = list.closest(".md-pane");
  if (!pane) return;
  const kind = listId.replace("edit-", "").replace("-list", "");
  const sel = pane.dataset.sel || "";
  const rows = [];
  list.querySelectorAll(".edit-card").forEach((card) => {
    if (!card.dataset.mdkey) card.dataset.mdkey = `${kind}-${rows.length}`;
    const name = card.querySelector('[data-field="id"]')?.value?.trim() || "（未命名）";
    rows.push(
      `<div class="md-item${card.dataset.mdkey === sel ? " active" : ""}" data-key="${card.dataset.mdkey}">` +
        `<span class="md-name">${escapeHtml(name)}</span>${mdBadgeFor(kind, card)}` +
      `</div>`
    );
  });
  pane.querySelector(".md-items").innerHTML = rows.join("");
  // 选中校验：原选中项已删则回落到第一条
  const valid = sel && list.querySelector(`.edit-card[data-mdkey="${sel}"]`);
  mdShow(listId, valid ? sel : list.querySelector(".edit-card")?.dataset.mdkey || "");
}

function mdShow(listId, key) {
  const list = $("#" + listId);
  const pane = list.closest(".md-pane");
  if (!pane) return;
  pane.dataset.sel = key || "";
  list.querySelectorAll(".edit-card").forEach((c) => {
    c.style.display = c.dataset.mdkey === key ? "" : "none";
  });
  pane.querySelectorAll(".md-item").forEach((r) => r.classList.toggle("active", r.dataset.key === key));
}

function bindEditEvents() {
  document.querySelectorAll(".edit-list").forEach((list) => {
    list.onclick = (e) => {
      const btn = e.target.closest(".remove-item");
      if (!btn) return;
      btn.closest(".edit-card").remove();
      mdRebuild(list.id);
      // 删除是纯 click（无 input/change），必须手动触发脏检查
      refreshDirty();
      // 删掉归属条目后，人物卡上的提示跟着更新；删掉人物后下拉选项要重算
      if (list.id === "edit-lore-list") refreshNpcLoreHints();
      if (list.id === "edit-npc-list") syncLoreSubjectOptions({ rebuild: true });
    };
    // 主角唯一性（2026-09-13）：勾上任意一张卡的「主角」，其余自动取消；
    // 重渲染人物列表让"删除"按钮随之出现/消失——数据从当前 DOM 读回，编辑不丢。
    // 用 on* 赋值而非 addEventListener：bindEditEvents 会被重复调用，避免监听器堆积。
    list.onchange = (e) => {
      const box = e.target.closest?.('[data-field="is_player"]');
      if (box && box.checked && list.id === "edit-npc-list") {
        const pane = list.closest(".md-pane");
        const prevIdx = Array.from(list.querySelectorAll(".edit-card")).findIndex(
          (c) => c.dataset.mdkey && c.dataset.mdkey === pane?.dataset.sel
        );
        list.querySelectorAll('[data-field="is_player"]').forEach((cb) => {
          if (cb !== box) cb.checked = false;
        });
        const npcs = readNpcs();
        renderEditNpcs({ npcs });
        bindEditEvents();
        const fresh = Array.from($("#edit-npc-list").querySelectorAll(".edit-card"));
        const target = fresh[prevIdx >= 0 ? prevIdx : 0];
        if (target) mdShow("edit-npc-list", target.dataset.mdkey);
        return;
      }
      // 改了 ID：左侧摘要的名字跟着刷新（不动选中项）；若是人物改名，
      // 归属下拉的选项集合也变了（旧名会变成"不在人物表"的悬空引用）
      if (e.target.matches?.('[data-field="id"]')) {
        mdRebuild(list.id);
        if (list.id === "edit-npc-list") syncLoreSubjectOptions({ rebuild: true });
      }
      // 改了归属：别人下拉里的"已满"余额、人物卡提示、左侧徽标都要跟着走
      if (e.target.matches?.('[data-field="subject"]')) {
        // 选中那一刻就把值记在卡上——保存路径从此不依赖 select 的内部选中态
        const card = e.target.closest(".edit-card");
        if (card) card.dataset.subject = e.target.value ?? "";
        syncLoreSubjectOptions();
        mdRebuild("edit-lore-list");
        refreshNpcLoreHints();
      }
    };
  });

  // 主从交互：点摘要行切详情、搜索框过滤摘要行
  document.querySelectorAll(".md-pane").forEach((pane) => {
    const listId = pane.querySelector(".edit-list")?.id;
    if (!listId) return;
    pane.onclick = (e) => {
      const item = e.target.closest(".md-item");
      if (item) mdShow(listId, item.dataset.key);
    };
    pane.oninput = (e) => {
      if (!e.target.classList?.contains("md-search")) return;
      const q = e.target.value.trim().toLowerCase();
      pane.querySelectorAll(".md-item").forEach((row) => {
        row.style.display = row.textContent.toLowerCase().includes(q) ? "" : "none";
      });
    };
  });

  const addLore = $("#add-lore");
  if (addLore) addLore.onclick = () => {
    $("#edit-lore-list").insertAdjacentHTML(
      "beforeend",
      loreCard({}, 999, npcIdList(), loreSubjectCounts())
    );
    mdRebuild("edit-lore-list");
    refreshDirty();
  };
  const addScene = $("#add-scene");
  if (addScene) addScene.onclick = () => {
    $("#edit-scene-list").insertAdjacentHTML("beforeend", sceneCard({}, 999));
    mdRebuild("edit-scene-list");
    refreshDirty();
  };
  const addNpc = $("#add-npc");
  if (addNpc) addNpc.onclick = () => {
    $("#edit-npc-list").insertAdjacentHTML("beforeend", npcCard("", {}));
    mdRebuild("edit-npc-list");
    refreshDirty();
  };
  const addAxis = $("#add-axis");
  if (addAxis) addAxis.onclick = () => {
    $("#edit-axis-list").insertAdjacentHTML("beforeend", axisCard({}, 999));
    mdRebuild("edit-axis-list");
    refreshDirty();
  };
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
  const rawMemory = val("memory_limit").trim();
  return {
    id: val("id"),
    name: val("name"),
    opening: val("opening"),
    summary: splitLines(val("summary")),
    start_time: val("start_time"),
    start_scene: val("start_scene").trim(),
    memory_limit: rawMemory === "" ? 50 : Math.max(0, Number(rawMemory) || 0),
  };
}

function readLore() {
  return Array.from(document.querySelectorAll("#edit-lore-list .edit-card")).map((card) => ({
    id: card.querySelector('[data-field="id"]')?.value ?? "",
    // 归属以"用户选中的那一刻"记在卡上为准（dataset.subject），select 的值只作兜底：
    // 原生 select 的内部选中态很脆（重建 innerHTML 会掉），保存不该依赖它。
    subject:
      card.dataset.subject ?? card.querySelector('[data-field="subject"]')?.value ?? "",
    keywords: splitList(card.querySelector('[data-field="keywords"]')?.value),
    body: card.querySelector('[data-field="body"]')?.value ?? "",
    always_on: !!card.querySelector('[data-field="always_on"]')?.checked,
  }));
}

function readScenes() {
  return Array.from(document.querySelectorAll("#edit-scene-list .edit-card")).map((card) => ({
    id: card.querySelector('[data-field="id"]')?.value ?? "",
    aliases: splitList(card.querySelector('[data-field="aliases"]')?.value),
    perceivable: card.querySelector('[data-field="perceivable"]')?.value ?? "",
    region: card.querySelector('[data-field="region"]')?.value.trim() ?? "",
  }));
}

function readNpcs() {
  const result = {};
  document.querySelectorAll("#edit-npc-list .edit-card").forEach((card) => {
    const id = card.querySelector('[data-field="id"]')?.value ?? "";
    if (!id) return;
    result[id] = {
      id,
      appearance: card.querySelector('[data-field="appearance"]')?.value ?? "",
      persona: card.querySelector('[data-field="persona"]')?.value ?? "",
      private_note: card.querySelector('[data-field="private_note"]')?.value ?? "",
      personal_secrets: card.querySelector('[data-field="personal_secrets"]')?.value ?? "",
      has_actor: !!card.querySelector('[data-field="has_actor"]')?.checked,
      is_player: !!card.querySelector('[data-field="is_player"]')?.checked,
      region: splitList(card.querySelector('[data-field="region"]')?.value),
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
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function saveAsWorld() {
  const newId = prompt(
    "复制为新世界：把当前世界（含全部演化）fork 成一份新内容包，原世界不动。\n请输入新世界 ID（英文/数字/下划线）"
  );
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
    state.worldDirty = false;
    await loadWorldList();
    if (confirm(`已复制为新世界：${res.world_id}。现在切换过去？`)) {
      await switchToWorld(res.world_id);
    }
  } catch (e) {
    alert(`复制失败：${e.message}`);
  }
}

async function refreshWorldModal() {
  if (state.worldDirty && !confirm("刷新会丢弃未保存的改动，确定吗？")) return;
  if (state.worldPanelOpen) {
    await loadWorldList();
  }
  await loadWorldBrowser();
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

function exportSave() {
  const url = `/api/sessions/${state.sid}/export`;
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
  await loadWorldList();
  if (confirm(`已导入为新世界：${data.world_id}。现在切换过去？`)) {
    await switchToWorld(data.world_id);
  }
}

async function importSave(file) {
  if (!file) return;
  const content = await readFileAsBase64(file);
  const res = await fetch(`/api/saves/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, content }),
  });
  if (!res.ok) {
    const text = await res.text();
    alert(`导入备份失败：${text}`);
    return;
  }
  const data = await res.json();
  alert(`已导入备份：${data.world_id}\n将切换到该世界。`);
  // 切换过去：同一世界则换存档名，跨世界则换世界。
  state.worldId = data.world_id;
  localStorage.setItem("aiworld_world_id", data.world_id);
  state.saveName = data.save_name;
  state.worldName = null;
  state.worldData = null;
  closeWorldPanel();
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
  if (state.worldDirty && !confirm("有未保存的改动，重置会一并丢弃。继续重置？")) return;
  if (!confirm("确定重置当前存档？事件、候选与运行状态会清空；工作台对概览、NPC 卡、场景、世界书的修改会保留。")) return;
  state.worldDirty = false;
  await api(`/api/sessions/${state.sid}/reset`, { method: "POST" });
  clearCandidateControls();
  await refreshState();
  await syncPendingFromServer();
  await loadWorldBrowser();
  alert("存档已重置");
}

/* ---------- 初始化 ---------- */

async function pickWorld() {
  // 启动选世界：localStorage 记忆优先，但必须仍存在；否则取列表第一个。
  let worlds = [];
  try {
    const data = await api(`/api/worlds`);
    worlds = data.worlds || [];
  } catch (e) {
    return null;
  }
  if (!worlds.length) return null;
  const remembered = localStorage.getItem("aiworld_world_id");
  const rememberedValid = worlds.some((w) => w.id === remembered);
  state.worldId = rememberedValid ? remembered : worlds[0].id;
  localStorage.setItem("aiworld_world_id", state.worldId);
  return state.worldId;
}

async function init() {
  // 设置加载独立于游戏会话：会话初始化失败不再连坐设置页。
  try {
    await loadSettings();
  } catch (e) {
    /* 设置接口失败时保留输入框空白，主流程继续 */
  }

  let picked = null;
  try {
    picked = await pickWorld();
  } catch (e) {
    picked = null;
  }
  if (picked) {
    try {
      await ensureSession();
      const info = await api(`/api/sessions/${state.sid}`);
      state.worldName = info.world;
      $("#world-name").textContent = info.world;
      await loadEventHistory();
      await refreshState();
      await syncPendingFromServer();
      await loadDirectorHistory();
    } catch (e) {
      addMessage("npc", `初始化失败：${e.message}`);
    }
  } else {
    addMessage("npc", "还没有世界：点右上角「世界工作台」→「切换世界」→ 新建一个。");
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
  // 状态徽章 = 抽屉「状态」页的入口（顶栏常显，主角没状态时也要能点进去查 NPC）。
  $("#state-badge").addEventListener("click", () => openDrawer("states"));
  $("#close-drawer").addEventListener("click", closeDrawer);

  document.querySelectorAll(".drawer-tabs .tab").forEach((btn) => {
    btn.addEventListener("click", () => switchDrawerTab(btn.dataset.tab));
  });

  $("#director-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await sendDirectorMessage();
  });
  $("#save-preset").addEventListener("click", savePreset);
    $("#save-settings").addEventListener("click", saveSettings);
  $("#refresh-usage").addEventListener("click", loadUsage);
  $("#refresh-debug").addEventListener("click", loadDebugTrace);

  $("#close-world").addEventListener("click", closeWorldModal);
  $("#wb-switch-world").addEventListener("click", toggleWorldPanel);
  $("#wb-refresh").addEventListener("click", refreshWorldModal);
  showFrontendVersion();
  $("#wb-save").addEventListener("click", saveWorldEdit);
  $("#wb-discard").addEventListener("click", discardWorldEdits);
  $("#wb-check").addEventListener("click", checkSessionWorld);
  document.querySelectorAll(".modal-tabs .tab").forEach((btn) => {
    btn.addEventListener("click", () => switchWorldTab(btn.dataset.tab));
  });
  $("#export-world").addEventListener("click", exportWorld);
  $("#export-save").addEventListener("click", exportSave);
  $("#save-as-world").addEventListener("click", saveAsWorld);
  $("#import-save").addEventListener("change", (e) => importSave(e.target.files[0]));
  $("#reset-world").addEventListener("click", resetWorld);
}

init();