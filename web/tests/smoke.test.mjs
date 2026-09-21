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

/* ---------- 落卡窗口 2.0（2026-09-19）：待落卡的人物 + 场景 ---------- */

const unfiledState = {
  present_names: ["刘星", "卢克"],
  unfiled: [
    { name: "卢克", location: "测试场景", present: true },
    { name: "米娅", location: "酒馆", present: false },
    { name: "阿七", location: "", present: false },
  ],
  unfiled_scenes: [{ name: "村东苇塘", aliases: ["苇塘"] }],
};

test("在场名单里分得清「有卡 / 未落卡」，入口报人物 + 场景的总数", async () => {
  const { result } = await bootApp({
    routes: worldRoutes({ state: unfiledState }),
    probe: `return {
      rail: document.querySelector("#present-npcs").textContent,
      entry: document.querySelector("#unfiled-entry").textContent,
      hidden: document.querySelector("#unfiled-entry").hidden,
    };`,
  });

  assert.match(result.rail, /卢克/, "未落卡者也是在场的「人」，要在名单里");
  assert.match(result.rail, /未落卡/, "在场名单必须看得出谁还没有档案");
  assert.equal(result.entry, "待落卡 · 4", "入口要报全量（3 人 + 1 场景，含不在场的人）");
  assert.equal(result.hidden, false);
});

test("没有待补档的人时入口整行不出现（空行只是噪音）", async () => {
  const { result } = await bootApp({
    routes: worldRoutes({ state: { present_names: ["刘星"], unfiled: [] } }),
    probe: `return { hidden: document.querySelector("#unfiled-entry").hidden };`,
  });

  assert.equal(result.hidden, true);
});

test("抽屉「落卡」：人物与场景两段并列，含不在场者与变体名", async () => {
  const { result } = await bootApp({
    routes: worldRoutes({ state: unfiledState }),
    probe: `
      ${clickJs("#unfiled-entry .rail-item")}
      await new Promise((r) => setTimeout(r, 40));
      return {
        open: document.querySelector("#drawer").classList.contains("open"),
        active: document.querySelector("#tab-unfiled").classList.contains("active"),
        text: document.querySelector("#unfiled-panel").textContent,
        collapsed: [...document.querySelectorAll("#unfiled-panel .land-body")].every((b) => b.hidden),
      };`,
  });

  assert.equal(result.open, true, "点入口应该打开抽屉");
  assert.equal(result.active, true, "并且停在「落卡」页");
  // 不在场的人也在——玩家离开酒馆后他还挂得住，这里是他唯一的入口。
  assert.match(result.text, /人物 · 3/);
  assert.match(result.text, /场景 · 1/);
  assert.match(result.text, /卢克/);
  assert.match(result.text, /在场/);
  assert.match(result.text, /米娅/);
  assert.match(result.text, /在酒馆/);
  assert.match(result.text, /去向不明/);
  assert.match(result.text, /村东苇塘/);
  assert.match(result.text, /变体：苇塘/);
  assert.equal(result.collapsed, true, "默认全部收起：收起时不发任何请求");
});

test("收起态不发草稿请求：没展开的条目不该花钱", async () => {
  const { calls } = await bootApp({
    routes: worldRoutes({ state: unfiledState }),
    probe: `
      ${clickJs("#unfiled-entry .rail-item")}
      await new Promise((r) => setTimeout(r, 40));
      return null;`,
  });

  const drafts = calls.filter((c) => c.url.endsWith("/draft"));
  assert.deepEqual(drafts, [], `没展开就不该要草稿：${JSON.stringify(drafts)}`);
});

test("展开待落卡的一条：拉草稿预填、挂「AI 草稿」标记、确定时原样 POST", async () => {
  let drafted = null;
  let posted = null;
  const { result } = await bootApp({
    routes: [
      ...worldRoutes({ state: unfiledState }),
      [
        /\/unfiled\/[^/]+\/draft$/,
        (u) => {
          drafted = u;
          return {
            name: "卢克",
            appearance: "灰袍，左手有旧疤",
            persona: "话少",
            evidence_count: 2,
          };
        },
      ],
      [
        /\/unfiled\/[^/]+\/file$/,
        (u, opts) => {
          posted = { url: u, body: JSON.parse(opts.body) };
          return { ok: true, name: "卢克", problems: ["start_scene 不在场景表里"] };
        },
      ],
    ],
    probe: `
      ${clickJs("#unfiled-entry .rail-item")}
      await new Promise((r) => setTimeout(r, 30));
      const luke = [...document.querySelectorAll("#unfiled-panel .st-group")]
        .find((r) => r.textContent.includes("卢克"));
      luke.querySelector(".land-toggle").dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 60));
      const boxes = luke.querySelectorAll(".land-body textarea");
      const prefilled = boxes[0].value;
      const badgeShown = !luke.querySelector(".land-draft-badge").hidden;
      boxes[1].value = "话少，但记性极好";  // 玩家改过草稿
      luke.querySelector(".land-file").dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 60));
      return { prefilled, badgeShown, messages: document.querySelector("#messages").textContent };`,
  });

  assert.ok(drafted, "展开就该去拉草稿");
  assert.equal(result.prefilled, "灰袍，左手有旧疤", "草稿要预填进 textarea");
  assert.equal(result.badgeShown, true, "预填的字段必须挂「AI 草稿 · 请核对」（草稿 ≠ 事实）");
  assert.ok(posted, "「确定落卡」必须真的发请求");
  assert.equal(posted.body.appearance, "灰袍，左手有旧疤", "玩家没改的栏位原样送出去");
  assert.equal(posted.body.persona, "话少，但记性极好", "玩家改过的以玩家为准");
  assert.deepEqual(
    Object.keys(posted.body).sort(),
    ["appearance", "persona"],
    "只发能从正文推导的两栏；幕后注 / 自知的隐秘 / Actor 档位不进落卡表单（落卡后到工作台填）"
  );
  // 体检问题与本张卡无关时也会报出来——允许半成品态存在，但它必须被看见。
  assert.match(result.messages, /已落卡/);
  assert.match(result.messages, /start_scene 不在场景表里/);
});

test("「已写出的事实」默认折叠：点了才拉证据（草稿的对账凭据）", async () => {
  let asked = null;
  const { result } = await bootApp({
    routes: [
      ...worldRoutes({ state: unfiledState }),
      [/\/unfiled\/[^/]+\/draft$/, () => ({ name: "卢克", appearance: "灰袍", persona: "话少", evidence_count: 1 })],
      [
        /\/unfiled\/[^/]+\/evidence$/,
        (u) => {
          asked = u;
          return {
            name: "卢克",
            location: "测试场景",
            last_seen: "2026-07-14T08:00:00",
            events: [{ id: "e1", at: "2026-07-14T08:00:00", location: "测试场景", body: "卢克擦着杯子。" }],
          };
        },
      ],
    ],
    probe: `
      ${clickJs("#unfiled-entry .rail-item")}
      await new Promise((r) => setTimeout(r, 30));
      const luke = [...document.querySelectorAll("#unfiled-panel .st-group")]
        .find((r) => r.textContent.includes("卢克"));
      luke.querySelector(".land-toggle").dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 60));
      const ev = luke.querySelector(".unfiled-evidence");
      const before = { hidden: ev.hidden, text: ev.textContent };
      luke.querySelector(".land-ev-toggle").dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 60));
      return { before, hidden: ev.hidden, text: ev.textContent };`,
  });

  // 没点之前盒子是空的（真拉过的话会先被写成"读取中…"再被填上）
  assert.equal(result.before.hidden, true, "默认折叠：不该请求证据");
  assert.equal(result.before.text, "", "默认折叠：盒子必须是空的");
  assert.ok(asked, "点了才拉");
  assert.equal(result.hidden, false);
  assert.match(result.text, /卢克擦着杯子/);
});

test("场景段：展开拉草稿、写明听域后果、确定 POST 到 /locations/{name}/file", async () => {
  let posted = null;
  const { result } = await bootApp({
    routes: [
      ...worldRoutes({ state: unfiledState }),
      [
        /\/locations\/[^/]+\/draft$/,
        () => ({ name: "村东苇塘", perceivable: "齐腰的苇子围着浅塘。", evidence_count: 1 }),
      ],
      [
        /\/locations\/[^/]+\/file$/,
        (u, opts) => {
          posted = { url: u, body: JSON.parse(opts.body) };
          return { ok: true, name: "村东苇塘", problems: [] };
        },
      ],
    ],
    probe: `
      ${clickJs("#unfiled-entry .rail-item")}
      await new Promise((r) => setTimeout(r, 30));
      const row = [...document.querySelectorAll("#unfiled-panel .st-group")]
        .find((r) => r.textContent.includes("村东苇塘"));
      row.querySelector(".land-toggle").dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 60));
      const ro = row.querySelector(".land-readonly");
      const warn = row.querySelector(".land-warn").textContent;
      const prefilled = row.querySelector(".land-body textarea").value;
      row.querySelector(".land-file").dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 60));
      return { roValue: ro.value, roReadonly: ro.readOnly, warn, prefilled };`,
  });

  assert.equal(result.prefilled, "齐腰的苇子围着浅塘。", "场景草稿预填进描述栏");
  assert.equal(result.roValue, "村东苇塘");
  assert.equal(result.roReadonly, true, "名称是场景键（历史事件都记着它），只读");
  assert.match(result.warn, /风闻/, "落卡会改变听域语义——必须在前端说出来，不能藏");
  assert.ok(posted, "场景落卡也要真的发请求");
  assert.match(posted.url, /\/locations\/[^/]+\/file$/);
  assert.equal(posted.body.perceivable, "齐腰的苇子围着浅塘。");
  assert.deepEqual(Object.keys(posted.body), ["perceivable"], "场景表单只有描述一栏");
});

/** 人物页锁态用例的桩：一个主角 + 一个普通 NPC，player_locked 由参数决定。 */
function npcWorldRoutes(locked) {
  const card = (id, isPlayer) => ({
    id,
    is_player: isPlayer,
    has_actor: !isPlayer,
    appearance: "",
    persona: "",
    private_note: "",
    personal_secrets: "",
    region: [],
  });
  return [
    ...worldRoutes(),
    [
      "/api/sessions/s1/world",
      () => ({
        overview: { id: "w1", name: "测试世界" },
        lorebook: [],
        scenes: [],
        npcs: { 刘星: card("刘星", true), 朱明: card("朱明", false) },
        axes: [],
        events: [],
        player_locked: locked,
      }),
    ],
    ["/api/sessions/s1/world/check", () => ({ ok: true, problems: [] })],
  ];
}

/** 打开工作台 → 切到「人物」页，然后把锁态相关的外部可见状态读回来。
 *  ⚠️ 探针返回值要跨 realm 传回 Node：只回**标量 / 字符串**，别回数组或对象
 *  （jsdom 里的 Array 原型与 Node 不同，deepStrictEqual 会莫名判不相等）。 */
const npcTabProbe = `
  ${clickJs("#open-world")}
  await new Promise((r) => setTimeout(r, 60));
  ${clickJs('.modal-tabs .tab[data-tab="npcs"]')}
  await new Promise((r) => setTimeout(r, 30));
  const cards = [...document.querySelectorAll("#edit-npc-list .edit-card")];
  const player = cards.find((c) => c.classList.contains("is-player"));
  const other = cards.find((c) => !c.classList.contains("is-player"));
  return {
    cardCount: cards.length,
    // 锁是全局状态 → 每一张卡的框都要禁：只禁主角那张等于没禁（换张卡就能勾上）。
    boxesDisabled: cards.map((c) => c.querySelector('[data-field="is_player"]').disabled).join(","),
    playerIdDisabled: player.querySelector('[data-field="id"]').disabled,
    otherIdDisabled: other.querySelector('[data-field="id"]').disabled,
    lockText: player.querySelector(".player-lock").textContent,
  };`;

test("主角锁定：已开局 → 每张卡的「主角」框都禁用，主角姓名也禁用", async () => {
  const { result } = await bootApp({
    routes: npcWorldRoutes(true),
    probe: npcTabProbe,
  });

  assert.equal(result.cardCount, 2);
  assert.equal(result.boxesDisabled, "true,true", "每一张卡的框都要禁，不只是主角那张");
  assert.equal(result.playerIdDisabled, true, "改名 = 换日志键，与换人同锁");
  assert.equal(result.otherIdDisabled, false, "非主角卡的姓名不受锁影响（改名是日常操作）");
  assert.match(result.lockText, /已锁定/);
  assert.match(result.lockText, /重置世界/, "必须指出唯一出口，否则玩家会一头撞上破坏性操作");
});

test("主角未锁定：框可勾，提示仍在教怎么换主角", async () => {
  const { result } = await bootApp({
    routes: npcWorldRoutes(false),
    probe: npcTabProbe,
  });

  assert.equal(result.boxesDisabled, "false,false");
  assert.equal(result.playerIdDisabled, false);
  assert.match(result.lockText, /换主角/);
  assert.doesNotMatch(result.lockText, /已锁定/);
});

/** 左栏「在场人物」的读取：只回标量/字符串（跨 realm 不能回数组）。 */
const railProbe = `
  const rows = [...document.querySelectorAll("#present-npcs .rail-item")];
  const tags = [...document.querySelectorAll("#present-npcs .st-tag")].map((t) => t.textContent).join("|");
  return {
    count: rows.length,
    labels: rows.map((r) => r.textContent).join("|"),
    firstIsPlayer: !!(rows[0] && rows[0].classList.contains("rail-player")),
    tags,
  };`;

test("在场人物：镜头场景里有主角 → 主角排第一并挂「主角」标", async () => {
  const { result } = await bootApp({
    routes: worldRoutes({
      state: { player_present: true, player_name: "刘星", present_names: ["朱明"] },
    }),
    probe: railProbe,
  });

  assert.equal(result.count, 2, "主角也要列进来（他不再是「还有谁在」的反面）");
  assert.equal(result.firstIsPlayer, true, "主角排第一：镜头就是他");
  assert.equal(result.labels, "刘星主角|朱明");
  assert.equal(result.tags, "主角");
});

test("在场人物：player_present=false 时不硬塞主角，空场仍显示「无人」", async () => {
  const { result } = await bootApp({
    routes: worldRoutes({ state: { player_present: false, player_name: "刘星", present_names: [] } }),
    probe: railProbe,
  });

  assert.equal(result.count, 1);
  assert.equal(result.firstIsPlayer, false, "没有位置事实就不该假装他在场");
  assert.equal(result.labels, "无人");
});

test("在场人物：主角不在场但别人在 → 只列别人，不出现主角行", async () => {
  const { result } = await bootApp({
    routes: worldRoutes({
      state: { player_present: false, player_name: "刘星", present_names: ["朱明"] },
    }),
    probe: railProbe,
  });

  assert.equal(result.count, 1);
  assert.equal(result.labels, "朱明");
  assert.equal(result.tags, "");
});
