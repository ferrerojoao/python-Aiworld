# AIWorld

本地运行的 AI 文字世界引擎 MVP。

## 当前状态

- 后端 FastAPI 骨架已可运行。
- 内容包加载：`content/qinghsi` 示例世界。
- 账本：`events.jsonl` + `save.json`。
- 前端排版：左侧窄栏（场景导航/在场 NPC）+ 顶栏状态 + 主聊天区 + 右侧抽屉（导演/预设/设置）+ 世界浏览器弹窗。
- 预设是全局通用配置，存放在 `data/presets.json`，不随世界资产包导出/导入。
- 世界资产包只包含世界内容：世界概要、世界书、场景、NPC、数值属性（`axes.json`）等。
- 候选交互：主聊天框内用 ◀ ▶ 切换多个候选，当前候选作为默认，下一次输入自动采纳，采纳静默。
- 回合流水线：导演 → 说书人 → 质检（可通过 FakeLLM 离线测试）。
- API：SSE 回合交付 + 候选区采纳/重掷/放弃。

## 快速开始

```bash
# 安装依赖
pip install -e .

# 运行服务
python -m app.main
# 默认 http://127.0.0.1:8765
# 打开浏览器即可看到简易前端

# 无真实模型时，可用 FakeLLM 演示前端
# Windows PowerShell: .\scripts\start_fake.ps1
# bash: ./scripts/start_fake.sh
# 或手动设置 AIWORLD_FAKE_LLM=true 再 python -m app.main

# 离线演示（不需要真实模型）
python scripts/demo.py
```

## 真实 LLM 联调

项目支持直接编辑根目录下的 `.env` 文件，保存后重启服务即可生效，不需要每次手动设置环境变量。

`.env` 示例：

```bash
AIWORLD_LLM_BASE_URL=http://127.0.0.1:11434/v1
AIWORLD_LLM_API_KEY=ollama
AIWORLD_MODEL_MAIN=qwen2.5:7b
AIWORLD_MODEL_CHEAP=qwen2.5:7b
```

然后跑冒烟测试：

```bash
python scripts/smoke_llm.py
```

脚本会分别测试：

- `complete_json`（结构化输出）
- `complete_text`（普通文本）
- Token 统计是否会累积

如果本地模型不支持 `response_format=json_object`，网关会自动去掉后重试，并用 JSON 提取兜底。

Windows PowerShell 示例：

```powershell
$env:AIWORLD_LLM_BASE_URL="http://127.0.0.1:11434/v1"
$env:AIWORLD_LLM_API_KEY="ollama"
$env:AIWORLD_MODEL_MAIN="qwen2.5:7b"
$env:AIWORLD_MODEL_CHEAP="qwen2.5:7b"
python scripts/smoke_llm.py
```

## 测试

```bash
python -m pytest -q
# 如果系统临时目录权限受限，可改用：
# python -m pytest -q -p no:cacheprovider --basetemp=.pytest_tmp
```

## API 摘要

- `GET /api/worlds` — 列出内容包
- `DELETE /api/worlds/{world_id}` — 删除世界及其存档
- `PUT /api/worlds/{world_id}` — 原地保存世界资产（有存档时拒绝）
- `POST /api/worlds/{world_id}/save-as` — 另存为新世界资产包
- `POST /api/sessions` — 创建存档
- `POST /api/sessions/open` — 打开已有存档
- `POST /api/sessions/{sid}/turn` — SSE 回合交付；body 可带 `adopt_candidate_id` 指定先采纳当前候选再进入新回合
- `GET /api/sessions/{sid}/candidates/pending` — 待定候选列表
- `POST /api/sessions/{sid}/candidates/{candidate_id}/adopt` — 采纳指定候选
- `POST /api/sessions/{sid}/turns/{turn_id}/reroll` — 重掷/抽卡，生成新候选
- `POST /api/sessions/{sid}/turns/{turn_id}/discard` — 放弃整个回合
- `POST /api/sessions/{sid}/director` — 导演窗口（含真实导演对话）
- `GET /api/sessions/{sid}/director/history` — 导演对话历史
- `GET /api/sessions/{sid}/world` — 世界浏览器数据
- `GET /api/sessions/{sid}/world/export` — 导出资产包 zip
- `POST /api/sessions/{sid}/world/import` — 导入资产包 zip（base64 JSON）
- `POST /api/sessions/{sid}/reset` — 重置当前世界存档
- `GET/PUT /api/settings` — 系统设置（API/模型/FakeLLM）
- `GET /api/settings/usage` — Token 统计

## 目录

```
app/
  api/           FastAPI 路由
  core/          LLM 网关、文件存储
  ledger/        账本、事件、查询、访问控制
  rules/         规则段（路由/移动/场景/声明）
  runtime/       会话、回合、候选事务
  workers/       导演/说书人/质检/审计等 Worker
  world/         内容包加载
content/         可替换世界内容包
tests/           pytest 测试
docs/            设计文档
```

## 已知简化

- v1 关系数值属性（M5/`axes.json`）暂缓，只保留字段。
- 移动/声明规则段是简单规则，未完整实现 M1/M17/M18/M19。
- 真 LLM 接入需要配置 `AIWORLD_LLM_BASE_URL` 等环境变量。
- 前端是简易原生 HTML/JS 页面，核心交互已具备；复杂 UI（导演窗口/设置面板）后续再补。