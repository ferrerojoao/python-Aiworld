# AIWorld

本地运行的 AI 文字世界引擎（回合制角色扮演）。后端 FastAPI，前端原生 HTML/JS 单页，**由后端一并托管**。

## 当前状态

- 后端 FastAPI 可运行；前端（左侧场景/在场栏 + 顶栏状态 + 主聊天区 + 右侧抽屉 + 世界浏览器）已具备完整交互。
- 回合流水线：**导演 → 说书人（多候选）→ 质检**，跨回合另有**审计** Worker 负责时间结算、状态与场景注册。
- 候选交互：主聊天框内 ◀ ▶ 切换候选，当前候选作为默认，下一次输入自动采纳，采纳静默。
- 账本：`events.jsonl`（事件流）+ `save.json`（世界状态），**一个世界目录就是一份存档**（`save.json` 与 `world.json` 同级）。
- 世界 = `content/<id>/`：世界概要、世界书、场景、NPC、数值属性（`axes.json`）等；可整包导出/导入。
- ⚠️ **`content/` 与 `data/` 是运行态、不进版本库**（见下节）。预设与设置同理，缺文件时会自动写一份默认值。
- API：SSE 回合交付 + 候选区采纳/重掷/放弃。

## 快速开始

```bash
# 建虚拟环境并安装（测试与冒烟都跑在这个 venv 里）
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"      # Windows
# .venv/bin/python -m pip install -e ".[dev]"              # macOS / Linux

# 启动服务（默认 http://127.0.0.1:8765）
./.venv/Scripts/python.exe app/main.py
```

Windows 下也可以直接双击 `run.bat`（等价于上面的启动命令）。

离线自检（不联网、不花 token，走 FakeLLM）：

```bash
./.venv/Scripts/python.exe -m pytest -q
```

## 首次运行：世界从哪来

**`content/` 不进版本库**（一个世界就是一份存档，属于运行态数据），所以新克隆的仓库里没有 `content/`，第一次启动时世界列表是空的。三条拿到世界的路：

1. **新建**：前端右上角「世界工作台 → 新建世界」（`POST /api/worlds/new`，或让模型起草 `POST /api/worlds/draft`）。
2. **导入资产包**：`POST /api/sessions/{sid}/world/import`（zip，base64 JSON），或前端「导入资产包」。
3. **把已有的 `content/<id>/` 目录拷进来**（`world.json` 必填；带 `save.json` 即续上旧存档）。

本机现有世界：`byh`、`qingshi2`、`shenshan`。

导出自己调好的世界用 `GET /api/sessions/{sid}/world/export`（得到可分享的 zip，不含存档）。

## 真实 LLM 联调

支持直接编辑根目录 `.env`，保存后重启服务即可生效，不需要每次手动设环境变量。**完整变量清单与注释见 `.env.example`**（HOST/PORT/鉴权/超时/各 Worker 模型覆盖等）。

最小示例（Ollama）：

```bash
AIWORLD_LLM_BASE_URL=http://127.0.0.1:11434/v1
AIWORLD_LLM_API_KEY=ollama
AIWORLD_MODEL_MAIN=qwen2.5:7b
AIWORLD_MODEL_CHEAP=qwen2.5:7b
```

然后跑冒烟测试：

```bash
./.venv/Scripts/python.exe scripts/smoke_llm.py
```

脚本会分别测试 `complete_json`（结构化输出）、`complete_text`（普通文本）与 Token 统计是否累积。如果本地模型不支持 `response_format=json_object`，网关会自动去掉后重试，并用 JSON 提取兜底。

跑真实一轮对话（会真的花额度）：

```bash
./.venv/Scripts/python.exe scripts/smoke_turn_real.py "推门进去"
```

## 测试

```bash
# 后端（pytest 228 个用例，全 FakeLLM 离线跑，不需要任何 API key）
./.venv/Scripts/python.exe -m pytest -q

# 前端（jsdom 里跑真实的 index.html + app.js）
cd web && npm ci && npm test
```

- 后端 `addopts` 已在 `pyproject.toml` 里设好（`-p no:cacheprovider --basetemp=bt_all`），临时目录权限受限的机器也能跑。
- CI 会在 push / PR 时自动跑这两套（`.github/workflows/ci.yml`）。
- **改了 `web/dist/` 下的前端资源，要跑一次 `scripts/bump_frontend_version.py`**：
  `index.html` 里的 `?v=` 是**文件内容哈希**，不是手改的序号（手改会忘，忘了就出"改了没变"）。
  `tests/test_frontend_version.py` 守着这条不变量，忘了跑 pytest 会直接红。

## API 摘要

所有路由都在 `/api` 前缀下（`app/api/routes_sessions.py` 等）。

**世界（资产包）**

- `GET /api/worlds` — 列出内容包
- `POST /api/worlds/new` — 新建空世界；`POST /api/worlds/draft` — 由模型起草世界
- `GET /api/worlds/{world_id}` — 读取世界资产
- `PUT /api/worlds/{world_id}` — 原地保存世界资产（有存档时拒绝）
- `DELETE /api/worlds/{world_id}` — 删除世界及其存档
- `GET /api/worlds/{world_id}/check` — 校验世界资产

**存档（会话）**

- `GET /api/sessions` — 列出存档；`POST /api/sessions` — 创建存档；`POST /api/sessions/open` — 打开已有存档
- `GET /api/sessions/{sid}` — 会话概要
- `POST /api/sessions/{sid}/reset` — 重置当前世界存档
- `GET /api/sessions/{sid}/export` — 导出存档
- `POST /api/saves/import` — 导入存档

**回合**

- `POST /api/sessions/{sid}/turn` — SSE 回合交付；body 可带 `adopt_candidate_id` 指定先采纳当前候选再进入新回合
- `GET /api/sessions/{sid}/candidates/pending` — 待定候选列表
- `POST /api/sessions/{sid}/candidates/{candidate_id}/adopt` — 采纳指定候选
- `POST /api/sessions/{sid}/turns/{turn_id}/reroll` — 重掷/抽卡，生成新候选
- `POST /api/sessions/{sid}/turns/{turn_id}/discard` — 放弃整个回合
- `GET /api/sessions/{sid}/debug/latest` — 最近一轮各工位的调试信息

**状态 / 账本 / 玩家**

- `GET /api/sessions/{sid}/state` — 世界状态快照（含时钟、结算留痕、本回合状态变化）
- `GET /api/sessions/{sid}/ledger/events` — 事件流
- `GET`/`PUT /api/sessions/{sid}/player` — 主角设定
- `POST /api/sessions/{sid}/states/{state_id}/revoke` — 撤销一条角色状态
- `PUT /api/sessions/{sid}/presets` — 更新写作预设（实际落全局 `data/presets.json`）

**世界工作台（会话内编辑）**

- `GET`/`PUT /api/sessions/{sid}/world` — 读取 / 原地保存世界资产
- `GET /api/sessions/{sid}/world/check` — 校验
- `POST /api/sessions/{sid}/world/save-as` — 另存为新世界资产包
- `GET /api/sessions/{sid}/world/export` — 导出资产包 zip
- `POST /api/sessions/{sid}/world/import` — 导入资产包 zip（base64 JSON）

**导演**

- `POST /api/sessions/{sid}/director` — 导演窗口对话（可带 action）
- `GET /api/sessions/{sid}/director/history` — 导演对话历史

**全局**

- `GET`/`PUT /api/presets` — 全局写作预设（落 `data/presets.json`）
- `GET`/`PUT /api/settings` — 系统设置（API/模型/注入上限，落 `data/settings.json`）
- `GET /api/settings/usage` — Token 统计

## 目录

```
app/
  api/           FastAPI 路由
  core/          LLM 网关、文件存储、预设
  ledger/        账本、事件、查询、访问控制
  rules/         规则段（目前只剩移动）
  runtime/       会话、回合、候选事务
  workers/       导演/说书人/质检/审计等 Worker
  world/         内容包加载与模型
web/            前端：dist/ 就是源码本体（原生 HTML/JS，无构建步骤），tests/ 是 jsdom 冒烟测试
scripts/        冒烟、诊断、演示、打包脚本
content/        运行态世界与存档（不进版本库）
data/           运行态设置与预设（不进版本库）
tests/          pytest 测试
docs/           设计文档（REQ / GDD / TDD）
.github/        CI 工作流
```

## 已知简化

- 关系数值属性（M5 / `axes.json`）暂缓，只保留字段。
- 规则段只剩移动；时间结算、状态变更、场景注册全部交给审计 Worker（见 `docs/TDD-技术设计文档-AIWorld.md`）。
- 真 LLM 接入需要配置 `AIWORLD_LLM_BASE_URL` 等环境变量（见 `.env.example`）。
- **场景**可以由模型自动注册（只落名字，描述需回工作台补）；**NPC 人物卡仍必须由人来建**（世界起草 / 世界工作台 / 直接改 `npcs/*.json`）。
- 🔴 `AIWORLD_AUTH_TOKEN` 目前是空壳：**没有请求级鉴权中间件**。绑 `0.0.0.0` 会暴露给整个网段，别把服务直接开到公网。
- 前端是原生 HTML/JS 单页（`web/dist`），核心交互已具备；`web/tests/` 有 3 条 jsdom 冒烟测试（守住"无世界"与「秘」标签判据这两个真出过的 bug），覆盖面仍然很薄。
- `?v=` 不是手改的序号，而是**文件内容哈希**（`scripts/bump_frontend_version.py` 写入）；改了 `web/dist/` 下的资源记得跑它。
