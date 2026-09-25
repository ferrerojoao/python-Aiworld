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

import { bootApp, clickJs, readDist, worldRoutes } from "./harness.mjs";

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

/* ---------- 落卡窗口 2.0（2026-09-19）：待落卡的人物 + 场景 ----------
   ⚠️ 机制内部叫「落卡」/ `unfiled`，**前台文案叫「转正」**（2026-09-22 用户要求）。
   断言一律按玩家看得见的文案写——文案错了就是 bug。 */

const unfiledState = {
  present_names: ["刘星", "卢克"],
  unfiled: [
    { name: "卢克", location: "测试场景", present: true },
    { name: "米娅", location: "酒馆", present: false },
    { name: "阿七", location: "", present: false },
  ],
  unfiled_scenes: [{ name: "村东苇塘", aliases: ["苇塘"] }],
};

test("在场名单里分得清「有卡 / 未转正」，入口报人物 + 场景的总数", async () => {
  const { result } = await bootApp({
    routes: worldRoutes({ state: unfiledState }),
    probe: `return {
      rail: document.querySelector("#present-npcs").textContent,
      entry: document.querySelector("#unfiled-entry").textContent,
      hidden: document.querySelector("#unfiled-entry").hidden,
    };`,
  });

  assert.match(result.rail, /卢克/, "未转正者也是在场的「人」，要在名单里");
  assert.match(result.rail, /未转正/, "在场名单必须看得出谁还没有档案");
  assert.equal(result.entry, "待转正 · 4", "入口要报全量（3 人 + 1 场景，含不在场的人）");
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
  assert.equal(result.active, true, "并且停在「转正」页");
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

test("展开待转正的一条：拉草稿预填、挂「AI 草稿」标记、确定时原样 POST", async () => {
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
  assert.ok(posted, "「确定转正」必须真的发请求");
  assert.equal(posted.body.appearance, "灰袍，左手有旧疤", "玩家没改的栏位原样送出去");
  assert.equal(posted.body.persona, "话少，但记性极好", "玩家改过的以玩家为准");
  assert.deepEqual(
    Object.keys(posted.body).sort(),
    ["appearance", "persona"],
    "只发能从正文推导的两栏；幕后注 / 自知的隐秘 / Actor 档位不进转正表单（转正后到工作台填）"
  );
  // 体检问题与本张卡无关时也会报出来——允许半成品态存在，但它必须被看见。
  assert.match(result.messages, /已转正/);
  assert.match(result.messages, /start_scene 不在场景表里/);
});

test("「已写出的事实」入口已下线：转正条目里没有这个按钮，也不去拉证据", async () => {
  const { result, calls } = await bootApp({
    routes: [
      ...worldRoutes({ state: unfiledState }),
      [/\/unfiled\/[^/]+\/draft$/, () => ({ name: "卢克", appearance: "灰袍", persona: "话少", evidence_count: 1 })],
    ],
    probe: `
      ${clickJs("#unfiled-entry .rail-item")}
      await new Promise((r) => setTimeout(r, 30));
      const luke = [...document.querySelectorAll("#unfiled-panel .st-group")]
        .find((r) => r.textContent.includes("卢克"));
      luke.querySelector(".land-toggle").dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 60));
      return {
        toggle: luke.querySelectorAll(".land-ev-toggle").length,
        box: luke.querySelectorAll(".unfiled-evidence").length,
        text: luke.textContent,
        buttons: [...luke.querySelectorAll("button")].map((b) => b.textContent),
      };`,
  });

  assert.equal(result.toggle, 0, "「已写出的事实」按钮不该再出现在转正条目里（2026-09-22 用户要求）");
  assert.equal(result.box, 0, "证据盒子一并撤掉——没有入口的盒子只是死重量");
  assert.ok(!result.text.includes("已写出的事实"), `条目里不该再出现这几个字：\n${result.text}`);
  assert.deepEqual(
    calls.filter((c) => c.url.endsWith("/evidence")),
    [],
    "没有入口就不该有人去拉证据"
  );
  assert.ok(result.buttons.includes("确定转正"), `「确定转正」必须还在：${JSON.stringify(result.buttons)}`);
});

/* ---------- 调试窗口：入口下线，代码保留（2026-09-22） ---------- */

test("调试入口已下线：顶栏与抽屉都没有「调试」，但面板与代码保留", async () => {
  const { result } = await bootApp({
    routes: worldRoutes(),
    probe: `return {
      entries: document.querySelectorAll('[data-tab="debug"]').length,
      staleButtons: [...document.querySelectorAll("button")].filter((b) => b.textContent === "调试").length,
      panel: document.querySelector("#tab-debug") !== null,
      refresh: document.querySelector("#refresh-debug") !== null,
      drawerTabs: [...document.querySelectorAll(".drawer-tabs .tab")].map((b) => b.textContent),
    };`,
  });

  assert.equal(result.entries, 0, "顶栏与抽屉都不该再有 data-tab=debug 的按钮（2026-09-22 用户要求）");
  assert.equal(result.staleButtons, 0, "文案层也不该再有「调试」按钮");
  assert.equal(result.panel, true, "面板要留着：入口下线、代码不删，以后还能加回来");
  assert.equal(result.refresh, true, "#refresh-debug 的接线也留着（恢复入口即可用）");
  assert.ok(result.drawerTabs.includes("转正"), `抽屉里要有「转正」页：${JSON.stringify(result.drawerTabs)}`);
});

test("场景段：展开拉草稿、写明**落卡不改听域**、确定 POST 到 /locations/{name}/file", async () => {
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
  // 旧文案说"转正后公开事件会进所有人的风闻范围（现在不会）"——那是"空 region = 全域公共"
  // 时代的结论。2026-09-24 反转默认之后落卡对听域**零影响**，警告必须跟着改，否则它自己
  // 就在误导玩家（两边都得说：默认不传播 + 去哪改）。
  assert.match(result.warn, /不传播/, "落卡不改听域：默认不传播要说出来");
  assert.match(result.warn, /世界工作台/, "还要说清去哪改（不然玩家只被告知了一个坏消息）");
  assert.ok(!/所有人/.test(result.warn), "旧的『进所有人风闻范围』不能再出现");
  // 这句是 textContent（不是 innerHTML），markdown 一律不解析 ⇒ 写 `**听域一个字不变**`
  // 到玩家眼里就是星号。要强调就得靠措辞，不能靠 markdown。
  assert.ok(!/\*\*/.test(result.warn), "警告文案里漏出了 markdown 的 ** —— 玩家看到的是星号");
  assert.ok(posted, "场景落卡也要真的发请求");
  assert.match(posted.url, /\/locations\/[^/]+\/file$/);
  assert.equal(posted.body.perceivable, "齐腰的苇子围着浅塘。");
  assert.deepEqual(Object.keys(posted.body), ["perceivable"], "场景表单只有描述一栏");
});

test("region 选项化：场景可选已有地域 + 「全域」，人物只听已有地域（都不再靠手打）", async () => {
  // 手打 region 的后果是**静默的**：场景填「青石镇」、人物误填「清石镇」，两边都不报错，
  // 只是那个人从此收不到消息。把已出现的值喂给 datalist，错字压根不在候选里。
  const data = {
    scenes: [
      { id: "主街", region: "青石镇" },
      { id: "网吧", region: "" },
      { id: "鱼市", region: "全域" },
    ],
    npcs: { 朱明: { region: ["清石镇"] }, 王蓉: {} },
  };
  const { result } = await bootApp({
    routes: worldRoutes(),
    probe: `
      renderEditScenes(${JSON.stringify(data)});
      renderEditNpcs(${JSON.stringify(data)});
      const sInput = document.querySelector('#edit-scene-list [data-field="region"]');
      const nInput = document.querySelector('#edit-npc-list [data-field="region"]');
      const opts = (id) => [...document.querySelectorAll("#" + id + " option")].map((o) => o.value);
      const label = (el) => el.closest(".field").querySelector("label").textContent;
      return {
        sList: sInput.getAttribute("list"),
        nList: nInput.getAttribute("list"),
        sOpts: opts("scene-region-options"),
        nOpts: opts("npc-region-options"),
        sLabel: label(sInput),
        nLabel: label(nInput),
      };`,
  });

  assert.equal(result.sList, "scene-region-options", "场景的 region 要挂上候选表");
  assert.equal(result.nList, "npc-region-options", "人物的听域也要挂");
  // 场景候选 = 「全域」+ 本世界已出现过的地域（场景卡 ∪ 人物卡），空串不进候选。
  assert.equal(result.sOpts.join("|"), "全域|清石镇|青石镇", "候选要含全域与两侧已有的地域");
  // 听域写「全域」是空转（全域事件本来就绕过听域这一路，后端也会滤掉）⇒ 不给这个候选。
  assert.equal(result.nOpts.join("|"), "清石镇|青石镇", "听域候选里不该有『全域』");
  assert.match(result.sLabel, /不传播/, "场景那条要说清留空 = 不传播");
  assert.match(result.nLabel, /别手打/, "人物那条要说明为什么给候选（错一个字就静默收不到）");
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

/* ---------- 附图（2026-09-24）----------
   图在服务端是相对路径串（assets/scenes/鱼市-3f9a1c72.png），前端统一走
   带令牌的 fetch → objectURL。这几条各自钉一个"真出过或一定会出"的坑：
   · `<img src>` 带不了 Authorization 头 ⇒ 非本机必 401 ⇒ 缺图静默消失
   · 缺图的人**不能**从「在场」消失（一格一人）
   · 卡片只回答"此刻"（哪里 + 谁在）⇒ 不放「最近去过」
   · 全量重写 ⇒ 附图路径不回传就等于保存一次把图抹掉 */

const sceneState = {
  scene_id: "鱼市",
  scene_perceivable: "青石板路两侧是铺面，门口晾着鱼干。",
  scene_image: "assets/scenes/鱼市-3f9a1c72.png",
  present_view: [
    { name: "刘星", portrait: "", is_player: true },
    { name: "朱明", portrait: "assets/npcs/朱明-8b21de04.png", is_player: false },
    { name: "老掌柜", portrait: "", is_player: false },
  ],
  recent_scenes: ["主街", "码头"],
};

test("场景卡：一格一人 —— 没图的人照样占一格，且不画占位剪影", async () => {
  const { result } = await bootApp({
    routes: worldRoutes({ state: sceneState }),
    probe: `
      const card = document.querySelector("#scene-card");
      return {
        people: card.querySelectorAll(".scene-person").length,
        noPic: card.querySelectorAll(".scene-person.no-pic").length,
        names: Array.from(card.querySelectorAll(".scene-person-name")).map((n) => n.textContent),
        pics: card.querySelectorAll(".scene-person-pic").length,
        hasStar: !!card.querySelector(".scene-chip-star"),
        // 「最近去过」与"图下那行 perceivable 描述"都撤了（2026-09-24 用户要求）。
        // 夹具里**故意**塞着 recent_scenes 与 scene_perceivable ⇒
        // "字段在"不等于"卡片要读它"，这几样都不许出现。
        recentChips: card.querySelectorAll(".scene-chip").length,
        hasRecentLabel: card.textContent.includes("最近去过"),
        hasDesc: !!card.querySelector(".scene-card-desc"),
        cardText: card.textContent,
      };`,
  });

  assert.equal(result.people, 3, "三个人就要有三格（含没图的老掌柜）");
  assert.equal(result.noPic, 2, "没图的两个人只出名字格");
  assert.equal(result.pics, 1, "只有朱明有头像");
  assert.equal(result.names.join("|"), "刘星主角|朱明|老掌柜");
  assert.equal(result.hasStar, true, "主角挂「主角」标");
  assert.equal(result.hasDesc, false, "图下那行 perceivable 描述不该再渲染");
  assert.doesNotMatch(result.cardText, /鱼干/, "描述里的字一个都不该出现");
  assert.equal(result.recentChips, 0, "「最近去过」的 chip 不该再出现");
  assert.equal(result.hasRecentLabel, false, "连那个小标题也不许留");
});

test("点场景卡里的图 → 看大图浮层（点浮层 / Esc 关掉，都不该顺手关卡片）", async () => {
  const { result } = await bootApp({
    routes: [
      ...worldRoutes({ state: sceneState }),
      // 图走取图端点：给一个能当 blob 读的响应（`jsonResponse` 上没有 blob()）。
      [
        "/api/sessions/s1/assets/",
        () => ({ __raw: true, res: { ok: true, status: 200, blob: async () => new Blob(["fake"]) } }),
      ],
    ],
    // ⚠️ jsdom 没有 `URL.createObjectURL`，而 `hydrateImages` 在 init 阶段就要用它
    // 把 src 填上 ⇒ 只能在 app.js **之前**补（probe 在之后，来不及）。
    pre: 'URL.createObjectURL = () => "blob:stub";',
    probe: `
      const card = document.querySelector("#scene-card");
      const box = document.querySelector("#img-lightbox");
      const big = document.querySelector("#lightbox-img");
      const cap = document.querySelector("#lightbox-cap");
      const closedAtStart = box.hidden;
      const picSrc = card.querySelector(".scene-card-img").getAttribute("src");

      ${clickJs("#scene")}
      await new Promise((r) => setTimeout(r, 20));

      ${clickJs("#scene-card .scene-card-img")}
      await new Promise((r) => setTimeout(r, 20));
      const opened = {
        open: !box.hidden,
        sameSrc: big.getAttribute("src") === picSrc,
        cap: cap.textContent,
        cardStillOpen: !card.hidden,
      };

      ${clickJs("#img-lightbox")}
      await new Promise((r) => setTimeout(r, 20));
      const closedByClick = { box: box.hidden, card: card.hidden };

      ${clickJs("#scene-card .scene-person-pic")}
      await new Promise((r) => setTimeout(r, 20));
      const avatarOpen = !box.hidden;
      const avatarCap = cap.textContent;

      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      const esc1 = { box: box.hidden, card: card.hidden };
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      return {
        closedAtStart, picSrc, ...opened, ...closedByClick,
        avatarOpen, avatarCap, esc1, cardAfterEsc2: card.hidden,
        // 关掉大图之后，卡片上那张图必须**原样还在**（closeLightbox 只清浮层那张，
        // blob URL 归 _imgCache 所有 —— 这里顺手 revoke 就会把卡片一起弄坏）。
        cardPicAfterClose: card.querySelector(".scene-card-img").getAttribute("src"),
      };
    `,
  });

  assert.equal(result.closedAtStart, true, "浮层初始不该显示");
  assert.ok(result.picSrc, "夹具该把场景图的 src 填上，否则这条什么都测不到");
  assert.equal(result.open, true, "点场景图该打开大图");
  assert.equal(result.sameSrc, true, "大图复用卡片那张的 blob URL：不重新请求、不新建 objectURL");
  assert.equal(result.cap, "鱼市", "浮层下方显示场景名");
  assert.equal(result.cardStillOpen, true, "点图不该把卡片一起关掉");
  // ⚠️ 探针里 `closedByClick` 是 `...closedByClick` 展开的 ⇒ 落在 result 顶层（`box` / `card`），
  //    不是 `result.closedByClick.box`。`esc1` 才是原样嵌套的。
  assert.equal(result.box, true, "点浮层本身关闭");
  assert.equal(result.card, false, "关大图也不该顺手关卡片");
  assert.equal(result.avatarOpen, true, "在场头像同样可点");
  assert.equal(result.avatarCap, "朱明", "头像浮层的名字跟着那个人");
  assert.equal(result.esc1.box, true, "Esc 关大图");
  assert.equal(result.esc1.card, false, "第一次 Esc 不该把卡片一起关掉");
  assert.equal(result.cardAfterEsc2, true, "大图关掉之后，第二次 Esc 才轮到卡片");
  assert.equal(result.cardPicAfterClose, result.picSrc, "关大图不该动卡片那张图（blob 归 _imgCache，别在这里 revoke）");
});

test("浮层自己带 display ⇒ 必须配一条 [hidden] 覆盖（jsdom 看不见这个 bug，只能查源码）", () => {
  // 🔴 为什么不用 jsdom 验：`getComputedStyle` 对带 `hidden` 属性的元素是**硬编码**成
  //    `display: none` 的 —— 实测（2026-09-24）即使**一条覆盖规则都没有**、只留
  //    `.lightbox { display: flex }`，它照样算 `none`。也就是说这个 bug（浮层永远挂在
  //    屏幕上，因为类的 display 盖掉了 UA 的 `[hidden]`）**在 jsdom 里永远看不见**，
  //    写成断言就是恒真的假守卫（变异检验：删掉覆盖规则，绿灯照旧）。
  //    所以这里退一步只查**源码里那对规则还在不在** —— 它不能证明渲染对，
  //    但能在有人"清理 CSS"时把这对规则拆开。
  const css = readDist("style.css");
  const body = (sel) => {
    const i = css.indexOf(sel + " {");
    return i < 0 ? null : css.slice(i + sel.length, css.indexOf("}", i));
  };

  assert.match(String(body(".lightbox")), /display:\s*flex/, "浮层该铺满视口（display:flex）");
  assert.match(
    String(body(".lightbox[hidden]")),
    /display:\s*none/,
    "少了这条，`.lightbox` 的 display:flex 会把 [hidden] 盖掉 ⇒ 浮层一直挂着"
  );
});

test("场景卡：点顶栏「场景」开合，点空白处 / Esc 关掉", async () => {
  const { result } = await bootApp({
    routes: worldRoutes({ state: sceneState }),
    probe: `
      const card = document.querySelector("#scene-card");
      const trigger = document.querySelector("#scene");
      const before = card.hidden;
      ${clickJs("#scene")}
      const afterClick = card.hidden;
      ${clickJs("#scene")}
      const afterSecond = card.hidden;
      ${clickJs("#scene")}
      document.body.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      const afterOutside = card.hidden;
      ${clickJs("#scene")}
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      const afterEsc = card.hidden;
      return { before, afterClick, afterSecond, afterOutside, afterEsc, triggerTag: trigger.tagName };`,
  });

  assert.equal(result.before, true, "默认不展开");
  assert.equal(result.afterClick, false, "点一下展开");
  assert.equal(result.afterSecond, true, "再点一下收起");
  assert.equal(result.afterOutside, true, "点卡片外面收起");
  assert.equal(result.afterEsc, true, "Esc 收起");
  assert.equal(result.triggerTag, "BUTTON");
});

test("取图必带 Authorization 头（<img src> 带不了头，所以走 fetch）", async () => {
  const { calls, result } = await bootApp({
    url: "http://localhost:8765/?token=s3cret",
    // ⚠️ 故意用一个**不带图**的 /state：否则 init 阶段就已经为场景卡取过图，
    //    请求计数会被"初始化那一轮"污染（这条要数的是缓存命中，不是总次数）。
    routes: [
      ...worldRoutes({ state: { scene_id: "鱼市", present_view: [] } }),
      [
        "/api/sessions/s1/assets/",
        () => ({ __raw: true, res: { ok: true, status: 200, blob: async () => ({ size: 3 }) } }),
      ],
    ],
    probe: `
      URL.createObjectURL = () => "blob:stub";
      URL.revokeObjectURL = () => {};
      const url = await imageUrl("assets/scenes/鱼市-3f9a1c72.png");
      const again = await imageUrl("assets/scenes/鱼市-3f9a1c72.png");
      const none = await imageUrl("");
      return { url, again, none };`,
  });

  const imgCalls = calls.filter((c) => c.url.includes("/assets/"));
  assert.equal(imgCalls.length, 1, `同一张图只取一次（缓存），实际 ${imgCalls.length} 次`);
  assert.equal(imgCalls[0].headers.Authorization, "Bearer s3cret", "必须带令牌，否则非本机 401");
  assert.equal(result.url, "blob:stub");
  assert.equal(result.again, "blob:stub", "第二次走缓存，不再发请求");
  assert.equal(result.none, "", "空路径不发请求，直接当缺图");
});

test("取图失败（401/404）→ 静默当缺图，不抛错", async () => {
  const { result } = await bootApp({
    routes: worldRoutes({ state: sceneState }),
    probe: `
      const url = await imageUrl("assets/scenes/不存在-00000000.png");
      return { url };`,
  });

  assert.equal(result.url, "", "取不到就是缺图，界面不画那一块");
});

test("工作台附图槽：路径必须随全量回传 —— 漏了就等于保存一次把图抹掉", async () => {
  const { result } = await bootApp({
    routes: worldRoutes({ state: sceneState }),
    probe: `
      document.querySelector("#world-tab-npcs").innerHTML =
        '<div class="edit-list" id="edit-npc-list">' +
        npcCard("朱明", { portrait: "assets/npcs/朱明-8b21de04.png" }) +
        npcCard("刘星", { is_player: true }) +
        "</div>";
      document.querySelector("#world-tab-scenes").innerHTML =
        '<div class="edit-list" id="edit-scene-list">' +
        sceneCard({ id: "鱼市", image: "assets/scenes/鱼市-3f9a1c72.png" }, 0) +
        "</div>";
      const npcs = readNpcs();
      const scenes = readScenes();
      return {
        portraits: Object.values(npcs).map((c) => c.portrait),
        images: scenes.map((s) => s.image),
        hasInput: !!document.querySelector('#edit-npc-list [data-field="portrait"]'),
      };`,
  });

  assert.equal(result.hasInput, true, "肖像槽要有一个 hidden input 让回读拿得到");
  // ⚠️ 用 join 而不是 deepEqual：探针的返回值来自 jsdom 那一侧的 realm，
  //    它的 Array.prototype 和 Node 的不是同一个，deepStrictEqual 会比原型而误判。
  assert.equal(result.portraits.join("|"), "assets/npcs/朱明-8b21de04.png|", "有图的带路径，没图的空串");
  assert.equal(result.images.join("|"), "assets/scenes/鱼市-3f9a1c72.png");
});

/* ---------- 候选控件与手改（2026-09-24） ----------

   玩家原话：「编剧写出来的东西一半满意一半不满意，采纳只能整体采纳」——重抽是从头
   再写，所以"局部不满意"只剩**就地手改**一条路。摘要由后台跟着正文对齐（界面刻意
   不显示），因为摘要此后每轮都被压成一行重新装配给编剧与导演。

   顺带把控件文字化：按钮从三个涨到六个，🎲 / ✓ 就得靠猜了。**只有 ◀ ▶ 留图标**
   ——方向是自明的，换成"上一稿/下一稿"只会把这一行撑长。 */

/** 一份待采纳的候选（只留前端会读的字段）。 */
const CAND = {
  candidate_id: "cand_1",
  turn_id: "turn_1",
  prose: "朱明从网吧出来，看见你愣了一下，把烟头踩灭。",
  side_effects: { narrative: { location: "主街", summary: "朱明在网吧门口看见刘星。" } },
  participants: ["刘星", "朱明"],
};

/** 待采纳候选 + PUT 手改口；PUT 的请求体收进 seen，好断言"发出去的到底是什么"。
 *  必须排在 worldRoutes 之前：载具按注册顺序取**第一条**命中的路由。 */
function candidateRoutes(seen = []) {
  return [
    [
      /^\/api\/sessions\/s1\/candidates\//,
      (url, opts = {}) => {
        if ((opts.method || "GET") === "PUT") {
          const body = JSON.parse(opts.body || "{}");
          seen.push(body);
          return { ...CAND, prose: body.prose };
        }
        return { candidates: [CAND] };
      },
    ],
  ];
}

/** 探针里的小工具（拼进 eval 的字符串，**不能出现反引号**）。 */
const BTN_HELPERS = `
  const btn = (t) => [...document.querySelectorAll(".message-controls button")].find((b) => b.textContent === t);
  const labels = () => [...document.querySelectorAll(".message-controls button")].map((b) => b.textContent).join("|");
  const click = (el) => el.dispatchEvent(new MouseEvent("click", { bubbles: true }));`;

/** 切候选是图标（方向自明），其余四个动作是文字（它们才需要辨认）。 */
const DEFAULT_LABELS = "◀|▶|重抽|编辑|采纳|放弃";

test("候选控件：只有 ◀ ▶ 是图标，其余一律文字", async () => {
  const { result } = await bootApp({
    routes: [...candidateRoutes(), ...worldRoutes()],
    probe: `
      ${BTN_HELPERS}
      return {
        labels: labels(),
        prose: document.querySelector(".message-text").textContent,
      };`,
  });

  assert.equal(result.prose, CAND.prose, "启动就该把待采纳的那一稿渲染出来");
  assert.equal(result.labels, DEFAULT_LABELS, "◀ ▶ 留图标、另外四个用文字，顺序也别乱");
});

test("手改：编辑 → 改字 → 保存修改 → 正文就地变新（不新开一稿，界面不谈摘要）", async () => {
  const seen = [];
  const { calls, result } = await bootApp({
    routes: [...candidateRoutes(seen), ...worldRoutes()],
    probe: `
      ${BTN_HELPERS}
      const msgCount = () => document.querySelectorAll("#messages .message").length;
      const before = msgCount();
      click(btn("编辑"));
      const ebox = document.querySelector(".message-edit");
      const opening = {
        hasBox: !!ebox,
        value: ebox ? ebox.value : null,
        textHidden: document.querySelector(".message-text").hidden,
        labels: labels(),
      };
      ebox.value = "朱明挑眉看了你一眼。";
      click(btn("保存修改"));
      await new Promise((r) => setTimeout(r, 30));
      return {
        opening, before,
        prose: document.querySelector(".message-text").textContent,
        textHidden: document.querySelector(".message-text").hidden,
        boxGone: !document.querySelector(".message-edit"),
        labels: labels(),
        messages: document.querySelector("#messages").textContent,
        after: msgCount(),
      };`,
  });

  assert.equal(result.opening.hasBox, true, "点「编辑」要出现编辑框");
  assert.equal(result.opening.value, CAND.prose, "编辑框里带的是这一稿的正文");
  assert.equal(result.opening.textHidden, true, "编辑框顶上不该还压着一层正文");
  assert.equal(result.opening.labels, "保存修改|取消", "手改态只留保存/取消，别的动作点不到");

  assert.equal(result.prose, "朱明挑眉看了你一眼。", "保存后正文就地变成改后的样子");
  assert.equal(result.textHidden, false, "正文要重新显示出来");
  assert.equal(result.boxGone, true, "保存完编辑框要收掉");
  assert.equal(result.labels, DEFAULT_LABELS, "退出编辑态后控件回到原有六个");
  assert.equal(result.after, result.before, "保存是就地替换：不该往对话里塞一条提示");
  assert.equal(result.messages.includes("摘要"), false, "界面不显示摘要变化（后台变就行）");

  assert.equal(seen.length, 1, "只发一次保存请求");
  assert.equal(seen[0].prose, "朱明挑眉看了你一眼。", "请求体就是改后的正文（摘要不在请求里）");
  const puts = calls.filter((c) => c.method === "PUT");
  assert.equal(puts.length, 1);
  assert.match(puts[0].url, /\/api\/sessions\/s1\/candidates\/cand_1$/, "就地改这一稿");
});

test("编辑框必须与正文同列折行（padding 一给，行数就变、高度就对不上）", () => {
  // ⚠️ jsdom 不排版 ⇒ 这条在行为上永远测不出来，只能退一步查源码（同「浮层是不是
  //    真藏起来了」那条）。高度是拿编辑框自己的 scrollHeight 量的：左右 padding 一给，
  //    可用宽度就比正文窄，同一段话多折出一行 ⇒ 高度静默差出一行。
  const css = readDist("style.css");
  const i = css.indexOf(".message-edit {");
  assert.ok(i >= 0, "style.css 里要能找到 .message-edit 规则");
  const body = css.slice(i + ".message-edit".length, css.indexOf("}", i));
  assert.match(body, /padding:\s*0\s*;/, "padding 必须为 0（要改就得同步改 fitEditorHeight）");
});

test("编辑框高度跟着内容量（和正文档一样大：同列折行 + 补回边框）", async () => {
  const { result } = await bootApp({
    routes: [...candidateRoutes(), ...worldRoutes()],
    probe: `
      ${BTN_HELPERS}
      // jsdom **不排版**：scrollHeight / offsetHeight / clientHeight 恒为 0。要给这条
      // 逻辑写守卫，只能自己塞假的尺寸进去——顺便连"边框要补回来"一起验。
      let fakeH = 512;
      const stub = (name, get) =>
        Object.defineProperty(HTMLTextAreaElement.prototype, name, { configurable: true, get });
      stub("scrollHeight", () => fakeH);          // 内容高（不含边框）
      stub("clientHeight", () => fakeH);          // 同上（无 padding / 无滚动条）
      stub("offsetHeight", () => fakeH + 2);      // 上下各 1px 边框
      click(btn("编辑"));
      const box = document.querySelector(".message-edit");
      const onOpen = box.style.height;
      fakeH = 900;                                // 玩家又敲进去几段
      box.dispatchEvent(new Event("input", { bubbles: true }));
      return { onOpen, onInput: box.style.height };
    `,
  });

  // 512 + (offsetHeight - clientHeight) = 514 ⇒ 高度与正文对齐，不会比正文矮 2px。
  assert.equal(result.onOpen, "514px", "一进编辑态就按内容高度撑开（含边框补偿）");
  assert.equal(result.onInput, "902px", "继续写要跟着长，否则又变成在小框里滚");
});

test("编辑框宽度必须与正文一致（宽度真排版才量得出 ⇒ 查源码里两处读同一个变量）", () => {
  // ⚠️ 同「padding 必须为 0」那条：jsdom **不排版**，宽度测不出来，只能退一步钉**同源**。
  //    正文的 max-width 与编辑态的 width 必须读同一个变量 —— 否则改一处忘一处，
  //    编辑框的宽度就和正文对不上（用户原话："宽度也要和稿件一样"）。
  const css = readDist("style.css");
  assert.match(css, /--bubble-max:\s*72%\s*;/, "气泡宽度要提成一个变量");
  assert.match(css, /\.message\s*\{[^}]*max-width:\s*var\(--bubble-max\)/, "正文读这个变量");
  assert.match(
    css,
    /\.message\.editing\s*\{[^}]*width:\s*var\(--bubble-max\)/,
    "编辑态也得读同一个（textarea 的 100% 要有个确定的基准，否则退回内在宽度、缩成窄条）",
  );
});

test("手改：进编辑态气泡挂 editing 类 → 宽度才有基准；退出就摘掉", async () => {
  const { result } = await bootApp({
    routes: [...candidateRoutes(), ...worldRoutes()],
    probe: `
      ${BTN_HELPERS}
      const box = () => [...document.querySelectorAll("#messages .message")].pop();
      const before = box().classList.contains("editing");
      click(btn("编辑"));
      const opened = box().classList.contains("editing");
      click(btn("取消"));
      const closed = box().classList.contains("editing");
      return { before, opened, closed };
    `,
  });

  assert.equal(result.before, false, "正文态不挂 editing（宽度仍由内容决定）");
  assert.equal(result.opened, true, "进编辑态要挂上，否则 textarea 的 width:100% 没有基准");
  assert.equal(result.closed, false, "退出编辑态要摘掉，否则这条空消息也占满一条");
});

test("编辑态中途待采纳的稿没了 → 气泡上那条 editing 也要摘掉（不然空消息占满一条）", async () => {
  // 真实路径：`syncPendingFromServer` 发现这一轮候选已经被别处采纳/放弃 ⇒ clearCandidateControls。
  // 那时气泡**不会**从对话里消失（只是控件被摘了），editing 类就会留在一条已经没有编辑框的
  // 消息上。桩按调用次数分岔：启动那次给待采纳的稿，探针里那次给空。
  let pendingHits = 0;
  const { result } = await bootApp({
    routes: [
      [
        /^\/api\/sessions\/s1\/candidates\/pending/,
        () => ({ candidates: ++pendingHits === 1 ? [CAND] : [] }),
      ],
      ...candidateRoutes(),
      ...worldRoutes(),
    ],
    probe: `
      ${BTN_HELPERS}
      const box = () => [...document.querySelectorAll("#messages .message")].pop();
      click(btn("编辑"));
      const opened = box().classList.contains("editing");
      await syncPendingFromServer();
      return { opened, cleared: box().classList.contains("editing") };
    `,
  });

  assert.equal(result.opened, true, "先得真的进了编辑态（前提没成立的话，这条守卫等于没跑）");
  assert.equal(result.cleared, false, "候选没了要连 editing 一起摘");
});

test("手改：点「取消」→ 编辑框收掉、正文回到原稿，且一个请求都不发", async () => {
  const { calls, result } = await bootApp({
    routes: [...candidateRoutes(), ...worldRoutes()],
    probe: `
      ${BTN_HELPERS}
      click(btn("编辑"));
      document.querySelector(".message-edit").value = "乱改的。";
      click(btn("取消"));
      return {
        prose: document.querySelector(".message-text").textContent,
        textHidden: document.querySelector(".message-text").hidden,
        boxGone: !document.querySelector(".message-edit"),
        labels: labels(),
      };`,
  });

  assert.equal(result.prose, CAND.prose, "取消要回到原稿");
  assert.equal(result.textHidden, false);
  assert.equal(result.boxGone, true);
  assert.equal(result.labels, DEFAULT_LABELS);
  assert.equal(calls.filter((c) => c.method === "PUT").length, 0, "取消不发请求");
});

test("手改途中发新输入要被拦下（发送会顺手把候选静默采纳，手改就白改了）", async () => {
  const { calls, result } = await bootApp({
    routes: [...candidateRoutes(), ...worldRoutes()],
    probe: `
      ${BTN_HELPERS}
      click(btn("编辑"));
      document.querySelector(".message-edit").value = "改到一半。";
      document.querySelector("#input").value = "接着问他";
      document.querySelector("#input-form").dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true })
      );
      await new Promise((r) => setTimeout(r, 30));
      return {
        messages: document.querySelector("#messages").textContent,
        kept: document.querySelector("#input").value,
        stillEditing: !!document.querySelector(".message-edit"),
      };`,
  });

  assert.equal(result.messages.includes("正在手改"), true, "要说清为什么发不出去，不能默默吞掉");
  assert.equal(result.kept, "接着问他", "被拦下时不能把玩家敲的字清掉");
  assert.equal(result.stillEditing, true, "拦下就该留在编辑态");
  assert.equal(
    calls.filter((c) => c.url.includes("/turn")).length,
    0,
    "一个回合都不该发——发出去服务端会把这一稿静默采纳掉"
  );
});

test("编辑框不跟着串到别的候选（否则「保存」会把 A 的正文写进 B 的候选）", async () => {
  const { result } = await bootApp({
    routes: [...candidateRoutes(), ...worldRoutes()],
    probe: `
      ${BTN_HELPERS}
      click(btn("编辑"));
      const opened = !!document.querySelector(".message-edit");
      // 模拟"候选被换掉"（切世界 / 导入存档 / 换稿都走这一句 renderCandidateMessage）
      state.candidates = [{ ...state.candidates[0], candidate_id: "cand_2", prose: "另一稿的正文。" }];
      state.currentCandidateId = "cand_2";
      renderCandidateMessage();
      return {
        opened,
        boxGone: !document.querySelector(".message-edit"),
        prose: document.querySelector(".message-text").textContent,
        textHidden: document.querySelector(".message-text").hidden,
        labels: labels(),
      };`,
  });

  assert.equal(result.opened, true, "前提：得先真的进了编辑态");
  assert.equal(result.boxGone, true, "换了候选，编辑框必须整块收掉");
  assert.equal(result.prose, "另一稿的正文。", "显示的是新候选的正文，不是 A 那段的残留");
  assert.equal(result.textHidden, false);
  assert.equal(result.labels, DEFAULT_LABELS);
});

test("项目地址放两处，且都在最底部：抽屉「设置」页 + 世界工作台「概览」页", async () => {
  // 用户要求（2026-09-24）：仓库地址放两个地方。
  // 断言特意钉的是"**最底部那一条**"，不是"文件里出现过" —— 位置本身是需求的一半
  // （概览页那处是"管理面才有、玩家一般不点进来"的隐蔽位）。
  const REPO = "https://github.com/ferrerojoao/python-Aiworld";
  const { result } = await bootApp({
    routes: [settingsOnly, ["/api/worlds", () => ({ worlds: [] })]],
    probe: `
      // 概览页是 app.js 里的模板串：必须真渲染一次才看得见（同一次 eval，能直接调它）
      renderEditOverview({ overview: {} });
      const last = (sel) => {
        const el = document.querySelector(sel).lastElementChild;
        return el ? { cls: el.className, html: el.innerHTML } : null;
      };
      return { settingsLast: last("#tab-settings"), overviewLast: last("#world-tab-overview") };`,
  });

  for (const [where, node] of [
    ["设置页", result.settingsLast],
    ["概览页", result.overviewLast],
  ]) {
    assert.ok(node, `${where}应当有内容`);
    assert.match(node.cls, /repo-foot/, `${where}最底部那一条应当是项目地址`);
    assert.ok(node.html.includes(REPO), `${where}里要有完整的仓库地址（含 href，地址写错也要红）`);
  }

  // 上面是直接调模板渲染的：模板写了却没人调，用户一样看不到 ⇒ 顺手钉一下接线。
  // （概览页内容要真开工作台才会渲染，而那条路要摆一整套 /world 桩，这里按源码守。）
  // ⚠️ 正则特意钉"调用"那种写法（行首缩进 + 结尾分号），**不能写成 `renderEditOverview\(`** ——
  //    那样会被 `function renderEditOverview(data) {` 这一行自己匹配上，删掉调用点也照样绿（假牙）。
  assert.match(
    readDist("app.js"),
    /^\s+renderEditOverview\([^)]*\);/m,
    "概览页的模板必须还在真实流程里被调用（只有函数定义不算）"
  );
});

/* ---------- SillyTavern 卡导入（2026-09-25） ----------
   真会出错的只有两处，都是"静默写错"那一类：
     ① 类型被启发式预选 → 玩家不看就点导入 → 人物名被当地名，整局正文全错；
     ② 候选改名没跟**条目 index** 绑 → 改第二条的名字写进第一条。 */

/** 读卡接口的最小形状（字段与 app/world/sillytavern.py::inspect 对齐）。 */
const CARD_INFO = {
  ok: true,
  format: "png-ccv3",
  spec: "chara_card_v3",
  name: "高岭爱花",
  suggested_world_id: "st-abcd1234",
  kind_hint: "",
  evidence: {
    description_chars: 120,
    has_type_header: false,
    has_character_book: true,
    lore_total: 3,
    lore_kept: 3,
    always_on: 1,
    always_on_chars: 200,
    lore_chars: 600,
    candidate_npcs: 2,
    first_mes_looks_like_meta: true,
    use_regex_entries: 0,
    secondary_keys_entries: 0,
    dropped_entry_fields: [],
  },
  greetings: [
    { index: 0, source: "first_mes", chars: 30, preview: "此处仅作为说明，实际开场白请右滑开局" },
    { index: 1, source: "alternate_greetings[0]", chars: 90, preview: "雪落下来" },
  ],
  candidates: [
    { index: 0, name: "夏晴天", keywords: ["夏晴天"], persona_chars: 100, preview: "活泼" },
    { index: 1, name: "慕容清寒", keywords: ["慕容清寒"], persona_chars: 120, preview: "冰山" },
  ],
};

const cardStub = ["/api/worlds/inspect-card", () => CARD_INFO];

test("导入卡：非 .zip 走读卡流程，表单铺出来且类型不预选", async () => {
  const { calls, result } = await bootApp({
    // ⚠️ `["/api/worlds", …]` 是**前缀**匹配，会把 /api/worlds/inspect-card 一起吃掉
    //    ⇒ 专用桩必须排在它前面。
    routes: [settingsOnly, cardStub, ["/api/worlds", () => ({ worlds: [] })]],
    probe: `
      ${clickJs("#open-world")}
      await new Promise((r) => setTimeout(r, 60));
      window.alert = () => {};
      await importWorld(new File([new Uint8Array([1, 2, 3])], "高岭爱花.png"));
      const box = document.querySelector("#wb-card-import-body");
      return {
        shown: !!box && !box.hidden,
        kinds: [...document.querySelectorAll('input[name="ci-kind"]')].map((e) => e.checked),
        candidates: [...box.querySelectorAll(".ci-cand")].map((row) => ({
          index: row.querySelector(".ci-cand-on").dataset.index,
          name: row.querySelector(".ci-cand-name").value,
          checked: row.querySelector(".ci-cand-on").checked,
        })),
        worldId: document.querySelector("#ci-world-id").value,
        opening: document.querySelector("#ci-opening").value,
        scene: document.querySelector("#ci-scene").value,
      };`,
  });

  assert.equal(result.shown, true, "读卡完要把表单摊开");
  // ⚠️ 从 jsdom 里带回来的数组，原型是 jsdom 的 Array.prototype ⇒ `assert/strict`
  //    的 deepEqual（会比原型）会误判成"不等"。一律先 `[...]` 转成 Node 的数组。
  assert.deepEqual(
    [...result.kinds],
    [false, false, false],
    "类型一个都不能预选——角色卡与场景卡的字段一样，猜错就是把人物名当地名"
  );
  assert.deepEqual(
    [...result.candidates.map((c) => `${c.index}:${c.name}`)],
    ["0:夏晴天", "1:慕容清寒"],
    "候选名字要挂在**条目 index** 上（不是行号）"
  );
  assert.deepEqual([...result.candidates.map((c) => c.checked)], [true, true], "候选默认全勾");
  assert.equal(result.worldId, "st-abcd1234");
  assert.equal(result.opening, "1", "first_mes 是说明页 ⇒ 默认跳到第一条备选");
  assert.equal(result.scene, "", "非场景卡不要乱填场景名");
  assert.equal(
    calls.some((c) => c.url.includes("/world/import")),
    false,
    "非 .zip 不该走 zip 那条老路"
  );
  assert.equal(calls.some((c) => c.url === "/api/worlds/inspect-card"), true, "应该去读卡");
});

test("导入卡：提交只收勾选的条目，改名跟着条目 index 走", async () => {
  const { calls, result } = await bootApp({
    routes: [
      settingsOnly,
      cardStub,
      ["/api/worlds/import-card", () => ({ ok: true, world_id: "st-abcd1234", report: {}, problems: [] })],
      ["/api/worlds", () => ({ worlds: [] })],
    ],
    probe: `
      ${clickJs("#open-world")}
      await new Promise((r) => setTimeout(r, 60));
      window.alert = () => {};
      window.confirm = () => false;
      await importWorld(new File([new Uint8Array([1])], "高岭爱花.png"));
      const rows = [...document.querySelectorAll("#wb-card-import-body .ci-cand")];
      rows[0].querySelector(".ci-cand-on").checked = false;
      rows[1].querySelector(".ci-cand-name").value = "我改的慕容";
      document.querySelector('input[name="ci-kind"][value="worldbook"]').checked = true;
      ${clickJs("#ci-do")}
      await new Promise((r) => setTimeout(r, 60));
      return { closed: document.querySelector("#wb-card-import-body").hidden };`,
  });

  const call = calls.find((c) => c.url === "/api/worlds/import-card");
  assert.ok(call, "点「导入为新世界」要打 import-card");
  const body = JSON.parse(call.body);
  assert.equal(body.kind, "worldbook");
  assert.equal(body.world_id, "st-abcd1234");
  assert.deepEqual(
    body.npcs_from_entries,
    [{ index: 1, name: "我改的慕容" }],
    "只收勾选的条目，且 index 跟着条目走（改第二条不能写到第一条上）"
  );
  assert.equal(result.closed, true, "提交后表单要收起来");
});

test("导入卡表单（排版）：单选框 / 复选框不能被 `.new-world input{width:100%}` 拉满整行", async () => {
  // 🔴 这条 bug 是「看不见的」：字段全在、值全对，只是三个类型单选各占一整行。
  //    jsdom 的 getComputedStyle **会算级联后的 width**（能区分 100% 与 auto），
  //    所以排版这一层可以配真守卫 —— 前提是把 style.css 注入进去（载具默认不加载
  //    外部 CSS，不注入的话 getComputedStyle 只会给默认值，断言等于没写）。
  const { result } = await bootApp({
    routes: [settingsOnly, cardStub, ["/api/worlds", () => ({ worlds: [] })]],
    pre: `
      window.alert = () => {};
      const __style = document.createElement("style");
      __style.textContent = ${JSON.stringify(readDist("style.css"))};
      document.head.appendChild(__style);
    `,
    probe: `
      ${clickJs("#open-world")}
      await new Promise((r) => setTimeout(r, 60));
      await importWorld(new File([new Uint8Array([1])], "高岭爱花.png"));
      const box = document.querySelector("#wb-card-import-body");
      const w = (el) => getComputedStyle(el).width;
      return {
        radio: [...box.querySelectorAll('input[name="ci-kind"]')].map(w),
        checkbox: w(box.querySelector(".ci-cand-on")),
        name: w(box.querySelector(".ci-cand-name")),
        text: box.textContent,
      };`,
  });

  assert.deepEqual(
    [...result.radio],
    ["auto", "auto", "auto"],
    "单选框是「点一下」的控件：width 必须是 auto，拉成 100% 就是一列大圆点"
  );
  assert.equal(result.checkbox, "auto", "复选框同理（以前靠 .ci-cand-on 单独补的例外，现已按类型整组撤回）");
  assert.equal(result.name, "100%", "名字输入框仍要占满中间那一列");
  // 强调只能用 `<b>`，不能写 markdown 的 `**`：这两个串进的是 innerHTML / textContent，
  // markdown 根本不会被解析 ⇒ 玩家看到的就是星号（2026-09-25 实测这里漏出 4 处）。
  assert.match(result.text, /卡主是一个人/, "类型说明要真的渲染出来 —— 否则下一条断言查的是空字符串");
  assert.ok(!/\*\*/.test(result.text), "可见文案里漏出了 markdown 的 ** —— 玩家看到的是星号");
});

test("导入入口接受 .png / .json（否则玩家在文件对话框里根本选不中卡）", () => {
  assert.match(
    readDist("app.js"),
    /id="import-world"[^>]*accept="[^"]*\.png[^"]*"/,
    "文件框的 accept 必须含 .png"
  );
});
