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
│   │   ├── access.py            # known_by 改判：名单直给落账 / 改判公开
│   │   └── goals.py             # M14 剧情目标（save.json 内）
│   ├── rules/                   # 规则段（尽量零 LLM，规则优先）
│   │   ├── route.py             # 输入路由（L0 规则短路 + L1 轻量分类）
│   │   ├── movement.py          # M19 裁决链 / M1 推断 / M18 在场
│   │   ├── scenes.py            # M17 场景注册表 + 模板底稿渲染
│   │   ├── claims.py            # M2 玩家声明覆写 + 冲突判定
│   │   └── axes.py              # ③ 抽象属性轴结算（M5，二期预留）+ 记因留痕
│   ├── workers/                 # 无状态 Agent（每个 = 一个协议函数）
│   │   ├── writer.py            # 编剧（导演+说书人合一：判定+成文）
│   │   ├── actor.py             # NPC Actor（深抉择隔离回流）
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
└── axes.json         # ③ 抽象属性轴声明（题材级，可空数组；二期启用，v1 预留）

# 叙述预设不在世界包内；是全局配置 data/presets.json（导演准则 + 说书人预设）
```

```jsonc
// world.json —— 世界概要：天然公开，每份工作单 system 块常驻、永不裁剪
{
  "id": "qinghsi", "name": "青石镇",
  "summary": ["青石镇靠打渔为生，镇上人家大半识得彼此。",
              "硬规则：与现实世界无异，没有超自然力量、没有异能。"],
  "default_durations": { "move_per_edge_min": 10, "action_default_min": 30 }
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
  "private_note": "",                                  // 幕后注：无人（含本人）知道的作者底牌，仅编剧可读
  "personal_secrets": "他爸在县城欠了赌债，这是最不愿提的事。",  // 本人自知的隐秘 → 进他自己的 Actor 切片
  "has_actor": true,                                // has_actor 档位初值
}

// axes.json —— ③ 轴声明（二期启用；v1 可空/预留，引擎不结算）
[ { "id": "favor", "label": "好感", "tags": ["relation"], "target": "npc_zhuming",
    "range": [-100, 100], "init": 0, "visible": true, "track_cause": true } ]

// data/presets.json —— 全局叙述预设（不在世界包内；2026-09-05 导演准则+说书人预设合并单框）
{
  "writer_guidelines": "不要主动揭穿秘密；优先让 NPC 主动制造冲突；克制写实，白描为主…",
  "banned_words": ["一丝", "不易察觉"]
}
```

### 2.1.1 world.json 新增字段（2026-09-08）

`start_time`（ISO 时间，可选）：该世界的世界钟起点。新建存档与重置存档都回到此时刻；缺省回退引擎默认 `2026-07-14T08:00:00`。`check_world` 校验 ISO 格式。

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
// 唯一事件类型：narrative —— 玩家采纳后的正文成史实（叙述即公开的默认落点）
// 同时也是位置/在场查询的数据源：位置由 narrative.location 推导，谁在场由 participants 推导。
{ "id": "ev_00042", "kind": "narrative", "at": "2026-07-14T14:32",
  "location": "school_gate",
  "participants": ["player", "npc_zhuming"],      // 戏中在场者 = 私密名单的名单原料，也是位置推导依据
  "known_by": null,                                // null=公开（人人可引）；数组=私密切名单
  "body": "校门口围了一小圈人……",                 // 史实正文：一经落库永不修改
  "source": "turn" }
```

- **known_by 是访问控制状态**：正文（body）不可变；`known_by` 是每记录一个可改字段，改判只重设它、不触碰正文（§7）。
- **位置/在场没有独立记录**：`where_is` / `present_at` 直接从统一事件流的 `location + participants` 推导，不维护额外位置表。

**save.json**（引擎运行态 + 元配置 + 实体运行层）：

```jsonc
{
  "meta": { "world_id": "qinghsi", "save_name": "main", "created_at": "…", "next_event_id": 44 },
  "clock": "2026-07-14T14:32",                    // 世界钟：只执行不逐笔记账
  "player_scene": "net_bar",                      // 玩家当前场景 id
  "scene_name": "网吧",                           // 当前场景显示名（注册场景取场景表；
                                                  //   一次性场景取审计中文名，防 UI 显示英文 id）
  "player": {                                     // 主角资料（与 NPC 人物卡同构，无 has_actor）
    "name": "刘星", "appearance": "…", "persona": "…",
    "private_note": "…",                          // 作者底牌：无人知道，仅编剧可读
    "personal_secrets": "…" },                    // 主角自知隐秘：防 NPC 提及（质检禁区）
  "narrative_preset": { "writer_guidelines": "…", "banned_words": [] },  // 全局预设镜像
  "entities": { "npc_zhuming": { "lifecycle": "active" } },  // active | retired
  "axes": { },                                    // ③ 轴当前值（二期；v1 预留不结算）
  "access_overrides": { },                        // 事件访问改判（§7）
  "audit_last_error": null,                       // 最近一次审计失败记录
  "active_lore_ids": ["net_bar_fire"],            // 本轮世界书命中列表（§6 世界书 v2）
  "goals": [ { "id": "goal_01", "text": "查明朱明打架的真相", "kind": "big",
               "subject": "player",               // 归属者：player | npc_id（NPC 目标）
               "status": "active", "big_goal_id": null, "npc_id": "npc_zhuming",
               "created_at": "…", "done_at": null } ]   // M14 剧情目标
}
```

**② 经历 = 装配时现拼，不落盘**：记忆条目 = 引擎把 viewer 可引用的结构化事件按固定模板拼装（时间+地点+谁干了什么+在场者）。LLM 不参与跨轮存储。**改判私密后各 NPC 切片自动跟随**——因为切片是装配时按 known_by 现过滤的，无缓存失同步问题（§7 强调的技术收益）。

---

## 3. 账本读写与查询（ledger/）

- **追加写**：`events.jsonl` 只 append；`store.append_event(rec)` 负责 id 分配（`next_event_id`）与落盘。
- **原子写**：`save.json` 写 `*.tmp` + `Path.replace`；读损坏自动回退 `.bak`。
- **启动加载**：全量读入 events.jsonl 建内存索引（几万条 <10ms，无需数据库）：
  - `by_id`；`by_location`（该地点全部事件）；`by_participant`；`by_kind`。
  - 增量维护：回合采纳 append 后同步更新索引。
- **读接口（查询即真相）**：
  - `where_is(entity)` → 该实体最近一条带 `location + participants` 的统一事件（M18）；
  - `present_at(scene)` → 最近事件中地点为该场景的实体集合（在场者 = 查询不是存储）；
  - `visible_to(viewer)` → 可引用事件集 = 世界概要 ∪ 公开条目 ∪ {私密 \| known_by ∋ viewer} ∪ {本场点名展开的世界书条目}（世界书候选行与展开规则见 §6）；
  - `experiences(entity, viewer)` → ② 记忆条目（规则化拼接，§2.2）。

---

## 4. 回合事务（候选区 / 采纳 / 回滚）

一回合 = 一个事务。正文过质检送达玩家后仍在**候选区**；**玩家采纳（显式或下一次输入自动采纳）那一刻才原子落账**。

```
PendingTurn（内存事务上下文）
├── 候选正文（说书人成品或导演采纳的玩家原文）
├── 副作用暂存：Δt（世界钟推进）· 待落 narrative/events 记录
│               · axes 轴变更（二期预留）· 覆写与有效期 · known_by 初值
└── 关联信息：质检修改记录（issues，随候选呈现；引擎不做矛盾仲裁——剧情一致性归玩家自决）
```

- **候选区呈现**：SSE 交付 `candidate` 事件（正文 + 副作用摘要 + 质检修改记录），一个回合可陆续产生多份候选版本，全部保留供玩家比较。玩家操作：
  - **采纳指定候选** `POST /candidates/{candidate_id}/adopt` → `transaction.commit(candidate_id)`：把选中的候选正文与副作用一次追加/写入，随后执行提交后结算；成功后清理该回合其余候选文件。回合闭环。
  - **下一次输入自动采纳（仅单候选时）**：`POST /turn` 时若该回合只有一份未决候选，先自动执行 `transaction.commit()` 再进入新回合；若有多份候选，引擎不自动选择，玩家必须先指定采纳哪一份。
  - **重掷 / 抽卡** `POST /turns/{turn_id}/reroll` → 生成一份新的候选版本，**旧版本全部保留**，供玩家比较，账本零写入。
  - **放弃** `POST /turns/{turn_id}/discard` → 清理该回合全部候选版本，账本零写入；这是明确放弃整个回合，不是重掷。
  - **先开导演窗口商量**再回候选区定夺（候选事务驻留内存并持久化 `candidates/` 暂存，断线可续）。
- **崩溃安全**：候选期未 commit = 未落盘 = 未发生；只有 commit 后的追加/原子写可能落盘。
- **采纳后反悔** = 走导演窗口（改判 / 覆写），与候选区重掷分轨。
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
    "events": [],
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
  0. 路由 route（§5.1 附表）：导演窗口 → 旁路直答（低 LLM）；其余进主链
     0.5 行内导演指令剥离：`((...))` 片段 → 剥出为本回合写作要求（单独消息递编剧，
         不进路由 / 不进事件日志 player_input / 不进正文；存 candidate.writer_directive，
         重掷沿用）；剥离后的剩余文本才是路由与落账用的玩家输入（纯指令回合 =
         无行动输入，编剧按当前情境推进）
  1. 规则段预结算 rules_presolve（尽量零 LLM，只对 move/jump/claim/mixed）：
     目的地解析 → 可达/耗时Δt候选 → 位置推断候选域 → 在场者查询 → 覆写冲突提示
     → 产出 rule_bundle 作为编剧输入；副作用写入 PendingTurn
  2. 装配编剧工作单（§6：玩家可见 + 账本数据 + rule_bundle）
  3. 编剧一次调用：脑中排节拍 → 直接成文（WriterOutput，§5.3）
  4. 若输出带 actor_questions（深抉择）→ 逐 NPC 装配其隔离工作单（viewer=npc）
     → Actor 决策块回流 → 编剧二次调用按决策成文（正常回合仅此 1 次，深抉择 2 次）
  5. （合并后无独立说书人调用；正文一律由编剧成文）
  6. 质检（必经）：文风（局部改写）/泄漏（参照区比对）/禁用词 → 成品正文 + 修改记录
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
| query | （2026-09-05 已删）状态问句原走旁路直答，因触发子串误伤正常对话且 UI 已覆盖，已并入主链由编剧答 | | |
| ooc | 导演窗口操作 | 旁路（§5.8） | 按需 |

### 5.2 Worker 一览

| Worker | 模型档位 | 温度 | 调用 | 输入 | 输出 |
|---|---|---|---|---|---|
| 编剧（导演+说书人合一） | 主模型 | 0.8 | 每回合 1（深抉择时 2） | 工作单（§6，全知） | WriterOutput（正文+副作用+actor_questions） |
| NPC Actor | 主模型/次档 | 0.8 | 深抉择 +1 | 本人隔离工作单 | 决策块 |
| 质检员 | 辅助模型 | 0.2 | 每回合 1 | 初稿 + 受限参照区（§5.6） | {status, prose, issues} |
| 世界审计 | 辅助模型 | 0.2 | 采纳时提交后结算 | 本回合落账事件 + 相关历史 | 结构性结果 |
| 意图分类 L1 | 最辅助模型 | 0 | 规则不短路时 1 | 玩家输入 + 场景 + 在场 | {route, mention, claim?} |

> **合并决策（2026-09-05 拍板）**：导演与说书人合并为单一"编剧" agent——决策和文字一次调用完成，成本与延迟约减半。全知信息直接接触文字笔，泄漏防线转移到质检参照区（§5.6），Actor 深抉择仍走物理隔离（两段式）。原"动机层纪律"随之简化：编剧的幕后判断是瞬时中间产物，不设独立字段承载。
>
> **模型数量**：v1 默认只需要 **1~2 个模型**：编剧 / Actor 可共用“主模型”，质检 / 审计 / L1 分类可共用“辅助模型”；甚至可以全部指向同一个本地模型。配置里未指定的档位自动回退到默认主模型或默认辅助模型。

### 5.3 编剧（workers/writer.py）

输入 = 玩家原话 + 本回合导演要求（`((...))` 剥离所得，独立消息："必须执行，但不写进正文、不算玩家台词、不进事件日志"）+ rule_bundle + 工作单。一次调用完成**判定 + 成文**（JSON 强制，先脑中排节拍、再直接写正文）：

```jsonc
{ "prose": "正文全文（必填，按叙述预设直接成稿）",
  "summary": "一句话剧情摘要（事件日志用）",
  "actor_questions": [
    { "npc_id": "npc_zhuming",
      "question": "被追问打架的事，朱明是含糊带过还是翻脸？",
      "context": "玩家问朱明昨天为什么打架，朱明想起他爸欠债的由头，好面子、心虚" } ]
}
```

- **编剧只写正文（2026-09-07 拍板）**：`WriterOutput` 仅 `prose / summary / actor_questions` 三字段——时间推进、地点、在场者、私密情境等世界副作用**不再由编剧上报**，一律由采纳时的世界审计从正文语义推断（§5.7）。
- **深抉择不替 NPC 决定**：在场"配 Actor"的 NPC 撞深抉择（内心判断 / 涉密反应 / 是否信任）时，编剧**不得猜其心思**，必须在 `actor_questions` 里上缴问题；引擎派该 NPC 的隔离 Actor 决定后，编剧二次调用按决策成文（§5.4）。档位由玩家直接编辑存档世界实例的人物卡管理、不经导演窗口（2026-09-05 合并：原"点名强制档"取消，浅反应一律编剧代笔）。
- `actor_questions[].context` 只写该 NPC 本人会知道的情境——禁止写幕后注等只有编剧知道的秘密（防止经 Actor 回流泄漏）。
- **知情总纲「角色不是你」（2026-09-09 收敛）**：编剧案头资料（事件日志 / 幕后注 / 世界书）角色本人并不知道；角色开口前须核对话词是否在其自身已知范围内——亲历（事件日志在场者标注）、来历（人物卡）、被告知（剧情内）。由此派生：公开旧事异地角色默认不知（轰动大事可作风闻）、幕后注/私密绝不出口（知情者坦白除外）、无卡即兴角色只知眼前。判断依据 = 事件日志各行标注的发生地/在场者/时间（**数据做重活，规则讲原理**：装配层提供证据，规则只讲一条原理，不 enumerate 特例）。金科玉律级禁令，与"位置是快照"纪律同构（提示词纪律，零新状态；时间锚等机制兜底默认不做）。
- M20 ③"正文写了时间流逝 → 同步推钟"由审计的 `delta_minutes` 落地（编剧不输出 time_hint）；与规则段 Δt 汇总后计入回合。
- **机密纪律**：编剧读幕后注是安全的（全知），但幕后注绝不允许出现在正文里——这是质检参照区比对的反向检查项（§5.6）。

### 5.4 NPC Actor（workers/actor.py）

- 触发：WriterOutput 携带 `actor_questions` 且该 NPC 有 actor 票（卡上 has_actor）。
- **输入 = 物理隔离工作单**：`build_work_order(viewer="actor_<npc_id>")`（§6 装配器实现）只含世界硬规则、场景可感知区、本人人物卡（含补丁）、`experiences(self)`（known_by 已过滤）。**不含**幕后注、其它 NPC 私密、世界书候选、剧情目标、编剧动机。人称已归一（"你/您"→"玩家"，第三人称姓名=自己）。
- 输出决策块（回流给编剧二次调用）：

```jsonc
{ "decision": "含糊带过", "action_hint": "不接话，拉你去打街机",
  "tone": "不耐烦里带点心虚" }
```

### 5.6 质检员（workers/qc.py）——必经前置文字关

- **输入两区**：① 正文初稿（可改）；② **受限知识参照区（只读）**：本次戏中每个开口实体的可引用集摘要（`visible_to(实体)` 提炼）+ 场景可感知 + 本场点名展开的世界书条目。参照区与成品分轨——成品只取初稿与改写，参照区永远不进正文。
- 三关一次完成：
  - **文风一致性**：按叙述预设核对/润色，**只动需改句、其余原样透传**；**禁用词硬拦**（命中即改稿）。
  - **泄漏比对**：某角色台词呈现的知识超出其参照区 → **脱敏改写**（模糊化）。泄漏 = 剧情错误，每个 LLM 都知道它会被查。
  - **秘密禁区**：幕后注与 NPC 自知隐秘（personal_secrets）绝不允许出现在正文或任何角色口中，命中即改稿。
- 输出 `{ status: pass|fixed, prose, issues: [{desc}] }`。
- **可跳过（2026-09-08）**：`Settings.qc_enabled`（系统设置面板复选框 / env `QC_ENABLED`，默认开）。关闭后 `run_turn` 与 `reroll` 跳过质检调用，编剧初稿直接进候选——省一次 LLM 调用与等待，代价是文风/泄漏/禁用词无人把关。
- **引擎不做矛盾仲裁（2026-09-05 拍板）**：剧情走向与既定设定的一致性归玩家自决——质检不设矛盾分级、不挂队列、不提示（玩家看不出矛盾说明其不重要）。
- **预留增强（2026-09-05 记录，暂不实施）**：① 决策忠实性——Actor 决策块进质检参照，编剧二稿不得违背决策；② 秘密禁区——幕后注与 personal_secrets 作为"绝不可进正文"清单交质检比对。
- 质检无幕后注输入（防质检自身泄漏与代答）——秘密禁区原文例外：仅作比对参照，与成品分轨。

### 5.7 世界审计（workers/auditor.py）——采纳时提交后结算

v1 的审计/记账在玩家“采纳”（含下一次输入自动采纳）时，于同一请求内完成，产物进账本影响后续轮。所有账本写入都收敛到采纳这一个串行点，避免后台协程与下一回合并发写 `events.jsonl` / `save.json`。
1. **目标完成判定（M14）**：按已采纳正文判活动目标是否达成（小目标=当事达成；大目标=关键真相/冲突解决；只推进未达成不填）。
2. **关系结算复核**：M5 关系系统暂缓设计，v1 不执行关系轴结算；此处只保留接口位，待二期实现。
3. **生命周期扫描（〇章）**：终态事件（死亡/永久离开）→ 实体置 `retired`（数据全保留；目标留玩家定夺）。

**失败/恢复（2026-09-08 与实现对齐）**：审计在采纳时同步执行；若失败，正文照常落账不回滚，错误记入 `save.json.audit_last_error`（不再静默），当轮副作用不补跑。当前同步模式下每次采纳只跑一次审计，无补跑场景；**若将来改成后台异步**，必须引入 run_id + 幂等补跑 + 写锁/单写者队列。

### 5.8 导演窗口（OOC 旁路，routes_director.py）

仅玩家主动开启，导演纯被动；窗口内一切是戏外话（玩家说了 ≠ 主角说了，导演的话 ≠ 正文）。四类请求：

| 请求 | 实现 | 账本 |
|---|---|---|
| 求建议 | 扫剧情目标 + 当前张力 → 2~3 条建议（收尾型 + 推动型） | 只读 |
| 答疑 | 查账本简略答（档案/事件/目标） | 只读 |
| 剧情讨论 | 多轮磋商 → 产出简略输入建议 → 玩家复制进正文框 | 只读 |
| 幕后事务 | 静默覆写 / 记忆注入 / 事件访问改判（§7）/ 剧情目标 set_goal（设立·废弃） | **玩家确认后落账** |

- 幕后事务与候选区同闸门：玩家提出 → 确认 → 落账。静默覆写 = 写一条统一 narrative 事件（如“朱明在网吧。”，source=director）+ 给该时段排戏，正文以"已发生"为基演。
- **人物卡 / Actor 档位 / 转正 / 场景注册（2026-09-07 改判）**：不再走导演窗口，改由**世界工作台直接编辑**存档的世界实例（设计 C：人物卡人格/幕后注/自知隐秘、has_actor 勾选、添加人物、添加场景）。导演窗口只保留事件流写操作（覆写/注入记忆/改判）与剧情目标。
- 观察/打量等描写请求在**正文窗**输入，走正常正文流水线，不进窗口。

---

## 6. 上下文装配与切片（工作单）

核心函数 `build_work_order(viewer, scope)`——按 viewer 身份现配"本轮工作单"，小且固定：

```
viewer ∈ { writer, npc_<id> }        # 编剧=决策+成文合一（全知）；Actor 走隔离工作单
scope  = 当前场景可感知 ∪ 在场实体 ∪ 对话对象 ∪ 关键历史状态（M8 上下文收敛）
```

| 块 | writer（编剧） | npc_<id>（Actor） |
|---|---|---|
| 世界概要（常驻 system 块） | ✅ | ✅ |
| 世界书 · 候选行（id+摘要） | ✅（按此设定写） | ❌ |
| 世界书 · 点名展开条目正文 | ✅ | ❌ |
| 公开事件条目（现拼记忆条目，含玩家输入） | ✅ | ✅ |
| 私密条目 known_by∋viewer | ✅ | ✅（仅含自己的） |
| 幕后注 / 注入记忆（仅决策者可读） | ✅ | ❌ |
| 剧情目标 / 节奏档 | ✅ | ❌ |
| 本人 persona / 经历切片 | ✅ | ✅ |
| 场景模板底稿 / 可感知 | ✅ | ✅（可感知原则） |

- **世界书触发机制 v2**（2026-09-07，零 LLM 关键词匹配）：条目 = `{id, keywords, body}`（无 summary 摘要层）。`save.active_lore_ids` 由引擎两处维护——预结算阶段玩家输入命中关键词追加（上轮事实优先、上限 5 条去重）；采纳阶段清空后按已采纳正文重建。工作单只读该列表，按 id 取条目 **body 全文**拼入编剧/导演工作单。世界书无 known_by、天然公开；命中与内容不进记忆流（世界书只读静态，不落账）。
- **Token 预算**（分片可配）：system 块（常驻：世界概要全量 + 角色纪律 + 数值/输出纪律）→ 场景块 → 角色块（在场 persona 精简）→ 记忆块（现拼条目按 viewer 过滤 + budget 截断，超出丢最旧）→ 玩家最近窗。裁剪顺序固定：先砍记忆低相关 → 再压玩家窗——**世界概要永不参与裁剪**（硬规则常驻，失守即世界观崩坏）。
- **记忆条目拼接模板**（零 LLM）：`{时间} {地点}，{谁} {干了什么}，在场：{…}`。
- **事件日志行标注发生地与在场者**（2026-09-09）：写手事件日志每行附 `（发生地 · 在场者：…）`（id 转中文名，玩家用主角名；发生地取场景表名，一次性场景回退 location_name）——为「角色不是你」知情总纲提供判断依据，含**异地不知**（别处城镇的公开旧事本地角色默认不知，§5.5）。
- **规则化拼接的意义**：原料只取已记账字段——每轮概貌稳定、成本确定、不产生正文二次概括的幻觉漂移。
- **装配实现（2026-09-05 定稿：`app/core/workorder.py`）**：工作单 = 客观层（账本快照，纯函数块）+ 主观层（角色契约：身份/金科玉律/输出格式）两部分，`build_work_order(viewer, …)` 装配。拼接顺序按三条原则：**前置原则**（身份与硬规则、金科玉律禁忌在最前——模型开头注意力最强，且合一 agent 的泄漏禁令必须置于高注意区）、**动态最近原则**（事件日志 → 场景快照 → 在场近况等每回合变化的块集中在尾部，离玩家输入更近，防止"把旧事当新事"）、**近因原则**（输出 JSON 格式说明收尾，紧贴玩家输入消息；编剧准则是"怎么写"的规则，贴近动笔位置）。可裁块（近况/世界书命中）集中于尾部，token 预算超支时最先可裁；身份/硬规则/金科玉律不可裁。viewer 参数为未来 actor_<npc_id>（物理隔离）与 qc（参照子集）预留。

---

## 7. 秘密与访问控制实现（ledger/access.py）

### 7.1 known_by 落库

- 事件记录落库时，导演在剧本指令中声明私密性（`beat.private` 或 narrative 记录级 `known_by` 初值）：公开情境 → `known_by: null`（默认态，人人可引）；私下情境 → `known_by = 当场在场者`（取该场戏 participants）。不为保密增设字段或场景属性。
- 注入记忆（M16 涉密补全）= 落私密记录 `known_by = [被注入者]`；导演静默覆写写统一 narrative 事件，不额外承载知情语义。
- 装配时的可引用集 = `visible_to(viewer)`——私密名单外的实体查不到这条（§3/§6）。

### 7.2 改判私密 · 玩家纠错（公开 → 私密）——名单直给

正文不动，只重设 `known_by`。**名单由导演/玩家直接给定落账，引擎不做检索起草**（2026-09-09 翻案：改判是补救行为，事件公开期间可能在剧情上传给无数人，逻辑上不可能全部收回——玩家选择改判即接受"已传开者收不回"的逻辑代价，以更大的叙事理由压过它）。落账 = `apply_access_override(event_id, known_by)`，只改访问层；运行期发现个别 NPC 应知情而名单未含，导演补名单或圆场即可（改判粒度单条、可反复修正）。

### 7.3 改判公开 / 官宣

- **改判公开 · 玩家纠错（私密 → 公开）**：清空 `known_by` 恢复公开（不产正文事件、不改旧条目）。
- **官宣不靠改判靠叙述**：导演排一场公开戏落新 narrative（公开情境）→ 全镇可引"他们在一起"；历史私密条目保持私密（翻旧账需逐条点名走上方改判公开）。

技术收益：因为切片是装配时现拼现过滤，known_by 一旦重设，所有 NPC 的记忆切片下一轮自动跟随——**访问状态修正不产生任何缓存失同步**。

---

## 8. 时钟与空间

### 8.1 世界钟（core/clock.py）

- 变量：save.json `clock`（datetime），只执行不记账。**起点** = `world.json.start_time`（内容包资产，`world_start_time()` 统一读取；新建存档与重置都回到该值）。
- 推进来源三层（规则直落，无专门 agent）：
  ① 玩家声明时刻（"等到中午"/"第二天"）→ 认字直接推进 Δt；
  ② 干实事未提时间 → `world.json.default_durations` 取默认耗时，缺失用引擎兜底常量；
  ③ 正文写了时间流逝 → 采纳时审计从正文推断 `delta_minutes` 推 Δt（§5.7；编剧不输出 time_hint）。
- 对话回合时钟冻结（纯闲聊 Δt=0）；回合 Δt 汇总在 PendingTurn，commit 才落盘。
- 离线 = 存档搁置，时钟停留在最后推进时刻，重开从该刻续走。

### 8.2 M17 场景双轨（rules/scenes.py）

- 预置 = 内容包 `scenes.json` 节点（名字/别名/标签/可感知区/开放时段/邻接点）。别名表与邻接图在启动时建索引。
- 现场补建（转正）：走进包外地点 → 引擎把节点写入存档世界实例 `world/scenes.json`（设计 C），之后可复用可被提及；一次性布景（register_scene=false）只记事件条目的 `location_name` 显示名、不进导航集。初态由来源定：玩家声明/审计判定可复用 → 当场注册；导演排戏默认临时；编剧写景顺笔 → 装饰性文字不入图。
- 转正信号（引擎被动核对）：玩家回访/指代（指代消解依赖已注册）→ 剧情目标指向 → 导演/NPC 复用；任一出现即补注册。导演可预注册。
- 一次性布景：剧情用完即从可导航集退场（事件流仍可回溯）。

### 8.3 M18 位置与在场者（ledger/queries.py）

- "X 此刻在哪" = `where_is(X)`：该实体最近一条带 `location + participants` 的统一事件。
- "谁在当前场景" = `present_at(scene)`：在场 = 查询不是存储。
- **空档位置查询三件套**：过期前最后事件 + 人格隐含大框先验 + 时间差 → 引擎合成候选域 → 导演做叙事落点 → 玩家进门时在场结算对导演落点执行（导演落点本身落一条新事件）。

### 8.4 M1 NPC 位置推断（rules/movement.py）

- 输入：最后事实与覆写（账本）/ 地点标签与开放时段（场景表）。（2026-09-05：正常日程字段已移除）
- 输出候选域（"暑假早晨朱明大概率在网吧"），非唯一答案；导演按叙事拍板。
- 最后事实缺失时退化为"人格隐含大框"（人格格内身份定位资料带出的日常场所进入候选）。

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
- 模型档位表（config.py + .env 覆盖）：编剧 / Actor / 质检 / 审计 / L1 分类五档，各自 model + temperature（§5.2）；未配置档位回退到默认主模型 / 默认辅助模型，通常 1~2 个模型即可。
- **v1 无向量检索**：召回/切片全部结构化查询 + 线性扫（几万条内 <10ms）。二期升级 = 事件流叠语义索引（embedding 落盘、启动重建），对内容包与存档结构零侵入；中文预留 bge 系接口。

---

## 10. API 设计

### 10.1 REST

```
GET    /api/worlds                          # 可用内容包列表
POST   /api/sessions                        # 新建存档 {world_id, save_name}
GET    /api/sessions/{sid}                  # 会话详情（时钟/场景/在场/预设/目标）
GET    /api/sessions/{sid}/state            # 右栏面板：时钟/场景/在场/可见轴（二期）/目标列表
GET    /api/sessions/{sid}/ledger/events?cursor=   # 事件日志（玩家视角全量叙述 + 访问状态标注，只读）
GET    /api/sessions/{sid}/world            # 世界工作台（概览/世界书/场景/NPC/轴/事件）
PUT    /api/sessions/{sid}/world            # 编辑存档的世界实例（设计 C）
POST   /api/sessions/{sid}/world/save-as    # 另存为新世界资产包
GET    /api/sessions/{sid}/world/export     # 导出资产包（仅世界资产）
GET    /api/sessions/{sid}/export           # 导出存档（save.json + events.jsonl + world/，2026-09-08）
POST   /api/saves/import                    # 导入存档（同名自动改名、缺世界则建档，2026-09-08）
GET/PUT /api/settings                        # 系统设置（含 qc_enabled 质检开关）
GET/PUT /api/presets                 # 全局预设（编剧准则 + 禁用词）
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
  {"topic": "confirm", "action": {"type": "override|inject_memory|access_rejudge|set_goal",
   "payload": {…}}}                   → 玩家确认后落账（两段式确认）
```

---

## 11. 前端（web/）

Vite + TS + 原生 DOM 轻量组件：

| 组件 | 职责 |
|---|---|
| `ChatView` | 正文流 + 打字机（SSE candidate 的 prose 播放）；**候选区版本列表**：展示/比较多份候选，可单独查看、选择采纳 / 重掷 / 放弃；提示“唯一候选时直接继续输入 = 采纳该候选” |
| `DirectorPanel` | OOC 窗口：求建议 / 答疑 / 剧情讨论 / 幕后事务（含改判、补卡、点名派活表单） |
| `SceneCard` | 当前场景 + 在场 NPC（模板底稿直出；首达/剧情舞台时展示说书人成品） |
| `StatePanel` | 时钟 / 在场 / 可见轴（二期）/ 目标列表；candidate 采纳后刷新 |
| `LedgerView` | 事件日志时间线（只读 + 公开/私密/正文分层展示，玩家可点名某条发起改判） |
| `SettingsPanel` | 全局预设（导演准则 + 说书人预设）+ 系统设置（API/模型/字体/主题） |

---

## 12. 配置（config.py + .env）

```
AIWORLD_HOST=127.0.0.1
AIWORLD_PORT=8765
AUTH_TOKEN=                     # 空 = 仅本机；非空 = 要求 X-Auth-Token
REQUIRE_AUTH_FOR_NON_LOCAL=true # host 非 127.0.0.1/::1 且 AUTH_TOKEN 为空时拒绝启动
LLM_BASE_URL=…  LLM_API_KEY=…   # OpenAI 兼容任意网关
MODEL_MAIN=…   # 主模型：导演 / Actor / 说书人默认；未配单项时回退到这里
MODEL_CHEAP=…  # 辅助模型：质检 / 审计 / L1 分类默认；未配单项时回退到这里
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
| 单元 | loader 校验（缺字段/越界/key 重复）；route L0 短路表；movement 裁决（可达/耗时/在场）；claim 冲突判定；access 改判（名单直给落账 + by_knower 索引同步）；记忆拼接模板；模板底稿渲染；LLM 网关 JSON 容错（畸形 JSON / 缺字段 / 重试失败 / 文本降级解析） |
| 集成（FakeLLM 录制响应，不联网） | **回合事务**：显式采纳指定候选 = 提交 / 放弃 = 回滚无痕（账本文件 hash 不变）；**多候选并存**：重掷/抽卡新增候选且不删旧版，采纳一份后清理该回合全部候选；**唯一候选自动采纳**：下一次输入自动采纳并进入新回合；**候选暂存恢复**：pending 文件重启后按 turn 分组可见、损坏文件跳过且不影响账本；**信息边界**：Actor 工作单不含他人私密、幕后注不进说书人/质检输入；质检拦域外知识 → 脱敏；改判私密后切片自动变化；混合句一次成稿；审计目标完成判定；**审计失败记 audit_last_error（同步模式，无补跑场景）** |
| 冒烟（真 LLM，可选） | `tests/smoke_qinghsi.py`：青石镇剧本关键几步真跑（手动/定时） |
| 前端 | Vitest 纯函数（打字机渲染/事件解析）；UI 人工走查 |
| 内容包校验 | `python -m app.world.loader --check content/<world>` |

---

## 14. 风险与对策

| 风险 | 对策 |
|---|---|
| LLM 角色说出域外知识 | 视图物理隔离（Actor 输入无导演区）+ 质检泄漏比对（参照区只读）+ 泄漏 = 剧情错误 |
| 本地模型结构化输出不稳定 | JSON schema + 重试 + 文本降级解析 + Pydantic 校验 + 失败给用户可见错误并记录原始输出 |
| 正文与时钟/账本不一致 | 审计从正文推断 Δt（规则段 Δt 与审计 delta_minutes 汇总为回合 Δt）；正文与时钟的叙事落差留玩家自决 |
| 时间线分叉（重掷留痕） | 回合事务：副作用与正文同包回滚，commit 前账本零写入 |
| 候选区进程崩溃 | 候选未 commit = 未落盘 = 未发生；`candidates/` 持久化，启动后经 `GET candidates/pending` 恢复多份候选列表；正常操作即时清理 |
| 改判名单不全（已传开者漏收） | 设计口径即接受：改判是补救行为、名单直给，已传开者收不回是玩家明知并接受的逻辑代价；运行期导演可补名单或圆场 |
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
| S2 | **回合主链闭环**：路由 → 编剧（判定+成文合一）→ 质检 → SSE 候选区 → 采纳指定候选/重掷/放弃；PendingTurn 事务 | FakeLLM 集成：采纳提交/放弃清理无痕 |
| S3 | 工作单装配与切片（viewer 过滤）；known_by 落库；改判私密（名单直给）+ 改判公开；叙述预设管线 | 信息边界测试过（Actor 无他人私密） |
| S4 | 时空机制：M17 双轨转正 / M18 位置推导与过期 / M1 推断三件套 / M19 裁决链 + 场景模板底稿 + 混合句 | 移动裁决与模板底稿测试过 |
| S5 | Actor 深抉择派发（隔离回流）；升格通道；导演窗口四功能（含幕后事务/补卡/点名派活） | 窗口全操作走查过 |
| S6 | 提交后结算：目标完成判定 / 生命周期 retired；M12 主动登门素材；审计失败记录 audit_last_error | 目标判定 + retired 流程测试过 |
| S7 | 前端体验：候选区多版本比较与操作（重掷保留旧版、选择采纳、唯一候选自动采纳）、导演窗口、事件日志查看器、叙述预设设置、状态面板 | 全 UI 人工走查 |
| S8+ | 内容包《青石镇》完整示例 + loader --check 发布流程；二期预留（关系系统 / 语义索引 / 真流式） | 完整包冒烟 |
