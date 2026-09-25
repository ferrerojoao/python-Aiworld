# 方案 · SillyTavern 卡导入（世界工作台「导入为新世界」）

> **状态**：✅ **已落地**（2026-09-25）。实施记录、踩坑与变异测试见 §8。
> **背景**：世界工作台已有「导入为新世界」，但只吃 **AIWorld 自己的 zip 资产包**。
> 现在要能直接吃 **SillyTavern（ST）的卡**：拖一张 PNG / JSON 进来，变成一个能玩的新世界。
> **性质**：🔴 这是 AIWorld 冻结（2026-09-24）后的**唯一一处定点破例**。理由见 §1。
> **依据**：三张真卡实测（`C:\AI\_st_cards\`，2026-09-25），数据见附录 A。**没有一条是猜的。**

---

## 0. 一句话

**ST 卡是一份"给模型看的设定文本"，AIWorld 世界包是一份"结构化状态"** —— 本方案只做一件事：
把前者翻译成后者，**翻译不了的明确丢弃并写进报告**，绝不假装翻译成功。

四条已拍板的取舍（2026-09-25 用户定）：

| # | 问题 | 定论 |
|---|---|---|
| 1 | 三类卡格式无法可靠区分 | **玩家在表单里选类型**；嗅探只摆证据，不预选 |
| 2 | 场景卡里的 NPC 条目 | **列表单列候选、默认全勾** → 勾上的落成 NPC 卡 |
| 3 | `first_mes` 可能是说明页 | **表单里下拉选开局**（`first_mes` + 各条 `alternate_greetings`） |
| 4 | 常驻条目可能 19k 字/轮 | **全导 + 报告把代价算清**，不替玩家砍 |

---

## 1. 为什么可以在 AIWorld 里做（破例的边界）

导入器是**纯新增的「入口适配层」**：

- ✅ 只产出 `overview/lorebook/scenes/npcs` 这个**既有形状**，落盘复用 `save_world_assets`（**唯一写路径**）；
- ✅ 不碰事件模型 / 审计 / 时间结算 / 提示词 / 状态；
- ✅ 不新增任何运行时概念，导入完就是一个普通世界；
- ❌ 不做反向导出（AIWorld → ST）。

⇒ 破例仅限此功能。其余新特性照旧去 `aiworld-alive`。

---

## 2. 三种输入形态（实测）

| 形态 | 判据 | 取值 |
|---|---|---|
| **PNG 卡** | 文件头 `\x89PNG` | tEXt/iTXt 块 **`ccv3` 优先 → 回退 `chara`**；值是 **base64 的 JSON** |
| **JSON 卡** | 顶层有 `spec`/`data`，或 V1 扁平字段 | 直接 `json.loads` |
| **JSON 世界书** | 顶层有 `entries`（**dict 键=uid** 或 list） | 直接 `json.loads` |

- V2/V3 都包一层 `{"spec","spec_version","data":{…}}`；**V1 是扁平 6 字段**（无 spec）。
- ⚠️ **实测到真实用例**：人物卡只有 `chara` 块（V2），另两张 `chara` + `ccv3`（V3）⇒ 顺序不能反。
- ⚠️ V3 卡的**顶层还留着一份 V1 扁平字段** ⇒ 解析一律**优先 `data`**，没有 `data` 才用顶层。

**不做**（写在这里防复活）：WebP 卡 · CharX(zip) · YAML · BYAF · 从 URL 下载卡。

---

## 3. 三类卡 → 三种落点（**玩家在表单里选**）

为什么不让代码判：实测显示 `description` / `system_prompt` / `personality` / `scenario` **在人物卡、场景卡、世界卡上都可能全空**，
唯一显式信号是人物卡 `description` 开头的 `Type: character`——**样本太小，不能当判据**。判错类型的代价是"把人名当地名"，整局正文全错。

| 类型 | `npcs` | `scenes` | `lorebook` | `opening` |
|---|---|---|---|---|
| **角色卡** | 卡 → **1 张 NPC**（`persona` = `description` + `personality`）+ 主角占位卡 | 1 个占位场景 | `character_book` 全量 | 玩家选的那段 |
| **场景卡** | 主角占位卡 + **勾选的候选条目 → NPC 卡** | **卡名 → 1 个 `Scene`**（`perceivable` = `description` + `scenario`） | `character_book` 全量 **减去**已转人的条目 | 玩家选的那段 |
| **世界卡** | 主角占位卡 + 勾选的候选条目 → NPC 卡 | 1 个占位场景 | `entries` 全量 **减去**已转人的条目 | 玩家选的那段 |

**共用规则**：

- **主角占位卡**：AIWorld 引擎保证「人物表恰好一张 `is_player`」。表单里填**主角名**（默认 `旅人`，可改）。
  ⚠️ **不能用 `PLAYER_PLACEHOLDER`（"主角"）**——`check_world` 会立刻报"还是占位名"，导入完就带一条待修。
- **占位场景名** = `DEFAULT_START_SCENE`（`起点`），复用 `app/world/draft.py` 的声明处，不另起一个名字。
- **世界 id**：`content/` 下的目录名，必须过 `_WORLD_ID_RE = ^[A-Za-z0-9_-]{1,64}$`（`routes_sessions.py:37`）。
  默认 = 卡名里能留下的 ASCII；留不下就 `st-<8 位 sha1>`。**表单可改**。
- **世界名**（`world.json.name`，不是目录名）默认 = 卡名，**可以含中文**。
- `start_time` 一律留空（引擎默认起点）。ST 没有世界钟概念。

### 3.1 「候选人物条目」的判据（零模型调用）

> 🔴 这是本方案最省事的一处发现：**场景卡/世界卡里的 NPC 本来就是结构化条目，不是散文。**

判据 = **`constant == False` 且 `keys` 非空**。

三张真卡全部命中、零误报（反面例子：世界卡里有一条 `绿皮状态栏` 是 `constant=True` 且有 keys，被正确排除）：

| 卡 | 命中数 | 实际是什么 |
|---|---|---|
| 场景卡（圣花学园） | **26** | 26 个学生，一条一个人 |
| 世界卡（兽人模拟器） | 5 | 5 个具名角色 |
| 人物卡（高岭爱花） | 2 | 2 个配角 |

- **人物名 = `keys[0]`**（实测三张卡都是人名：`慕容清寒` / `奥菲莉亚` / `青叶陆`）。
  撞名（与主角或已落卡的人重名）时取 `keys` 里第一个不撞的；全撞则跳过并进报告。
- **`persona` = 该条目的 `content`**；`appearance` 留空（ST 不区分外貌/人格，不硬猜）。
- 表单只给 **checkbox**（名字不在此处编辑）——工作台是既有的改名处（"一份概念一个声明处"）。
- 🔴 **勾选转成人物卡的条目，从世界书里移除**，否则同一段设定注入两遍（token ×2，还可能自相矛盾）。
  ⚠️ **代价要写明**：人物卡的 `persona` 只在**该角色在场**时注入，而世界书条目是"关键词命中就注入（不管人在不在场）"。
  ⇒ 报告里要提示：*"如需他在不在场都能被提到，请在工作台另加一条世界书条目。"*

---

## 4. 字段映射表

### 4.1 卡级字段

| ST 字段 | 落点 |
|---|---|
| `name`（`data.name`） | 角色卡 → NPC 的 `id`；场景卡 → `Scene.id`；世界名默认值 |
| `description` + `personality` | 角色卡 → NPC.`persona`（两段拼接）；场景卡 → `Scene.perceivable`；世界卡 → `summary` |
| `scenario` | 角色卡 → `summary[0]`；场景卡 → 拼进 `perceivable`；世界卡 → `summary` |
| `first_mes` / `alternate_greetings[]` | **表单里由玩家选一段** → `overview.opening` |
| `character_book` | → `lorebook`（见 4.2）；其中"候选人物条目"按 §3.1 处理 |
| *其余全部* | **丢弃**，见 §5 |

### 4.2 世界书条目字段

| ST 字段 | AIWorld | 说明 |
|---|---|---|
| `keys`（卡内书本） / `key`（独立世界书） | `keywords` | 🔴 **两个名字不能混**：写错 = 条目永远不触发 |
| `content` | `body` | |
| `constant` | `always_on` | 两者语义一致：每轮必注入 |
| `comment` / `name` | `id` | 都空就用 `条目N`；**同 id 自动加后缀去重** |
| `enabled`（卡内书本，正向） / `disable`（独立世界书，**反向**） | — | 🔴 反向布尔！当 `enabled` 读 = **条目全部静默消失** |
| `order` / `insertion_order` | — | 只用于**排序**（降序，重要的在前，见下） |
| 其余 | **丢弃** | 见 §5 |

**排序**：按 `order` **降序**（稳定排序，同值保持原顺序）。
理由：AIWorld 的关键词命中上限是**取列表前 5 条**（`LORE_ACTIVE_CAP = 5`，`hit_entry_ids` 按列表顺序截断）⇒
"重要的往上放"是既有纪律，而 ST 的 `order` 越大越靠近提示词末尾、越有分量 ⇒ 降序对齐。

**无 `subject`（归属角色）**：ST 没有对应概念，猜哪 2 条是"核心设定"没有依据，且猜错是**静默失效**。
留空时行为 = 纯关键词触发，**不减功能**（ST 也只有"关键词"和"常驻"两种触发）。玩家想用再在工作台填。

**安全闸门**（只这三条，都不砍内容）：

1. `enabled == False` → 跳过（报告计数）。
2. `content` 为空 → 跳过（报告计数）。
3. **既非常驻、又无关键词** → 跳过（在 AIWorld 里不可能生效，`check_world` 会直接报待修）。
   ⚠️ 常驻条目**允许无关键词**（引擎侧合法）。

---

## 5. 丢弃清单（**作废记录，别静默**）

🔴 这一节的作用是：以后有人问"为什么 ST 的 XX 没导进来"，能直接查到答案，而不是重新提一遍。

| 丢弃的 ST 字段 | 为什么 |
|---|---|
| `system_prompt` · `post_history_instructions` | 🔴 **会盖掉编剧契约**（AIWorld 的提示词是分级装配的，外来 system 指令会顶掉一级硬边界）。**最危险的一对，必须丢。** |
| `mes_example` | 例对话是"教模型说话"的，AIWorld 的文风走 `presets.json`（三级偏好）——两处会打架 |
| `creator_notes` · `creator` · `character_version` · `create_date` · `avatar` | 作者元数据，不进提示词 |
| `tags` · `talkativeness` · `fav` | 平台发现/UI 元数据 |
| `extensions` | 平台扩展（实测含 `world`= 外部世界书文件名、`regex_scripts`、`depth_prompt`）。**外部世界书不跟随 PNG**，只导内嵌的 `character_book` |
| `group_only_greetings` | 群聊专用 |
| 条目 `secondary_keys` / `keysecondary` | 🔴 ST 是 **AND** 条件，AIWorld 只有 OR ⇒ 丢弃会**放宽触发**（更容易命中）。实测三张卡 `secondary_keys` 全空，所以本样本无实际影响；**一般情况必须进报告** |
| 条目 `position` / `depth` / `role` | 注入位置（`before_char`/`after_char`）。AIWorld 的装配顺序是固定的（`build_work_order`） |
| 条目 `priority` · `probability` · `useProbability` | 概率触发 / 组内权重，AIWorld 没有 |
| 条目 `selective` · `selectiveLogic` · `group*` · `scanDepth` · `matchWholeWords` · `caseSensitive` | 匹配引擎细节 |
| 条目 `sticky` · `cooldown` · `delay` · `excludeRecursion` · `preventRecursion` | 时间/递归行为，AIWorld 没有 |
| `character_book` 外层 `scan_depth` · `token_budget` · `recursive_scanning` | 书本级扫描参数 |
| `use_regex` | 🔴 **不丢字段，但丢语义**：ST 的 `keys` 可能是**正则**。AIWorld 只做**子串包含** ⇒ 当子串用（多数情况等价），**含正则元字符的 key 必须进报告**（实测世界卡 17/17 条 `use_regex=True`） |

**AIWorld 侧刻意不做的事**（对照 §2.5 的纪律）：

- ❌ 不用正则从 `keys` 推时间/条件（AIWorld 踩过"正则假阳越补越多"的坑）
- ❌ 不调 LLM 从散文里抽人物（条目的判据已经够了，多一次调用只增加不确定性）
- ❌ 不自动写 `npcs/`（**资产一律玩家落** —— 表单勾选就是"玩家落"）

---

## 6. 导入报告（**必须给玩家看**）

导入不是"静默成功"。返回一个 `report`，前端**逐条显示**：

| 项 | 内容 |
|---|---|
| 识别 | 形态（PNG `ccv3` / JSON V3 …）· 卡名 · 类型（玩家选的） |
| **常驻代价** | 🔴 **`N 条常驻条目，共 X 字，每一轮都会进提示词`** —— 实测世界卡是 **19430 字**。这是玩家最需要知道的一个数 |
| 世界书 | 总数 / 收录 / 跳过（分三类计数：禁用 / 空正文 / 无关键词且非常驻）。🔴 **给玩家看的"进新世界几条"是转人后的 `in_lorebook`**，不是 `kept`——两者相差被转成人物卡的那些（实测场景卡：27 → 1） |
| 正则 key | 含正则元字符的 key 清单（提示"被当普通词处理了"） |
| 未知宏 | `{{…}}` 里没被替换的（不是 `char`/`user`/`original`），提示玩家去开局正文里清 |
| 丢弃字段 | §5 里**这张卡实际存在**的那些（不是全表照抄） |
| 待修 | `check_assets` 的软问题清单 |

**宏替换**：`{{char}}` / `{{original}}` → 卡名；`{{user}}` → 主角名；`<START>` 去掉。
**其余宏一律留着并进报告**——不猜、不删（删了可能吃掉正文）。

---

## 7. 端点与前端

| 端点 | 作用 |
|---|---|
| `POST /api/worlds/inspect-card` | **纯读，不写盘**。收 `{filename, content(base64)}` → 返回 `{format, spec, name, evidence, lore 计数, 候选人物清单, 开局候选清单}` |
| `POST /api/worlds/import-card` | 真导入。收 `{filename, content, kind, world_id, world_name, player_name, scene_name, opening_index, npcs_from_entries}` → 写 `content/<world_id>/` |

> 两个端点都**不挂 sid**：导入新世界不该要求"已有一个世界"（`/worlds` 前缀下与既有 `POST /worlds/new`、`/worlds/draft` 一致）。

**前端流程**（世界工作台 →「导入为新世界」）：

```
选文件 → 若 .zip 走既有 importWorld()            （老路径不动）
        否则 → inspect-card → 就地展开表单：
            识别结果 + 证据
            卡的类型（角色卡 / 场景卡 / 世界卡）
            世界 id · 世界名 · 主角名
            开局用哪一段（下拉：first_mes + alternate_greetings，各显示前 40 字）
            候选人物（checkbox 列表，默认全勾）
            [导入] [取消]
        → import-card → 显示报告 → 确认切过去
```

`accept` 从 `.zip` 改成 **`.zip,.png,.json`**。

---

## 8. 实施记录（2026-09-25 ✅ 已落地）

**代码**

| 文件 | 内容 |
|---|---|
| `app/world/sillytavern.py`（新，~350 行） | 解析 + 映射，**不碰磁盘** ⇒ 能拿真卡直接跑单测 |
| `app/api/routes_sessions.py` | `InspectCardBody` / `ImportCardBody` / `_decode_card_upload` + 两个端点 |
| `web/dist/app.js` | `importWorld()` 分流 + `importSillyTavernCard` / `renderCardImportForm` / `bindCardImportEvents` / `submitCardImport` / `formatImportReport` |
| `web/dist/style.css` | `.ci-*` 一节（候选列表自带滚动：26 个学生也不会把面板顶出屏幕） |
| `tests/test_sillytavern.py`（新，71 条） | 卡全部**当场构造**（最小 PNG + tEXt/zTXt/iTXt），不依赖 `C:\AI\_st_cards\` 的真卡 |
| `web/tests/smoke.test.mjs` | 3 条：非 `.zip` 分流 / 类型不预选 / 勾选与改名按条目 index 走 |

**闸门**：`ruff` 全过 · `pytest` **429 passed** · 覆盖率 **92.71%**（`sillytavern.py` 100%）· `node --test` **41 passed** · `bump_frontend_version.py` 已刷（`app.js?v=46c92e82`）。

**真卡端到端**（三张真卡 → 真实端点 → `save_world_assets` → `check_world`）：全部 `problems == []`，`/api/worlds` 报 `ok: true`。

### 8.1 实现时踩到的坑（都是实测，不是推演）

1. 🔴 **压缩的 iTXt 读不到**。原写法用 `body.split(b"\x00", 5)` + `len(parts) == 6` 定位字段，而 **`compression_flag` 字节在未压缩时本身就是 `\x00`** ⇒ 段数随内容变化，`flag=1` 时只有 5 段、静默读不到。改成逐字段 `partition`。
   > 三张真卡只有 `tEXt`（实测），所以这条**在真卡上永远测不出来**——是构造字节测出来的。
2. 🔴 **候选人名不能取 `keys[0]`**。实测 `keys=['活泼运动','夏晴天']` 取到的是**形容**，真正的名字在 `comment` 冒号后面（`'活泼运动型：夏晴天'`）。
3. 🔴 **`comment` 里的括号注释会污染**。`'大和抚子型：樱井千代 (中文名：千代)'` 不先剥括号，按"最后一个冒号后"会取到 `'千代)'`。
4. ⚠️ **`blank_world_assets` 会把空开场白换成纪实句**（`<主角>来到<开局场景>。`）——它是主角的**第一条位置事实**，不是占位符；测试别断言"空"。
5. ⚠️ **前端桩是前缀匹配**。`["/api/worlds", …]` 会把 `/api/worlds/inspect-card` 一起吃掉，专用桩必须排在它前面。
6. ⚠️ **jsdom 数组跨 realm**。`assert/strict` 的 `deepEqual` 会比原型，从 probe 带回来的数组要先 `[...]` 转成 Node 的。
7. ⚠️ **ruff 的 isort 要求 `as` 别名各占一条 import 语句**（写成一行会被判 I001）。端点里 `from app.world.sillytavern import build_assets as build_card_assets` 得单独一行。

### 8.2 变异测试（证明新断言有牙齿）

改坏 13 处，全部被抓：

- **后端 10 处**：iTXt 压缩标志判反 · `disable` 去掉 `not` · 人名不看冒号 · 转成人物卡后不从世界书摘掉 · ccv3/chara 顺序颠倒 · 候选判据漏掉"非常驻" · 条目容器不兜底 · 不剥括号 · `order` 改成升序 · 常驻也要求有词。
- **前端 3 处**：类型被预选 · 不勾的条目也收 · 候选名字不跟条目 index 绑。

### 8.3 原复查清单（全部已过）

- [x] 三张真卡都能导（PNG V2 / PNG V3 / JSON）
- [x] 导出的世界过 `check_world`（零待修）
- [x] `ruff check` + `pytest -q` 全过 + 覆盖率 ≥ 90
- [x] 改了 `web/dist/` 后跑了 `scripts/bump_frontend_version.py`
- [x] 报告里"常驻代价"的字数 = 世界书里 `always_on` 条目字数之和（`test_常驻条目的代价会算进报告` 钉住）

---

## 附录 A：三张真卡实测（2026-09-25）

| | 世界卡（兽人模拟器） | 人物卡（高岭爱花） | 场景卡（圣花学园高二3班） |
|---|---|---|---|
| PNG 块 | `chara` + `ccv3` | **只有 `chara`（V2）** | `chara` + `ccv3` |
| `description` | **14 字**（发布声明） | 2713 字（`Name:/Type: character/Age:/Occupation:`） | 1536 字（叙事框架 + 角色群） |
| `personality`·`scenario`·`system_prompt`·`post_history_instructions`·`mes_example`·`creator_notes` | **全空** | **全空** | **全空** |
| `first_mes` | 2054 字（正文） | 167 字（正文） | 332 字 = **元说明页**「实际开场白请右滑开局」 |
| `alternate_greetings` | 4 条 | — | 6 条 |
| 世界书条目 | 17 | 9 | 28 |
| `constant` 占世界书字数 | **19430 / 32758 = 59%** | 2820 / 3854 = 73% | 453 / 12126 = **3.7%** |
| 非 constant 且有 keys | 5 | 2 | **26** |
| `use_regex` | **17/17 True** | 无此字段 | 无此字段 |
| `position` | after_char 9 / before_char 8 | 全 after_char | **before_char 27** / after_char 1 |
| `secondary_keys` | 全空 | 全空 | 全空 |
| 有 disabled 条目 | ✅ 条目[0] | 无 | 无 |

## 附录 B：未决

1. **WebP 卡 / CharX(zip) 要不要支持**？实测三张都是 PNG。等遇到再说。
2. **`extensions.world`（外部世界书）要不要提示**？现在只是丢弃。若玩家的卡大量依赖外挂世界书，可以再加"请一并导入世界书文件"的提示。
3. **导入后要不要自动弹出「世界工作台 → 概览」**让玩家立刻看到常驻条目的代价？现在只靠报告文字。
4. **场景卡的「开局场景」默认值**：现在默认填**卡名**，但实测卡名常常是**标题**（`《身为女校唯一男老师，开局内射全班》`），不是地名。试过从 `description` 抽，抓不可靠 ⇒ 先只填、留给玩家改（选「场景卡」时才填，其余类型不填）。
5. ~~**`you are …` 到底在哪**~~ → **已结案（2026-09-25 用户确认）**：「**有些卡就是没有这些提示词的**」。
   ⇒ 不能把 `you are …` 当类型判别依据；「类型由玩家在表单里选」是**终态**，不再去找自动判别。
