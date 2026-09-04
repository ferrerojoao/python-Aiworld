# AIWorld —— AI 文字世界引擎 · 技术设计文档（TDD）

> 日期：2026-09-03
> 对齐：REQ 机制需求清单正本（当前口径：11 条机制 + 导演方案五员 + 〇章数据契约）/ GDD（策划口径）
> 定位：REQ 的工程实现蓝图——把机制条款落成数据结构、存储布局、回合流水线与 Worker 协议，可直接照着写代码。

---

## 0. 技术决策摘要

| 决策点 | 选择 | 理由 |
|---|---|---|
| 后端 | **Python 3.11+ / FastAPI** | async 原生、Pydantic v2 做全部 schema 校验与强制输出 |
| 前端 | 轻量原生 TS/Vite（无重型框架） | 页面量小（正文流/面板/导演窗口/日志）；避免框架负担 |
| 通信 | REST（CRUD/操作）+ SSE（回合交付流） | 单向流用 SSE 比 WebSocket 简单；正文以"整段交付 + 前端打字机"实现（见 §5.5） |
| LLM 接口 | OpenAI 兼容 SDK（base_url 可配） | DeepSeek / Qwen / Ollama / 任意网关通用 |
| 存储 | `events.jsonl` 追加 + `save.json` 原子写 | 正文史实不可变 → 追加式；单机单玩家；文件可读可 diff；崩溃安全 |
| 并发模型 | 单进程 asyncio，回合驱动，无后台世界协程；同一存档同一时刻只处理一个回合/事务 | 世界只在玩家回合内推进；审计为采纳时同一请求内的提交后结算，不做独立后台写协程，天然避免写并发 |
| 事务 | 回合 = 事务：候选区可并存多份候选版本 → 显式采纳指定候选（或唯一候选时下一次输入自动采纳） = 原子提交 / 放弃重掷 = 候选版本增删但不落账 | 玩家是作者；正文与副作用一体成立或不成立，同时不打断连续输入 |
| 模板 | Jinja2（prompt 与正文底稿模板）+ Pydantic（结构化输出） | 提示与引擎代码分离 |
| 世界设定装配 | 两级：世界概要常驻 system（永不裁剪）+ 世界书候选行（id+摘要）随导演工作单、点名才展开全文（标签匹配零 LLM） | 硬规则常驻不失守；详细条目按需展开省 token、导演不被要求通读 |
| 测试 | pytest + 注入 FakeLLM（录制响应） | 离线可跑、可回归；核心断言=回合事务与信息边界 |
| 包管理 | uv | 现代、快、锁文件可靠 |

依赖方向（单向）：`api / runtime → agents → core`；`world` 只读内容包；`core` 不 import 任何内容包。

---

## 1. 仓库结构

```
aiworld/
├── pyproject.toml / uv.lock / .env.example / README.md
├── app/
│   ├── main.py                  # FastAPI 装配、静态托管、启动
│   ├── config.py                # pydantic-settings：端口/模型档位/预算/默认耗时
│   ├── api/
│   │   ├── routes_sessions.py   # 存档 CRUD / 状态 / 事件日志 / 预设覆盖
│   │   ├── routes_turn.py       # POST turn（SSE）+ 候选区 adopt/reroll/discard
│   │   └── routes_director.py   # 导演窗口（OOC 旁路）
│   ├── core/
│   │   ├── llm.py               # OpenAI 兼容网关（§9）
│   │   ├── store.py             # JSONL 追加 + save.json 原子写 + 备份
│   │   ├── clock.py             # 世界钟（只执行不记账）
│   │   ├── templates.py         # Jinja2 装载
│   │   └── redlines.py          # 引擎基础禁用词红线集
│   ├── ledger/                  # ★ 账本（真相的唯一持有者）
│   │   ├── events.py            # 事件记录类型 / 追加 / 内存索引
│   │   ├── queries.py           # where_is / present_at / visible_to / experiences
│   │   ├── memory.py            # 记忆条目规则化拼接（现拼，不落盘）
│   │   ├── access.py            # known_by 改判：改判私密（检索式收权）/ 改判公开
│   │   └── hooks.py             # M14 钩子台账 / 待澄清队列（save.json 内）
│   ├── rules/                   # 规则段（尽量零 LLM，规则优先）
│   │   ├── route.py             # 输入路由（L0 规则短路 + L1 轻量分类）
│   │   ├── movement.py          # M19 裁决链 / M1 推断 / M18 在场
│   │   ├── scenes.py            # M17 场景注册表 + 模板底稿渲染
│   │   ├── claims.py            # M2 玩家声明覆写 + 冲突判定
│   │   └── axes.py              # ③ 抽象属性轴结算（M5，二期预留）+ 记因留痕
│   ├── workers/                 # 五名无状态 Agent（每个 = 一个协议函数）
│   │   ├── director.py          # 导演
│   │   ├── actor.py             # NPC Actor
│   │   ├── storyteller.py       # 说书人
│   │   ├── qc.py                # 质检员
│   │   └── auditor.py           # 世界审计（采纳时提交后结算）
│   ├── runtime/
│   │   ├── session.py           # 存档生命周期（开/续/存）
│   │   ├── turn.py              # ★ 回合编排（§5.1 阶段表）
│   │   └── transaction.py       # 候选区暂存 / 指定候选原子提交 / 放弃清理
│   └── world/                   # 内容包加载与校验
│       ├── models.py            # 内容包 Pydantic 模型（§2.1）
│       └── loader.py            # 目录 → 对象；--check 校验入口
├── web/                         # 前端（Vite + TS）
│   └── src/（chat / panel / scene / director / ledger / settings）
├── content/<world>/             # ★ 内容包（与引擎彻底分离，见 §2.1）
└── tests/（unit / integration / smoke）
```

---

## 2. 数据契约（实现 schema）

### 2.1 内容包（只读资产，`content/<world>/`）

```
content/<world>/
├── world.json        # 元信息 + 世界概要（硬规则/基调）
├── lorebook.json     # 世界书条目 [{id, tags, summary, body}]，可空
├── scenes.json       # M17 场景注册表（预置节点）
├── npcs/*.json       # 每 NPC 一卡（〇①）
├── axes.json         # ③ 抽象属性轴声明（题材级，可空数组；二期启用，v1 预留）
└── presets.json      # 元配置·叙述预设默认（四成员）
```

```jsonc
// world.json —— 世界概要：天然公开，每份工作单 system 块常驻、永不裁剪
{
  "id": "qinghsi", "name": "青石镇",
  "summary": ["青石镇靠打渔为生，镇上人家大半识得彼此。",
              "硬规则：与现实世界无异，没有超自然力量、没有异能。"],
  "default_durations": { "move_per_edge_min": 10, "repair_pc_min": 20 },
  "author_banned_words": []
}

// lorebook.json —— 世界书：详细设定，默认缺席任何切片，导演点名才展开
[ { "id": "fish_market", "tags": ["market", "daily_life"],
    "summary": "青石镇鱼市：镇民交易与邻里八卦的中心，清晨最热闹。",
    "body": "鱼市在码头东侧，天亮前渔船上岸……（正文只在本场导演点名展开时进入切片）" },
  { "id": "zhang_grievance", "tags": ["npc_zhang", "history"],
    "summary": "张婶与王蓉家二十年前的宅基地纠纷，至今两家不搭话。",
    "body": "当年王家翻建占了张家两尺宅基地……（正文……）" } ]

// scenes.json —— 每节点 = 名字 · 标签 · 可感知区 · 开放时段 · 邻接点
[ { "id": "school_gate", "name": "校门口", "aliases": ["学校门口"],
    "tags": ["public", "school"],
    "perceivable": "铁门半掩，槐树荫下停着几辆自行车，午后的蝉鸣很吵。",
    "open_hours": "全天", "adjacent": ["main_street", "net_bar", "alley_old_building"] } ]

// npcs/zhuming.json —— 人物卡（〇①）
{
  "id": "npc_zhuming", "name": "朱明",
  "appearance": "个子高，校服袖口总卷到小臂。",
  "persona": "性格：好面子、讲义气、遇事先犟后软。说话风格：短句、爱用'哥'。前史：初二转学来青石镇，跟人打过几架。",
  "private_note": "【仅导演】他爸在县城欠了赌债，这是他最不愿提的事。",   // 幕后注
  "has_actor": true,                                // has_actor 档位初值
  "normal_schedule": "暑假一般睡到中午，下午到晚上泡网吧打游戏。"      // 正常日程（可选质量字段）
}

// axes.json —— ③ 轴声明（二期启用；v1 可空/预留，引擎不结算）
[ { "id": "favor", "label": "好感", "tags": ["relation"], "target": "npc_zhuming",
    "range": [-100, 100], "init": 0, "visible": true, "track_cause": true } ]

// presets.json —— 叙述预设默认（作者）
{ "style": "克制写实，白描为主，少用形容词堆砌。",
  "description_style": "以玩家五官可感知为限写景，心理描写只写玩家自己的。",
  "banned_words": [], "pace": "slow" }
```

### 2.2 存档 = 账本（`content/<world>/saves/<name>/`）

物理真源四块：**事件流**（events.jsonl）+ **世界设定**（只读引用内容包：world.json 概要 ⊕ lorebook.json 世界书）+ **实体档案**（内容包卡 ⊕ 存档运行层补丁）+ **元配置**（内容包默认 ⊕ 存档玩家覆盖）。位置 / 在场 / 秘密 / 公共均无独立存储——全由事件流查询得出（§3）。

```
saves/<name>/
├── save.json          # 引擎运行态 + 元配置玩家覆盖 + 实体运行层（原子写）
├── events.jsonl       # 事件流（追加写；正文史实不可变）
└── candidates/        # 候选事务暂存（非账本，可断线恢复；采纳/放弃后清理，见 §4）
```

**事件流记录**（每行一条 JSON，`events.jsonl`）：

```jsonc
// kind=narrative —— 玩家采纳后的正文成史实（叙述即公开的默认落点）
{ "id": "ev_00042", "kind": "narrative", "at": "2026-07-14T14:32",
  "location": "school_gate",
  "participants": ["player", "npc_zhuming"],      // 戏中在场者 = 私密名单的名单原料
  "known_by": null,                                // null=公开（人人可引）；数组=私密切名单
  "body": "校门口围了一小圈人……",                 // 史实正文：一经落库永不修改
  "source": "turn" }

// kind=location_fact —— 位置 · 结算时刻 · 来源（M18/M2 覆写/M17 结算）
{ "id": "ev_00041", "kind": "location_fact", "at": "2026-07-14T14:30",
  "subject": "npc_zhuming", "location": "net_bar",
  "source": "witness | event | override | director",
  "valid_until": null }                            // 覆写带有效期（M18/M2）：非空则到期自动失效

// kind=memo —— 审计附注（记忆条目定性附注，ref 采纳事件；追加不改原文）
{ "id": "ev_00043", "kind": "memo", "at": "2026-07-14T14:32",
  "ref": "ev_00042", "note": "她说到一半声音低了下去。" }
```

- **known_by 是访问控制状态**：正文（body）不可变；`known_by` 是每记录一个可改字段，改判只重设它、不触碰正文（§7）。
- **位置事实的"位置"真相即在此层**；叙述事件也带 `location`（事发地），因此"谁在哪"的查询横跨两类记录的索引。

**save.json**（引擎运行态 + 元配置 + 实体运行层）：

```jsonc
{
  "meta": { "world_id": "qinghsi", "save_name": "main", "created_at": "…", "next_event_id": 44 },
  "clock": "2026-07-14T14:32",                    // 世界钟：只执行不逐笔记账
  "narrative_preset": {                           // 元配置玩家覆盖（默认在内容包 presets.json）
    "style": "…", "description_style": "…", "pace": "fast",
    "banned_words_display_only": [] },            // 禁用词只读展示，随内容包更换
  "entities": {
    "npc_zhuming": {
      "lifecycle": "active",                      // active | retired（〇章生命周期）
      "has_actor": true,                          // 运行时档位：只升不降（升格通道）
      "forced_actor": false,                      // 玩家点名强制档（导演窗口操作）
      "persona_patch": null                       // 补卡事务产物（叙述时 内容包卡 ⊕ patch）
    } },
  "axes": { "favor@player->npc_zhuming": 15 },    // ③ 轴当前值（二期启用；v1 预留字段，不结算）
  "hooks": [ { "id": "hk_01", "text": "答应明天帮王蓉修电脑",
               "level": "npc", "status": "open", "opened_at": "…", "due": null,
               "related": ["npc_wangrong"] } ],   // M14 台账
  "pending_conflicts": [ { "id": "cf_01", "at": "…", "level": "major",
                           "desc": "…", "status": "open", "ref": "ev_00038" } ],
  "scene_addons": { }                             // M17 现场转正注册的节点（包外地点）
}
```

**② 经历 = 装配时现拼，不落盘**：记忆条目 = 引擎把 viewer 可引用的结构化事件按固定模板拼装（时间+地点+谁干了什么+在场者），叠加该事件的 memo 附注。LLM 不参与跨轮存储，只由审计事后补一句定性附注（memo）。**改判私密后各 NPC 切片自动跟随**——因为切片是装配时按 known_by 现过滤的，无缓存失同步问题（§7 强调的技术收益）。

---

## 3. 账本读写与查询（ledger/）

- **追加写**：`events.jsonl` 只 append；`store.append_event(rec)` 负责 id 分配（`next_event_id`）与落盘。
- **原子写**：`save.json` 写 `*.tmp` + `Path.replace`；读损坏自动回退 `.bak`。
- **启动加载**：全量读入 events.jsonl 建内存索引（几万条 <10ms，无需数据库）：
  - `by_id`；`by_entity_latest_loc`（每实体最近未过期位置事实，覆写带 valid_until 优先）；
  - `by_location`（该地点全部在场记录）；`by_participant`；`by_kind`。
  - 增量维护：回合采纳 append 后同步更新索引。
- **读接口（查询即真相）**：
  - `where_is(entity)` → 最近未过期位置事实（M18）；
  - `present_at(scene)` → 最近位置 = 该场景的实体集合（在场者 = 查询不是存储）；
  - `visible_to(viewer)` → 可引用事件集 = 世界概要 ∪ 公开条目 ∪ {私密 \| known_by ∋ viewer} ∪ {本场点名展开的世界书条目}（世界书候选行与展开规则见 §6）；
  - `experiences(entity, viewer)` → ② 记忆条目（规则化拼接，§2.2）。

---

## 4. 回合事务（候选区 / 采纳 / 回滚）

一回合 = 一个事务。正文过质检送达玩家后仍在**候选区**；**玩家采纳（显式或下一次输入自动采纳）那一刻才原子落账**。

```
PendingTurn（内存事务上下文）
├── 候选正文（说书人成品或导演采纳的玩家原文）
├── 副作用暂存：Δt（世界钟推进）· 待落 narrative/location_fact 记录
│               · axes 轴变更（二期预留）· 覆写与有效期 · known_by 初值
└── 关联信息：质检冲突标注（大矛盾 → 待澄清队列，只挂起不阻塞）
```

- **候选区呈现**：SSE 交付 `candidate` 事件（正文 + 副作用摘要 + 挂起矛盾），一个回合可陆续产生多份候选版本，全部保留供玩家比较。玩家操作：
  - **采纳指定候选** `POST /candidates/{candidate_id}/adopt` → `transaction.commit(candidate_id)`：把选中的候选正文与副作用一次追加/写入，随后执行提交后结算；成功后清理该回合其余候选文件。回合闭环。
  - **下一次输入自动采纳（仅单候选时）**：`POST /turn` 时若该回合只有一份未决候选，先自动执行 `transaction.commit()` 再进入新回合；若有多份候选，引擎不自动选择，玩家必须先指定采纳哪一份。
  - **重掷 / 抽卡** `POST /turns/{turn_id}/reroll` → 生成一份新的候选版本，**旧版本全部保留**，供玩家比较，账本零写入。
  - **放弃** `POST /turns/{turn_id}/discard` → 清理该回合全部候选版本，账本零写入；这是明确放弃整个回合，不是重掷。
  - **先开导演窗口商量**再回候选区定夺（候选事务驻留内存并持久化 `candidates/` 暂存，断线可续）。
- **崩溃安全**：候选期未 commit = 未落盘 = 未发生；只有 commit 后的追加/原子写可能落盘。
- **采纳后反悔** = 走导演窗口（矛盾补救 / 改判 / 覆写），与候选区重掷分轨。
- **重掷三档**（每档独立过质检并生成一份新候选）：`rephrase` 同一剧本指令重跑说书人；`redirect` 导演重排整场戏决策重判；`retarget` 玩家口述不满处、导演按意见定向改。

### 4.1 候选暂存目录（candidates/）

`candidates/` 不是账本，只保存“尚未落账的提案”。每个候选版本一个 JSON 文件，命名 `<turn_id>_<candidate_id>.candidate.json`；同一回合可以有多个候选文件并存，供玩家比较和选择。

文件内容 = `PendingTurn` 的单版本可恢复快照：

```jsonc
{
  "candidate_id": "cand_0007",
  "turn_id": "turn_0007",
  "trace_id": "tr_abc123",
  "mode": "initial | rephrase | redirect | retarget",
  "status": "pending",               // 正常情况下只存在 pending；adopted/discarded 用于标记清理异常
  "prose": "候选正文……",
  "side_effects": {
    "delta_minutes": 20,
    "narrative": {"location": "school_gate", "participants": ["player", "npc_zhuming"], "known_by": null},
    "location_facts": [],
    "axes": {},                       // 二期预留
    "overrides": []
  },
  "conflicts": [{ "level": "major", "desc": "…" }],
  "created_at": "…",
  "updated_at": "…"
}
```

生命周期：

1. **创建**：每次候选送达或重掷/抽卡时，以“原子写（tmp + replace）”生成一份新候选文件；旧候选文件**不删除、不覆盖**。
2. **采纳**：显式 `adopt(candidate_id)`，或该回合只有一份候选时下一次输入自动采纳；`transaction.commit()` 成功后删除**该回合全部候选文件**（被采纳的和未被采纳的都清理）；commit 失败则全部保留，允许重试。
3. **重掷 / 抽卡**：新增一份候选文件，旧候选全部保留，供玩家比较；账本始终零写入。
4. **放弃**：明确放弃整个回合时，删除该回合全部候选文件，账本零写入；这不是重掷，不会保留任何候选。
5. **启动恢复**：启动时扫描 `candidates/`，按 `turn_id` 分组装载所有 `pending` 候选到内存；前端展示候选列表供比较，玩家可选择采纳其中一份，或继续重掷，或放弃整个回合。未恢复的 pending 不参与任何账本查询。
6. **清理 / TTL**：正常采纳或放弃后即时清理；异常退出遗留的 pending 文件保留，由“恢复未决候选”流程消费；可配置 TTL（如 7 天）清理长期未处理的候选并记日志。
7. **损坏处理**：单个候选文件损坏时视为该候选丢失，不影响同回合其它候选，也不影响 `events.jsonl` / `save.json`；启动时跳过并告警。

**崩溃安全**：候选文件只代表“提案”，不代表史实；没有 commit 就不影响账本。`candidates/` 可以纳入备份，但不是真相源。

---

## 5. 回合流水线与五员协议

### 5.1 流水线阶段（runtime/turn.py）

```
玩家输入
  0. 路由 route（§5.1 附表）：query/导演窗口 → 旁路直答（零/低 LLM）；其余进主链
  1. 规则段预结算 rules_presolve（尽量零 LLM，只对 move/jump/claim/mixed）：
     目的地解析 → 可达/耗时Δt候选 → 位置推断候选域 → 在场者查询 → 覆写冲突提示
     → 产出 rule_bundle 作为导演输入；副作用写入 PendingTurn
  2. 装配导演工作单（§6：玩家可见 + 导演独享区 + rule_bundle）
  3. 导演判定 → 分层剧本指令 directive（§5.3）
  4. 若 directive 含 actor 节点（深抉择）→ 逐 NPC 装配其隔离工作单（viewer=npc）
     → Actor 决策块填入 directive 对应节点（回流，导演不二次判定）
  5. 说书人成文（需成文时）：directive → 正文（JSON 信封 {prose, time_hint?}）；time_hint 计入 Δt
     导演直接采纳玩家原稿时跳过（说书人 0 次）
  6. 质检（必经）：文风/泄漏/矛盾/禁用词 → 成品正文 + 冲突标注
  7. SSE 交付候选区 → 玩家选择采纳某一候选 / 唯一候选自动采纳（commit，§4）→ 提交后结算（§5.7）

全流程生成并携带 `trace_id`（以及 `turn_id` / `candidate_id`）：所有 worker 调用、LLM usage 日志、SSE 事件、审计 run 都记录同一 `trace_id`，便于回放和排错。
```

**路由类别**（L0 规则短路，L1 轻量分类兜底，`route.py`）：

| 类别 | 例 | 路径 | LLM |
|---|---|---|---|
| chat_act | "问问他昨天为什么打架" / "我翻墙进去" | 主链 | 全链 |
| move | "去网吧" / "回学校" | 规则段 + 模板场景，无对话则旁路直达 | 尽量零 LLM，规则优先 |
| jump | "等到晚上" | 规则段推钟 + 导演排时段简报 | 部分 |
| claim | "朱明在家睡觉" | 覆写规则段（M2 冲突判定） | 尽量零 LLM |
| mixed | "去校门口找朱明，问他打架的事" | 规则段预结算 + 主链一次成稿 | 全链 |
| query | "现在什么时辰 / 谁在场" | 读账本直答 | 尽量零 LLM |
| ooc | 导演窗口操作 | 旁路（§5.8） | 按需 |

### 5.2 Worker 一览

| Worker | 模型档位 | 温度 | 调用 | 输入 | 输出 |
|---|---|---|---|---|---|
| 导演 | 主模型 | 0.7 | 每回合 1 | 工作单（§6） | 分层剧本指令 directive |
| NPC Actor | 主模型/次档 | 0.8 | 深抉择 +1 | 本人隔离工作单 | 决策块 |
| 说书人 | 主模型 | 0.9 | 需成文 0~1 | directive（无导演区） | {prose, time_hint?} |
| 质检员 | 便宜快模型 | 0.2 | 每回合 1 | 初稿 + 受限参照区（§5.6） | {status, prose, issues} |
| 世界审计 | 便宜快模型 | 0.2 | 采纳时提交后结算 | 本回合落账事件 + 相关历史 | 结构性结果 |
| 意图分类 L1 | 最便宜模型 | 0 | 规则不短路时 1 | 玩家输入 + 场景 + 在场 | {route, mention, claim?} |

> **模型数量**：上表是可配置档位，不是必须六套模型。v1 默认只需要 **1~2 个模型**：导演 / Actor / 说书人可共用“主模型”，质检 / 审计 / L1 分类可共用“便宜模型”；甚至可以全部指向同一个本地模型。配置里未指定的档位自动回退到默认主模型或默认便宜模型。

### 5.3 导演（workers/director.py）

输入 = 玩家原话 + rule_bundle + 工作单。产出**分层剧本指令**（JSON 强制，禁正文段）：

```jsonc
{ "mode": "scene | adopt_player_body",
  "beats": [
    { "kind": "narrate", "text": "可见层要点：朱明从网吧出来，看见你愣了一下。" },
    { "kind": "speech", "speaker": "npc_zhuming",
      "meaning": "不想提昨晚的事，打岔问你怎么来了。", "tone_hint": "敷衍" },
    { "kind": "actor", "npc_id": "npc_zhuming",
      "question": "被追问打架的事，朱明是含糊带过还是翻脸？",
      "context": "他在网吧通宵刚出来，知道你是为他好但好面子。", "resolved": null }
  ],
  "lore_refs": [],   // 本场点名展开的世界书条目 id（导演从候选行勾选，见 §6）
  "motivation_note": "动机层：他怕你知道他爸欠债的由头。只进账本参考，不进正文。",
  "adopt_player_body": false }
```

- `mode=adopt_player_body`：玩家输入本身已是完整成文叙述 → 导演核对节奏/矛盾后直接采纳为候选正文，跳过说书人。
- **动机层纪律**：motivation_note 与幕后注是导演独享——不进说书人输入；需要正文呈现的情绪由导演在 visible beats 里译成**可写表象**（"脸红了/移开视线"）。闸口在分层交付。
- 导演读幕后注是安全的：产出物只到指令层，正文一律说书人成文。

### 5.4 NPC Actor（workers/actor.py）

- 触发：directive 里 `kind=actor` 的节点（有 has_actor 的 NPC 撞深抉择）。
- **输入 = 物理隔离工作单**：`build_work_order(viewer=npc_id, scope=深抉择情境)`——只含 persona（含补丁）、`experiences(self)`、`visible_to(self)`、当前场景可感知、本场 `directive.lore_refs` 点名展开的世界书正文。**不含**导演区、其它 NPC 私密、幕后注、世界书候选行（候选行仅供导演点名）。
- 输出决策块（回流填进 directive 节点）：

```jsonc
{ "decision": "含糊带过", "action_hint": "不接话，拉你去打街机",
  "tone": "不耐烦里带点心虚" }
```

### 5.5 说书人（workers/storyteller.py）

- 输入：directive 的 beats（含 Actor 回流块）+ 叙述预设（文风/描写方式）+ 当前场景可感知 + 相关人物 persona（非私区）。**无导演区 / 无幕后注**。
- 输出 JSON 信封（v1 整段返回，前端打字机播放；真逐 token 流式留二期）：

```jsonc
{ "prose": "正文……（按叙述预设直接成稿）",
  "time_hint": null | { "desc": "天黑了", "advance_to": "sunset" } }
```

- `time_hint` = M20 ③"AI 正文写了时间流逝 → 该次输出顺带推钟"的落点；计入回合 Δt（与规则段 Δt 汇总，正文描述与总 Δt 明显冲突时质检挂矛盾）。

### 5.6 质检员（workers/qc.py）——必经前置文字关

- **输入两区**：① 正文初稿（可改）；② **受限知识参照区（只读）**：本次戏中每个开口实体的可引用集摘要（`visible_to(实体)` 提炼）+ 场景可感知 + 本场点名展开的世界书条目。参照区与成品分轨——成品只取初稿与改写，参照区永远不进正文。
- 四关一次完成：
  - **文风一致性**：按叙述预设核对/润色；**禁用词硬拦**（引擎红线 ∪ 作者词表，命中即改稿）。
  - **泄漏比对**：某角色台词呈现的知识超出其参照区 → **脱敏改写**（模糊化）。泄漏 = 剧情错误，每个 LLM 都知道它会被查。
  - **矛盾分级**：小矛盾（措辞/描述与场景不符）直接改稿；大矛盾（与玩家明示设定/节奏档冲突）挂 `pending_conflicts`。
- 输出 `{ status: pass|fixed|conflict, prose, issues: [{level, desc}] }`。
- **大矛盾只挂起、不阻塞采纳**：待澄清队列随候选一并呈现，玩家采纳 = 连同矛盾一起采纳（正文照常成史实，矛盾留队待导演窗口消费）。
- 质检无幕后注输入（防质检自身泄漏与代答）。

### 5.7 世界审计（workers/auditor.py）——采纳时提交后结算

v1 的审计/记账在玩家“采纳”（含下一次输入自动采纳）时，于同一请求内完成，产物进账本影响后续轮。所有账本写入都收敛到采纳这一个串行点，避免后台协程与下一回合并发写 `events.jsonl` / `save.json`。
1. **记忆定性附注**：对本回合 narrative 事件补一句 memo 附注（便宜快模型；纯规则拼接不在此处——拼接是装配时的引擎活）。
2. **钩子更新（M14）**：从本回合事件识别承诺/待闭合项挂账；到期闭合；更新台账（识别用 LLM 语义，挂闭后供导演排戏与"求建议"）。
3. **关系结算复核**：M5 关系系统暂缓设计，v1 不执行关系轴结算；此处只保留接口位，待二期实现。
4. **矛盾发现**：本回合言行对照历史（新增事件 vs 相关旧事件/人物卡补丁）→ 矛盾挂 pending_conflicts。
5. **生命周期扫描（〇章）**：终态事件（死亡/永久离开）→ 实体置 `retired`（数据全保留；钩子留导演定夺）。

**失败/恢复**：审计任务带 `run_id`；若中途失败，正文已落账不回滚，但该 run 标记为 `failed`，并在下次采纳/启动时幂等补跑。所有审计写入必须可重入：memo 按 `ref+note` 去重，hooks/conflicts 按 `id` 覆盖。未来若改成真正后台异步，必须引入写锁/单写者队列并保留本恢复机制。

### 5.8 导演窗口（OOC 旁路，routes_director.py）

仅玩家主动开启，导演纯被动；窗口内一切是戏外话（玩家说了 ≠ 主角说了，导演的话 ≠ 正文）。四类请求：

| 请求 | 实现 | 账本 |
|---|---|---|
| 求建议 | 扫 hooks + 当前张力 → 2~3 条建议（收尾型 + 推动型） | 只读 |
| 答疑 | 查账本简略答（档案/事件/钩子） | 只读 |
| 剧情讨论 | 多轮磋商 → 产出简略输入建议 → 玩家复制进正文框 | 只读 |
| 幕后事务 | 静默覆写 / 矛盾补救 / 点名强制派活 / 事件访问改判（§7）/ 补卡事务 | **玩家确认后落账** |

- 幕后事务与候选区同闸门：玩家提出 → 确认 → 落账。静默覆写 = 落一条带有效期的 location_fact（source=director）+ 给该时段排戏，正文以"已发生"为基演。
- **补卡事务**：玩家口述 → 导演拟稿 + 与事件流一致性核对 → 玩家确认 → 写入 `entities[npc].persona_patch`（只增补 ①，不开放裸编辑）。
- 观察/打量等描写请求在**正文窗**输入，走正常正文流水线，不进窗口。

---

## 6. 上下文装配与切片（工作单）

核心函数 `build_work_order(viewer, scope)`——按 viewer 身份现配"本轮工作单"，小且固定：

```
viewer ∈ { director, npc_<id> }        # 说书人/质检不走此函数，走各自的定向输入
scope  = 当前场景可感知 ∪ 在场实体 ∪ 对话对象 ∪ 关键历史状态（M8 上下文收敛）
```

| 块 | director | npc_<id>（Actor） |
|---|---|---|
| 世界概要（常驻 system 块） | ✅ | ✅ |
| 世界书 · 候选行（id+摘要，供点名） | ✅ | ❌ |
| 世界书 · 点名展开条目正文（本场 lore_refs） | ✅ | ✅ |
| 公开事件条目（现拼记忆条目） | ✅ | ✅ |
| 私密条目 known_by∋viewer | ✅ | ✅（仅含自己的） |
| 幕后注 / 注入记忆（导演可读） | ✅ | ❌ |
| 钩子台账 / 待澄清队列 / 节奏档 | ✅ | ❌ |
| 本人 persona / 经历切片 | ✅ | ✅ |
| 场景模板底稿 / 可感知 | ✅ | ✅（可感知原则） |

- **世界书候选机制**（零 LLM 标签匹配）：装配器取 scope 上下文标签（当前场景 tags ∪ 在场 NPC id ∪ 活跃钩子标签）线性扫 `lorebook.json`，命中即产候选行 `{id, summary}`，上限内（默认 12 行）随导演工作单附上。导演点名 = directive 带 `lore_refs: ["fish_market"]`；引擎展开正文随下游切片注入说书人 / Actor / 质检参照区。世界书无 known_by、天然公开；候选与展开都不进记忆流（世界书只读静态，不落账）。
- **Token 预算**（分片可配）：system 块（常驻：世界概要全量 + 角色纪律 + 数值/输出纪律）→ 场景块 → 角色块（在场 persona 精简）→ 记忆块（现拼条目按 viewer 过滤 + budget 截断，超出丢最旧）→ 玩家最近窗。裁剪顺序固定：先砍记忆低相关 → 再压玩家窗——**世界概要永不参与裁剪**（硬规则常驻，失守即世界观崩坏）。
- **记忆条目拼接模板**（零 LLM）：`{时间} {地点}，{谁} {干了什么}，在场：{…}。{memo 附注若有}`。
- **规则化拼接的意义**：原料只取已记账字段——每轮概貌稳定、成本确定、不产生正文二次概括的幻觉漂移。

---

## 7. 秘密与访问控制实现（ledger/access.py）

### 7.1 known_by 落库

- 事件记录落库时，导演在剧本指令中声明私密性（`beat.private` 或 narrative 记录级 `known_by` 初值）：公开情境 → `known_by: null`（默认态，人人可引）；私下情境 → `known_by = 当场在场者`（取该场戏 participants）。不为保密增设字段或场景属性。
- 注入记忆（M16 涉密补全）= 落私密记录 `known_by = [被注入者]`；导演静默覆写是位置记录、不载知情语义。
- 装配时的可引用集 = `visible_to(viewer)`——私密名单外的实体查不到这条（§3/§6）。

### 7.2 改判私密 · 玩家纠错（公开 → 私密）——检索式收权

正文不动，只重设 `known_by`。名单 = 当场在场者 ∪ 公开期引用者：

1. **当场在场者**：取该记录 `participants`（机械直取，零查询）。
2. **公开期引用者（引擎检索 + 导演确认）**：以该事件的话题指纹（participants 人名 + 正文命名实体，用已知实体名做规则抽取）为键，线性扫该事件落库之后、仍公开期间的 narrative 记录，正文命中指纹者取其 participants —— 引用过即知情。引用者集合天然小（NPC 间传播只在玩家在场的叙述中发生、说出口才落库）。
3. **传开闸**：引用面超出当场在场者 → 引擎提示「已被 N 位 NPC 提及，收权后他们将言行失忆」→ 玩家确认执行（引用者全数入名单 = 只停止再扩散，已传出的收不回）/ 放弃则保持公开。
4. 引擎出名单草稿 → 导演勾选确认 → 落账（只改访问层）。遗漏兜底 = 审计「矛盾发现」运行期挂出，导演补名单或圆场——改判当下无需一次求全（检索是近似，最终裁决在导演确认）。

### 7.3 改判公开 / 官宣

- **改判公开 · 玩家纠错（私密 → 公开）**：清空 `known_by` 恢复公开（不产正文事件、不改旧条目）。
- **官宣不靠改判靠叙述**：导演排一场公开戏落新 narrative（公开情境）→ 全镇可引"他们在一起"；历史私密条目保持私密（翻旧账需逐条点名走上方改判公开）。

技术收益：因为切片是装配时现拼现过滤，known_by 一旦重设，所有 NPC 的记忆切片下一轮自动跟随——**访问状态修正不产生任何缓存失同步**。

---

## 8. 时钟与空间

### 8.1 世界钟（core/clock.py）

- 变量：save.json `clock`（datetime），只执行不记账。
- 推进来源三层（规则直落，无专门 agent）：
  ① 玩家声明时刻（"等到中午"/"第二天"）→ 认字直接推进 Δt；
  ② 干实事未提时间 → `world.json.default_durations` 取默认耗时，缺失用引擎兜底常量；
  ③ 正文写了时间流逝 → 说书人 `time_hint` 推 Δt（§5.5）。
- 对话回合时钟冻结（纯闲聊 Δt=0）；回合 Δt 汇总在 PendingTurn，commit 才落盘。
- 离线 = 存档搁置，时钟停留在最后推进时刻，重开从该刻续走。

### 8.2 M17 场景双轨（rules/scenes.py）

- 预置 = 内容包 `scenes.json` 节点（名字/别名/标签/可感知区/开放时段/邻接点）。别名表与邻接图在启动时建索引。
- 现场补建（转正）：走进包外地点 → 引擎生成节点挂邻接图、写入 `save.json.scene_addons`，之后可复用可被提及。初态由来源定：玩家声明 → 当场注册；导演排戏默认临时；说书人写景顺笔 → 装饰性文字不入图。
- 转正信号（引擎被动核对）：玩家回访/指代（指代消解依赖已注册）→ 钩子挂账 → 导演/NPC 复用；任一出现即补注册。导演可预注册。
- 一次性布景：剧情用完即从可导航集退场（事件流仍可回溯）。

### 8.3 M18 位置与在场者（ledger/queries.py）

- "X 此刻在哪" = `where_is(X)`：该实体最近未过期 location_fact（覆写带有效期优先，过期即失效回退到次新/推断）。事实只在结算当场写入，随钟自然过期。
- "谁在当前场景" = `present_at(scene)`：在场 = 查询不是存储。
- **空档位置查询三件套**：过期前最后事实 + 正常日程先验 + 时间差 → 引擎合成候选域 → 导演做叙事落点 → 玩家进门时在场结算对导演落点执行（导演落点本身落一条新位置事实）。

### 8.4 M1 NPC 位置推断（rules/movement.py）

- 输入：正常日程（内容包卡，可空）/ 最后事实与覆写（账本）/ 地点标签与开放时段（场景表）。
- 输出候选域（"暑假早晨朱明大概率在网吧"），非唯一答案；导演按叙事拍板。
- 正常日程空缺时退化为"最后事实 + 身份隐含大框"（身份带出的日常场所进入候选）。

### 8.5 M19 移动裁决链（rules/movement.py）

```
目的地解析（四类）→ 查可达（邻接图 BFS）→ 结算耗时 Δt →（可选沿途编排）
→ 到达 → present_at 在场者 → 场景描述 → 上下文重装配（只含可感知区）
```

- 目的地解析：① 地点直命中（场景别名表）；② 由人推位置（"找朱明"→ M1）；③ 指代回溯玩家记忆（"昨天那家店"→ 最近访问过的注册地点）；④ 未建档地点 = 接受声明 + M17 现场注册。另"随便走走"→ 漫游态（沿邻接边慢移，导演可排偶遇）。
- 自然语言只在路由/目的地解析一处被判断；其后可达/耗时/在场全规则 + 账本（尽量零 LLM）。
- **混合句**：规则段只预结算产出 rule_bundle，导演把移动与对话作为同一场戏一次判定（衔接由导演在 beats 内编排），正文一次成稿。

### 8.6 场景描述 · 模板底稿（rules/scenes.py）

- 常规到达：引擎按场景属性拼静态底稿（尽量零 LLM）——"这里是{名字}。{可感知区原文}。在场：{在场者名列表}"。
- 升级条件（导演判定）：首次到达新场景 / 剧情舞台 / 需特殊氛围 → 派说书人按叙述预设专门成文。

---

## 9. LLM 网关与模型档位（core/llm.py）

```python
class LLMGateway:
    def __init__(self, cfg): self._sem = asyncio.Semaphore(4)
    async def complete_json(self, messages, schema, *, model, temperature) -> dict
    async def complete_text(self, messages, *, model, temperature) -> str
```

- OpenAI 兼容，`base_url/model/key` 按档位可配；`complete_json` 优先走 JSON schema / structured output，Pydantic 校验 + 显式拒空 `{}` + 失败重试 2 次。
- **结构化输出容错（必做）**：当模型不支持 JSON schema、连续失败或返回非法 JSON 时，降级为“提示词要求 JSON + 正则/起止标记抽取”，再交 Pydantic 校验；仍失败则返回用户可见错误，并记录原始响应供排查。禁止把非法 JSON 静默当作空结果。
- `enable_thinking=False` 作为配置项（推理模型默认关思维链省 token）。
- 每调用记结构化日志：`trace_id / turn_id / candidate_id / worker / model / prompt_tokens / completion_tokens / ts`，供成本核查与回合排错。
- 模型档位表（config.py + .env 覆盖）：导演 / Actor / 说书人 / 质检 / 审计 / L1 分类六档，各自 model + temperature（§5.2）；未配置档位回退到默认主模型 / 默认便宜模型，通常 1~2 个模型即可。
- **v1 无向量检索**：召回/切片/检索式收权全部结构化查询 + 线性扫（几万条内 <10ms）。二期升级 = 事件流叠语义索引（embedding 落盘、启动重建），对内容包与存档结构零侵入；中文预留 bge 系接口。

---

## 10. API 设计

### 10.1 REST

```
GET    /api/worlds                          # 可用内容包列表
POST   /api/sessions                        # 新建存档 {world_id, save_name}
GET    /api/sessions/{sid}                  # 会话详情（时钟/场景/在场/预设/角标）
GET    /api/sessions/{sid}/state            # 右栏面板：时钟/场景/在场/可见轴（二期）/钩子角标
GET    /api/sessions/{sid}/ledger/events?cursor=   # 事件日志（玩家视角全量叙述 + 访问状态标注，只读）
PUT    /api/sessions/{sid}/presets          # 玩家覆盖叙述预设 {style?, description_style?, pace?}
```

### 10.2 SSE（回合交付流）

```
POST /api/sessions/{sid}/turn  {"input": "…"}
  → text/event-stream:
     event: route        data: {"trace_id", "route": "mixed", "scene": "school_gate"}
     event: candidate    data: {"trace_id", "turn_id", "candidate_id", "prose", "side_effects": {"clock_to": "…",
                            "locations": […], "axes": […](二期预留)}, "conflicts": […]}
     event: error        data: {"trace_id", "message"}
```

> 若该回合只有一份未决候选，`POST /turn` 会先自动采纳该候选（等价于显式 `adopt`），再开始处理新输入；若有多份候选，玩家必须先选择采纳哪一份，引擎不自动选择——此时 `POST /turn` 应返回“需要先选择候选”（如 409/提示），或由前端阻止提交直到玩家 adopt / discard。
>
> 候选区操作（普通 POST，非流式）：

```
GET    /api/sessions/{sid}/candidates/pending   # 恢复未决候选列表（断线/重启后用；按 turn 分组）
POST   /api/sessions/{sid}/candidates/{candidate_id}/adopt
POST   /api/sessions/{sid}/turns/{turn_id}/reroll   {"mode": "rephrase|redirect|retarget", "note": "…"}
POST   /api/sessions/{sid}/turns/{turn_id}/discard
```

### 10.3 导演窗口

```
POST /api/sessions/{sid}/director
  {"topic": "advice"}                 → {"suggestions": [{"text": "…", "kind": "closing|driving"}]}
  {"topic": "qa", "question": "…"}    → {"answer": "…"}        # 只读
  {"topic": "discuss", "message": "…"}→ {"reply": "…"}         # 多轮，可产出输入建议
  {"topic": "backstage", "action": "override|remedy|force_actor|access_rejudge|amend_card",
   "payload": {…}}                    → 玩家确认后落账（两段式确认）
```

---

## 11. 前端（web/）

Vite + TS + 原生 DOM 轻量组件：

| 组件 | 职责 |
|---|---|
| `ChatView` | 正文流 + 打字机（SSE candidate 的 prose 播放）；**候选区版本列表**：展示/比较多份候选，可单独查看、选择采纳 / 重掷 / 放弃；提示“唯一候选时直接继续输入 = 采纳该候选”；挂起大矛盾角标（可见不阻塞） |
| `DirectorPanel` | OOC 窗口：求建议 / 答疑 / 剧情讨论 / 幕后事务（含改判、补卡、点名派活表单） |
| `SceneCard` | 当前场景 + 在场 NPC（模板底稿直出；首达/剧情舞台时展示说书人成品） |
| `StatePanel` | 时钟 / 在场 / 可见轴（二期）/ 钩子与待澄清角标；candidate 采纳后刷新 |
| `LedgerView` | 事件日志时间线（只读 + 公开/私密/正文分层展示，玩家可点名某条发起改判） |
| `SettingsPanel` | 叙述预设覆盖（文风 / 描写方式 / 节奏档；禁用词只读展示） |

---

## 12. 配置（config.py + .env）

```
AIWORLD_HOST=127.0.0.1
AIWORLD_PORT=8765
AUTH_TOKEN=                     # 空 = 仅本机；非空 = 要求 X-Auth-Token
REQUIRE_AUTH_FOR_NON_LOCAL=true # host 非 127.0.0.1/::1 且 AUTH_TOKEN 为空时拒绝启动
LLM_BASE_URL=…  LLM_API_KEY=…   # OpenAI 兼容任意网关
MODEL_MAIN=…   # 主模型：导演 / Actor / 说书人默认；未配单项时回退到这里
MODEL_CHEAP=…  # 便宜模型：质检 / 审计 / L1 分类默认；未配单项时回退到这里
# 可选单项覆盖：MODEL_DIRECTOR=… MODEL_ACTOR=… MODEL_STORY=… MODEL_QC=… MODEL_AUDIT=… MODEL_CLASSIFY=…
TEMP_QC=0.2   # …（各档温度）
CTX_MEMORY_BUDGET=…   CTX_WINDOW_TURNS=…
DEFAULT_DURATION_FALLBACK_MIN=10
CANDIDATE_TTL_DAYS=7   # candidates/ 中长期未处理候选的清理阈值
AUDIT_ENABLED=true      # v1 为采纳时同步结算；未来改异步需另加写锁/队列
```

---

## 13. 测试策略

| 层 | 内容 |
|---|---|
| 单元 | loader 校验（缺字段/越界/key 重复）；route L0 短路表；movement 裁决（可达/耗时/在场）；claim 冲突判定；access 改判（检索式收权名单正确性）；记忆拼接模板；模板底稿渲染；LLM 网关 JSON 容错（畸形 JSON / 缺字段 / 重试失败 / 文本降级解析） |
| 集成（FakeLLM 录制响应，不联网） | **回合事务**：显式采纳指定候选 = 提交 / 放弃 = 回滚无痕（账本文件 hash 不变）；**多候选并存**：重掷/抽卡新增候选且不删旧版，采纳一份后清理该回合全部候选；**唯一候选自动采纳**：下一次输入自动采纳并进入新回合；**候选暂存恢复**：pending 文件重启后按 turn 分组可见、损坏文件跳过且不影响账本；**信息边界**：Actor 工作单不含他人私密、幕后注不进说书人/质检输入；质检拦域外知识 → 脱敏；改判私密后切片自动变化；混合句一次成稿；审计钩子挂/闭；**审计失败/重启补跑幂等** |
| 冒烟（真 LLM，可选） | `tests/smoke_qinghsi.py`：青石镇剧本关键几步真跑（手动/定时） |
| 前端 | Vitest 纯函数（打字机渲染/事件解析）；UI 人工走查 |
| 内容包校验 | `python -m app.world.loader --check content/<world>` |

---

## 14. 风险与对策

| 风险 | 对策 |
|---|---|
| LLM 角色说出域外知识 | 视图物理隔离（Actor 输入无导演区）+ 质检泄漏比对（参照区只读）+ 泄漏 = 剧情错误 |
| 本地模型结构化输出不稳定 | JSON schema + 重试 + 文本降级解析 + Pydantic 校验 + 失败给用户可见错误并记录原始输出 |
| 正文与时钟/账本不一致 | time_hint 报账进 Δt；质检矛盾分级（小改稿/大挂起） |
| 时间线分叉（重掷留痕） | 回合事务：副作用与正文同包回滚，commit 前账本零写入 |
| 候选区进程崩溃 | 候选未 commit = 未落盘 = 未发生；`candidates/` 持久化，启动后经 `GET candidates/pending` 恢复多份候选列表；正常操作即时清理 |
| 检索式收权不精确 | 近似检索 + 导演勾选裁决 + 审计矛盾兜底（改判当下无需一次求全） |
| 上下文膨胀 | 分片预算 + 模板底稿省 token + 记忆现拼截断 + M8 上下文收敛（装配收敛到当前场景可感知 + 对话对象 + 关键历史状态） |
| 事件流膨胀（远期） | 全内存线性扫可撑几万条；二期 = 语义索引 + compact 预研（对内容包与存档结构零侵入） |
| 审计产物迟到 | v1 采纳时同步结算，无迟到；若未来改异步，必须加写锁/单写者队列，并保留 run_id 幂等补跑 |
| 隐私/安全 | 默认仅 127.0.0.1；AUTH_TOKEN 可选；SSE 不做 CORS 跨源；启动时若 host 非本机且未设 token 则报错/强警告 |

---

## 15. 里程碑排期

| 阶段 | 技术任务 | 验收 |
|---|---|---|
| S0 | uv init + FastAPI hello + Vite 空页 + LLM 网关直连聊天 | 直连对话可跑 |
| S1 | 内容包 loader + 校验；store（JSONL 追加 + 原子写）；账本加载索引；`where_is / present_at / visible_to` | 查询测试过 |
| S2 | **回合主链闭环**：路由 → 导演 → 说书人 → 质检 → SSE 候选区 → 采纳指定候选/重掷/放弃；PendingTurn 事务 | FakeLLM 集成：采纳提交/放弃清理无痕 |
| S3 | 工作单装配与切片（viewer 过滤）；known_by 落库；改判私密（检索式收权）+ 改判公开；叙述预设管线 | 信息边界测试过（Actor 无他人私密） |
| S4 | 时空机制：M17 双轨转正 / M18 位置事实与过期 / M1 推断三件套 / M19 裁决链 + 场景模板底稿 + 混合句 | 移动裁决与模板底稿测试过 |
| S5 | Actor 深抉择派发（隔离回流）；升格通道；导演窗口四功能（含幕后事务/补卡/点名派活） | 窗口全操作走查过 |
| S6 | 提交后结算：记忆附注 / 钩子台账 / 矛盾发现 / 生命周期 retired；M12 主动登门素材；审计失败补跑幂等 | 钩子挂闭 + retired 流程测试过 |
| S7 | 前端体验：候选区多版本比较与操作（重掷保留旧版、选择采纳、唯一候选自动采纳）、导演窗口、事件日志查看器、叙述预设设置、状态面板 | 全 UI 人工走查 |
| S8+ | 内容包《青石镇》完整示例 + loader --check 发布流程；二期预留（关系系统 / 语义索引 / 真流式） | 完整包冒烟 |
