# 接手说明 · 给下一个 Agent

> 日期：2026-10-19（对应提交 `d292011`）
> 这份文档只回答两件事：**怎么接手**、**哪些没做完**。
> 机制细则看 `REQ-机制需求清单-AIWorld.md`，实现细节看 `TDD-技术设计文档-AIWorld.md`，踩过的坑看 `.workbuddy/memory/`。

---

## 0. 先确认三件事

1. **本项目已冻结**（2026-09-24）：**只修 bug，不加特性**。
2. 新特性、结构改动一律去后继项目 `C:\AI\aiworld-alive`（文档在其 `docs/设计-总纲.md`、`docs/实现-总览.md`，历史方案在 `docs/历史/`）。
3. 判据很简单：**你要做的事，是让现有功能重新正确，还是让世界更活？** 前者留在这里，后者去那边。

> 🔴 冻结不等于"不许动代码"。2026-10-12 还修过大总结的提示词——那是 bug（旧编年史被丢弃），不是特性。

---

## 1. 五分钟跑起来

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
./.venv/Scripts/python.exe app/main.py        # 或双击 run.bat
# 浏览器打开 http://127.0.0.1:8765/  （服务不会自己弹窗）
```

必须有一个非空的 `AIWORLD_LLM_API_KEY`，否则服务拒绝启动。本机开发用 `data/settings.json` 里的网关配置。

### 闸门（改完必须全绿）

| 检查 | 命令 | 当前基线 |
|---|---|---|
| 后端测试 | `.venv/Scripts/python.exe -m pytest -q` | **463 passed** |
| 覆盖率 | `.venv/Scripts/python.exe -m pytest -q --cov=app --cov-report=term-missing` | **93.22%**（闸门 `fail_under = 90`） |
| 静态检查 | `.venv/Scripts/python.exe -m ruff check .` | 0 |
| 前端 | `cd web && node --test`（**无参**） | **51 passed** |
| 前端版本号 | `.venv/Scripts/python.exe scripts/bump_frontend_version.py --check` | 一致 |

### 三个环境坑（不知道会浪费半天）

- **`pytest` 一律前台跑**。后台跑（`run_in_background`）沙箱写不了临时目录 → 成片 **ERROR**（不是 FAILED）。看到这个先怀疑沙箱，别改代码。
- **前端别用 `npm test`**：这套环境会去拉 `wsl.exe` 被安全策略拦死。裸跑无参 `node --test`。
- **改了 `web/dist/` 必须跑 `bump_frontend_version.py`**（按内容哈希刷 `?v=`），否则浏览器吃旧缓存。入口页 `app/main.py::_StaticAssets` 已设 `no-store`，别改成 `no-cache`（会吐错的 304）。

---

## 2. 代码地图

| 位置 | 管什么 |
|---|---|
| `app/main.py` | 入口、静态资源缓存策略、鉴权挂载 |
| `app/config.py` | **唯一配置源**（环境变量 + `data/settings.json`）；`AUX_WORKERS` 名单在这 |
| `app/core/llm.py` | 模型出口：档位判定 + 主/辅两套 base_url |
| `app/core/workorder.py` | **所有提示词块的拼装**（最大的一块文本，改措辞先读它） |
| `app/core/presets.py` | 文风预设（生效的是 `data/presets.json`） |
| `app/runtime/turn.py` | 一回合的流水线（下面 §3） |
| `app/runtime/transaction.py` | **唯一写者**：`commit()` 落账、推钟、结算 |
| `app/runtime/session.py` | 会话内存态（`director_history` / `featured_counts` …） |
| `app/ledger/events.py` | append-only 事件流 `events.jsonl` |
| `app/ledger/save.py` | `save.json`：时钟、在场、计数、状态、候选册 |
| `app/ledger/digest.py` | 大总结写入（`apply_digest`，压缩进 `archive/`） |
| `app/ledger/queries.py` | 读侧查询；`region_scope()` 是 region 三态的**唯一映射点** |
| `app/ledger/access.py` | 可见性（`access_view` / `effective_known_by`） |
| `app/workers/{writer,qc,actor,auditor,digest,landing,drafter}.py` | 各工位；`schemas.py` 是结构化输出契约 |
| `app/world/{loader,models,images,draft,sillytavern}.py` | 内容包加载/校验、附图、转正草稿、ST 卡导入 |
| `app/rules/{route,movement,scenes,lorebook}.py` | **唯一的零 LLM 规则段**（基本只剩"移动"） |
| `web/` | 无构建步骤的单页（`dist/app.js` 手写；`tests/` 走 jsdom） |
| `scripts/` | **只留 5 个**：打包、bump 版本号、三个真实 LLM 冒烟 |

---

## 3. 一个回合发生了什么

```
POST /sessions/{sid}/turn
  → turn.py  TurnRunner
  1. 规则段（零 LLM）：looks_like_move → resolve_destination → lorebook 命中
  2. 编剧 run_writer：工单在 workorder.py；遇到"深抉择"中途派 run_actor（两段式）
  3. 质检 run_qc（qc_enabled 可关；本机 data/settings.json 现在是 false ⇒ 不跑）
  4. 产出候选 → 存 candidates/ → SSE 推前端
     ⚠️ 到这里账本一个字节都没动
  5. 玩家采纳 → Transaction.commit
       落事件 · 推时钟 · 状态/目标结算 · 跑 run_audit
  6. 审计产出 Change，**由 commit 一处**写进账本
```

---

## 4. 三条不能破的纪律

1. **账本是唯一真相，写只走一处**：所有落账都在 `Transaction.commit`。别在 worker 里直接写 `events.jsonl` 或 `save.json`。
2. **时钟只由审计推**：`clock_to`（绝对对钟，优先）→ `delta_minutes`（clamp 0–1440）。规则侧跳时**已整条删除**，别再提"让规则/编剧定钟"。
3. **玩家确认才落账**：候选不是事实。重掷只换候选，采纳才推进世界。

另有两条改动时的硬规矩：

- **"文件大"≠"提示词多"**：要减 token 先动参数（`recent_full` / `limit`），别改措辞。
- **新的守卫断言一律做变异测试**（把被测代码改坏，看它红不红）。只绿不算数——本项目有过"恒真假守卫"。

---

## 5. 未完成的设计

### A. 会动结构的（都在冻结前论证过、没写代码）

| 项 | 状态 | 卡在哪 / 已定结论 |
|---|---|---|
| **地图** | 未实施 | 邻接图**已退役**（`REQ:231` / `REQ:263`）⇒ 地图上不该有边；唯一空间结构是 `region` 层级（如 `集市/下层/暗渠/上层`，无 region 归「未分区」）。别再造一套邻接关系 |
| **位置面板** | 未实施 | **零新索引**：「最后目击」早就在编剧提示词里（`workorder._rel_seen`）⇒ 面板只是把已有信息也给玩家看。⚠️ 别重复造"此刻" |
| **玩家输入落点** | 未实施 | 方案里讨论了"输入落到哪个字段"，未拍板 |
| **世界模拟改进** | 未实施 | 实质是"让世界更活" ⇒ **属于后继项目，不该在这里做** |
| **刷新位置**（点一下让 LLM 判断各 NPC 此刻该在哪并移动） | 未实施 | 影响面已评估、规格已细化到可实施；原文在 `.workbuddy/memory/MEMORY-DETAIL.md` §18/§19 |

方案原文 2026-10-08 已从 `docs/` 删除（提交 `52c1119`），要原文从 git 历史取：

```bash
git show 52c1119^:docs/方案-地图-AIWorld.md
git show 52c1119^:docs/方案-位置面板-AIWorld.md
git show 52c1119^:docs/方案-玩家输入落点-AIWorld.md
git show 52c1119^:docs/方案-世界模拟改进-AIWorld.md
```

> 🔴 **别引用 `docs/方案-*` 的章节号**——文件已经不在了，引用等于让下一个人去找不存在的东西。

### B. 小口子（不需要设计，确认后可直接修）

| # | 问题 | 现状 |
|---|---|---|
| 1 | `landing` / `digest` 没有专属 `model_*` 覆盖字段 | 只有 `director/actor/story/qc/audit/classify` 有。想让大总结单独用便宜模型，**现在做不到** |
| 2 | `classify` 是空挂项 | 在 `AUX_WORKERS` 里、有 `MODEL_CLASSIFY` 字段，但**全仓零调用点** |
| 3 | `archive/` 不在备份范围 | `_SKIP_DIRS = {"candidates", "archive"}` ⇒ 不进导出包/全量备份；`/reset` 也不清它（只增不减、跨重置累积）。旧编年史唯一的坟 |
| 4 | `director_history` 只存 `reply` 文本、不存 `action` | 导演窗口下一轮"忘记"自己刚提过什么（`routes_director.py:89-90`） |
| 5 | 审计误推钟无兜底 | 审计把钟估快了就**没有回滚机制**（`clock_to` 只会拒绝"往回对"）。规则侧跳时删除后误推面已小很多。旧注记在 TDD §3.1，现已不在 TDD 里 |
| 6 | `TDD §5.6` 有过期条目 | 它说质检"秘密禁区"未实施，**实际已实现**（`qc.py:37-45,95`）；同句的「Actor 决策块进质检参照」**确实没做**。别按错前提排期 |

### C. 明确暂缓 / 已作废（**别再重提**）

| 项 | 为什么不再做 |
|---|---|
| **主模型流式输出** | 质检会改写初稿 ⇒ 玩家看到的不是最终版，加速毫无意义。除非先解决"流式与质检互斥" |
| **数值轴 / 关系值** | 旧实现已删净（`537167f`）。前置问题没答案：**"玩家输入是否必须完全执行"**。在那之前谈数值都是空的 |
| **大总结改回增量式** | 2026-10-12 已定：**吸收式（每轮回炉重压成一份）**。多份并存会忽厚忽薄、梯度断裂。详见 `REQ` 〇章 |

---

## 6. 别做的事

- ❌ 加任何新特性（去 `C:\AI\aiworld-alive`）
- ❌ 提交 `content/` 与 `data/presets.json`（运行数据，已从历史清干净；回滚凭据是 `C:/AI/python-aiworld-history-backup-*.bundle`）
- ❌ 把覆盖率闸门往下调（往上可以，往下要说出理由）
- ❌ 用 `ruff format` 重排全仓（会动 50+ 文件，属另一次独立决策）
- ❌ 改 `docs/` 下三份主文档里的"已拍板"结论——先读 `REQ`，那里是机制权威源
- ❌ 相信本地 `origin/master` 的跟踪值（会陈旧）⇒ 判远端一律 `git ls-remote origin -h refs/heads/master`

---

## 7. 深水区在哪

`docs/` 只讲**结论**；**为什么这么定、哪里踩过坑**全在项目记忆里：

| 文件 | 内容 |
|---|---|
| `.workbuddy/memory/MEMORY.md` | 20 条判据索引（先读这个，读完能避掉 90% 的坑） |
| `.workbuddy/memory/MEMORY-DETAIL.md` | 逐条论据与经过（1300+ 行，按需查章节） |
| `.workbuddy/memory/2026-*.md` | 按日工作日志（最近：10-12 大总结事故） |

最值得先看的三条判据：

1. **`glob()/iterdir()` 的次序由文件系统给** ⇒ 凡被断言或被引擎读的顺序一律显式 `sorted`。
2. **`content_hash` 必须先 CRLF→LF 归一、全仓只此一份** ⇒ "校验 X==f(Y)"的 `f` 必须从被测代码 import。
3. **jsdom 有三条硬边界**（`<select>` 不能当裁判 / `getComputedStyle` 对 hidden 元素撒谎 / 不排版）⇒ 那类 UI 守卫要么换成读源码规则，要么别写。
