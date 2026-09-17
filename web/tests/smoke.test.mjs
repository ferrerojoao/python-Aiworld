/**
 * 前端冒烟测试：跑真实的 index.html + app.js（jsdom），断言"外部可见的行为"。
 *
 * 每条用例都对应一个**真出过的 bug**（不是凭空写的覆盖率）：
 *   1. 启动时没有世界 → 必须给出可操作的提示，且**不能**把 null 当 sid 去打接口
 *   2. 无世界时点「世界工作台」→ 曾是 `/api/sessions/null/world`（jsdom 里直接抛错）
 *   3. 「秘」标签的判据是 `public === false`，不是 `!public`
 *      —— 老存档的留痕里没有这个字段（`undefined`），用 `!` 会把"不知道"标成"秘"
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
