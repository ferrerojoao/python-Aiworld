/**
 * 前端冒烟测试：跑真实的 index.html + app.js（jsdom），断言"外部可见的行为"。
 *
 * 每条用例都对应一个**真出过的 bug**（不是凭空写的覆盖率）：
 *   1. 启动时没有世界 → 必须给出可操作的提示，且**不能**把 null 当 sid 去打接口
 *   2. 无世界时点「世界工作台」→ 曾是 `/api/sessions/null/world`（jsdom 里直接抛错）
 *   3. 「秘」标签的判据是 `public === false`，不是 `!public`
 *      —— 老存档的留痕里没有这个字段（`undefined`），用 `!` 会把"不知道"标成"秘"
 *   4. 事件日志的公开/私密标签只看接口给的**有效**名单：改判私密之后不能还停在
 *      【公开】（2026-09-18，改判写进了 access_overrides 但接口吐的是原始事件）
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { bootApp, clickJs, worldRoutes } from "./harness.mjs";

const settingsOnly = ["/api/settings", () => ({ llm_base_url: "", model_main: "m", model_cheap: "c" })];

test("无世界启动：给出提示 + 世界名显示（无世界）+ 不打 sessions/null", async () => {
  const { calls, result } = await bootApp({
    routes: [settingsOnly, ["/api/worlds", () => ({ worlds: [] })]],
    probe: `return {
      worldName: document.querySelector("#world-name").textContent,
      messages: document.querySelector("#messages").textContent,
    };`,
  });

  assert.equal(result.worldName, "（无世界）");
  assert.match(result.messages, /还没有世界/);
  const nullCalls = calls.filter((c) => c.url.includes("/api/sessions/null"));
  assert.deepEqual(nullCalls, [], `不该拿 null 当 sid 去请求：${JSON.stringify(nullCalls)}`);
});

test("无世界时点「世界工作台」：打开弹窗 + 只拉世界列表，不打 sessions/null", async () => {
  const { calls, result } = await bootApp({
    routes: [settingsOnly, ["/api/worlds", () => ({ worlds: [] })]],
    probe: `
      ${clickJs("#open-world")}
      await new Promise((r) => setTimeout(r, 60));
      return {
        modalOpen: document.querySelector("#world-modal").classList.contains("open"),
        panelOpen: document.querySelector("#wb-world-panel").classList.contains("open"),
      };`,
  });

  assert.equal(result.modalOpen, true, "「世界工作台」应该打开");
  assert.equal(result.panelOpen, true, "没有世界时应该直接把「切换世界」面板摊开");
  const bad = calls.filter((c) => c.url.includes("/api/sessions/null") || c.url.includes("/api/sessions//"));
  assert.deepEqual(bad, [], `无世界时不该请求会话级接口：${JSON.stringify(bad)}`);
  assert.ok(
    calls.some((c) => c.url === "/api/worlds"),
    "应该去拉世界列表（玩家才能新建/切换）"
  );
});

test("「秘」标签：public=false 才标，undefined（老存档）不标", async () => {
  const { result } = await bootApp({
    routes: worldRoutes({
      state: {
        last_state_change: {
          added: [
            { npc_id: "刘星", id: "st_1", text: "左腿瘸了", public: false },
            { npc_id: "朱明", id: "st_2", text: "老存档里的条目" }, // 没有 public → undefined
            { npc_id: "王蓉", id: "st_3", text: "人尽皆知", public: true },
          ],
          revised: [],
          removed: [],
          expired: [],
          revoked: [],
        },
      },
    }),
    probe: `return { left: document.querySelector("#state-change").textContent };`,
  });

  const marks = (result.left.match(/秘/g) || []).length;
  assert.equal(marks, 1, `只有 public=false 的那条该挂「秘」，实际挂了 ${marks} 次：\n${result.left}`);
  assert.match(result.left, /左腿瘸了/);
  assert.match(result.left, /老存档里的条目/);
  assert.match(result.left, /人尽皆知/);
});

test("?token=xxx：存进 localStorage + 从地址栏抹掉 + 之后每个 /api 调用都带 Bearer", async () => {
  const { calls, result } = await bootApp({
    url: "http://localhost:8765/?token=s3cret#top",
    routes: [settingsOnly, ["/api/worlds", () => ({ worlds: [] })]],
    probe: `return {
      stored: localStorage.getItem("aiworld_token"),
      href: window.location.href,
    };`,
  });

  assert.equal(result.stored, "s3cret", "令牌应该落进 localStorage，刷新后还在");
  assert.ok(!result.href.includes("token="), `地址栏应把 token 抹掉（别留在历史里）：${result.href}`);

  const apiCalls = calls.filter((c) => c.url.startsWith("/api"));
  assert.ok(apiCalls.length > 0, "启动阶段应该有 /api 调用");
  const missing = apiCalls.filter((c) => c.headers.Authorization !== "Bearer s3cret");
  assert.deepEqual(missing, [], `这些请求没带上令牌：${JSON.stringify(missing)}`);
});

test("没有令牌时不发 Authorization 头（本机单人使用不该多送一个空头）", async () => {
  const { calls, result } = await bootApp({
    routes: [settingsOnly, ["/api/worlds", () => ({ worlds: [] })]],
    probe: `return authHeaders({ "Content-Type": "application/json" });`,
  });

  assert.equal(result.Authorization, undefined);
  const withAuth = calls.filter((c) => c.headers.Authorization !== undefined);
  assert.deepEqual(withAuth, [], `没配令牌时不该出现 Authorization：${JSON.stringify(withAuth)}`);
});

test("事件日志：标签由接口给的有效名单决定（改判私密后不能还显示【公开】）", async () => {
  // 后端（Ledger.access_view）已经把改判结果并进 known_by 并附带 access_rejudged，
  // 前端只照渲染、不做二次计算。三条覆盖三种形态：改判后的私密、本来就公开、
  // 改判成"谁都不知道"（空名单——旧写法会渲染成残缺的「【私密·仅】」）。
  const { result } = await bootApp({
    routes: worldRoutes(),
    probe: `
      renderEventsTab({ events: [
        { at: "10:00", summary: "本来该是秘密的事", participants: ["朱明"],
          known_by: ["刘星"], access_rejudged: true },
        { at: "09:00", summary: "本来就是公开的事", participants: ["朱明"] },
        { at: "08:00", summary: "改判成谁都别想知道", known_by: [], access_rejudged: true },
      ] });
      return { text: document.querySelector("#world-tab-events").textContent };`,
  });

  assert.match(result.text, /【私密·仅刘星】（已改判）/, "改判私密必须显示出来：\n" + result.text);
  assert.match(result.text, /本来就是公开的事[\s\S]*【公开】/, "没改判的公开事件照旧：\n" + result.text);
  assert.equal(
    (result.text.match(/【公开】/g) || []).length,
    1,
    `【公开】只该出现在那一条公开事件上：\n${result.text}`
  );
  assert.match(result.text, /【私密·无知情者】（已改判）/, "空名单是私密且无人知情：\n" + result.text);
});
