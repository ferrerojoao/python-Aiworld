/**
 * 前端冒烟测试的公共载具：在 jsdom 里跑**真实的** index.html + app.js。
 *
 * 为什么要有它：2026-09-17 前的两个前端 bug（无世界时打 `/api/sessions/null/world`、
 * 「秘」标签判据写成 `!public`）都是靠**一次性 jsdom 探针**发现的，探针用完就删，
 * 等于每次重新发明。这里把载具固化下来，用例只写"摆桩 + 断言"。
 *
 * 用法：
 *   const { calls, result } = await bootApp({
 *     routes: [["/api/worlds", () => ({ worlds: [] })]],
 *     probe: `return { name: document.querySelector("#world-name").textContent };`,
 *   });
 *
 * ⚠️ `probe` 会被拼进**同一次 eval**（和 app.js 同一个作用域），所以它能直接看见
 * app.js 里的顶层 `const state` / 各函数。反过来也意味着：**跨 eval 拿不到它们**
 * （顶层 `const` 不挂在 window 上），别写成两次 eval。
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { JSDOM } from "jsdom";

const DIST = path.resolve(
  process.env.AIWORLD_WEB_DIST || path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "dist")
);

export function readDist(name) {
  return readFileSync(path.join(DIST, name), "utf8");
}

export function jsonResponse(data, status = 200) {
  return {
    ok: status < 400,
    status,
    headers: { get: (k) => (String(k).toLowerCase() === "content-type" ? "application/json" : null) },
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

/**
 * @param {{routes?: Array, probe?: string, waitMs?: number}} opts
 *   routes：`[匹配, 处理函数]`。匹配可以是字符串（前缀）或正则；处理函数收到
 *          `(url, opts)`，返回对象即当 JSON 回；返回 `{__raw:true, res}` 可自定义响应。
 *   probe： 在 app.js 之后、同一次 eval 里执行的代码，**必须 return** 一个可在
 *          Node 侧断言的普通值（字符串/数字/数组/对象）。可以是异步的。
 *   waitMs：等 `init()` 那一串异步请求落地的时间（桩都是立即 resolve，80ms 足够）。
 *   url：   载入 jsdom 的地址。要用 `?token=` 触发令牌吸收时改这里。
 */
export async function bootApp({
  routes = [],
  probe = "return null",
  waitMs = 80,
  url = "http://localhost:8765/",
} = {}) {
  const dom = new JSDOM(readDist("index.html"), {
    url,
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });

  const calls = [];
  dom.window.fetch = async (url, opts = {}) => {
    const u = String(url);
    // headers 也记下来：令牌注入这种"看不见的行为"只能从请求侧验证。
    calls.push({ url: u, method: (opts && opts.method) || "GET", headers: (opts && opts.headers) || {} });
    for (const [pattern, handler] of routes) {
      const hit = typeof pattern === "string" ? u.startsWith(pattern) : pattern.test(u);
      if (!hit) continue;
      const out = await handler(u, opts);
      if (out && out.__raw) return out.res;
      return jsonResponse(out === undefined ? {} : out);
    }
    return jsonResponse({ detail: `测试没给 ${u} 摆桩` }, 404);
  };

  const source = `
    (async () => {
${readDist("app.js")}
      await new Promise((r) => setTimeout(r, ${waitMs}));
      ${probe}
    })()`;

  let result;
  try {
    result = await dom.window.eval(source);
  } catch (err) {
    err.message = `app.js 在 jsdom 里跑挂了：${err && err.message}`;
    throw err;
  }
  return { dom, window: dom.window, document: dom.window.document, calls, result };
}

/** 常用的桩：一个存在存档的世界，`/state` 等接口都返回最小可用形状。 */
export function worldRoutes({ state = {}, worlds = [{ id: "w1" }] } = {}) {
  return [
    ["/api/settings", () => ({ llm_base_url: "", model_main: "m", model_cheap: "c", qc_enabled: false })],
    ["/api/worlds/w1", () => ({ id: "w1", has_save: true, name: "测试世界" })],
    ["/api/worlds", () => ({ worlds })],
    ["/api/sessions/open", () => ({ sid: "s1" })],
    ["/api/sessions/s1/ledger/events", () => ({ events: [] })],
    ["/api/sessions/s1/candidates/pending", () => ({ candidates: [] })],
    ["/api/sessions/s1/director/history", () => ({ messages: [] })],
    [
      "/api/sessions/s1/state",
      () => ({ clock: "08:00", scene_id: "测试场景", present_names: [], recent_scenes: [], goals: [], state_view: [], ...state }),
    ],
    [/^\/api\/sessions\/s1$/, () => ({ sid: "s1", world: "w1" })],
  ];
}

/** 在 probe 里可用的点击小工具（拼进 eval 的字符串）。 */
export const clickJs = (sel) =>
  `document.querySelector(${JSON.stringify(sel)}).dispatchEvent(new MouseEvent("click", { bubbles: true }));`;
