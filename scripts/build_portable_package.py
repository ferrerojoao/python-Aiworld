"""组装 AIWorld 一键包（一次性脚本，跑完即删）。

包体布局：
    AIWorld-一键包/
      run.bat                 一键启动
      run.py                  启动器（把包根插进 sys.path + 切工作目录）
      .env                    配置（API key 留空）
      requirements-runtime.txt
      app/ web/dist/ data/ content/qingshi2/
      runtime/python/         便携 Python 3.13.12 + 依赖
"""

from __future__ import annotations

import shutil
from pathlib import Path

SRC = Path(r"C:/AI/python-aiworld")
PKG = Path(r"C:/AI/AIWorld-一键包")

# ---- 0. 清掉上一轮的包体（保留 runtime/，那是几十分钟装出来的） --------------
for stale in ("app", "web", "data", "content", "run.py", "run.bat", ".env",
              "requirements-runtime.txt"):
    t = PKG / stale
    if t.is_dir():
        shutil.rmtree(t)
    elif t.exists():
        t.unlink()

# ---- 1. 后端 app/（跳过 __pycache__ / *.pyc） --------------------------------
shutil.copytree(
    SRC / "app",
    PKG / "app",
    dirs_exist_ok=True,
    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
)

# ---- 2. 前端 web/dist --------------------------------------------------------
shutil.copytree(SRC / "web" / "dist", PKG / "web" / "dist", dirs_exist_ok=True)

# ---- 3. 运行态数据 data/（预设 + 系统设置） ---------------------------------
#     settings.json 里**必须删掉 llm_api_key 这个键**，不能只置空：
#     load_settings_overrides() 对「键存在」即采纳，空串也算覆盖 ->
#     会把 .env 里用户新填的 key 盖回空值（静默失效）。删键即"不覆盖"。
(PKG / "data").mkdir(exist_ok=True)
for name in ("presets.json",):
    shutil.copy2(SRC / "data" / name, PKG / "data" / name)

import json  # noqa: E402

settings_raw = json.loads((SRC / "data" / "settings.json").read_text(encoding="utf-8"))
settings_raw.pop("llm_api_key", None)
(PKG / "data" / "settings.json").write_text(
    json.dumps(settings_raw, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
    newline="\n",
)

# ---- 4. 世界：只留 qingshi2，且只留世界设定（全新开局） ---------------------
shutil.copytree(
    SRC / "content" / "qingshi2",
    PKG / "content" / "qingshi2",
    dirs_exist_ok=True,
    ignore=shutil.ignore_patterns(
        "save.json", "events.jsonl", "candidates", "__pycache__"
    ),
)

# ---- 5. .env：沿用 base_url / 模型名，只把 key 留空 --------------------------
lines = (SRC / ".env").read_text(encoding="utf-8").splitlines()
keep = {}
for ln in lines:
    ln = ln.strip()
    if not ln or ln.startswith("#") or "=" not in ln:
        continue
    k, _, v = ln.partition("=")
    keep[k.strip()] = v.strip()

base_url = keep.get("AIWORLD_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
model_main = keep.get("AIWORLD_MODEL_MAIN", "")
model_cheap = keep.get("AIWORLD_MODEL_CHEAP", "")

env_text = f"""\
# AIWorld configuration
# First run: fill AIWORLD_LLM_API_KEY below, save, then run run.bat again.
AIWORLD_HOST=127.0.0.1
AIWORLD_PORT=8765
AIWORLD_AUTH_TOKEN=
AIWORLD_REQUIRE_AUTH_FOR_NON_LOCAL=true

# OpenAI-compatible gateway
AIWORLD_LLM_BASE_URL={base_url}
AIWORLD_LLM_API_KEY=
AIWORLD_LLM_TIMEOUT_SECONDS=90
AIWORLD_MODEL_MAIN={model_main}
AIWORLD_MODEL_CHEAP={model_cheap}

# LAN access: set HOST=0.0.0.0 and REQUIRE_AUTH_FOR_NON_LOCAL=false.
# The inbound rule needs an elevated prompt (adjust remoteip to your subnet):
# netsh advfirewall firewall add rule name="AIWorld 8765" dir=in action=allow protocol=TCP localport=8765 remoteip=192.168.1.0/24 profile=private

AIWORLD_CANDIDATE_TTL_DAYS=7
AIWORLD_AUDIT_ENABLED=true
AIWORLD_DATA_DIR=data
"""
(PKG / ".env").write_text(env_text, encoding="utf-8", newline="\n")

# ---- 6. requirements-runtime.txt（与开发 venv 逐版本对齐） -------------------
(PKG / "requirements-runtime.txt").write_text(
    "fastapi==0.141.1\n"
    "uvicorn==0.52.4\n"
    "pydantic==2.13.5\n"
    "openai==3.13.0\n"
    "httpx==0.28.1\n",
    encoding="ascii",
    newline="\n",
)

# ---- 7. run.py：便携解释器必需的启动器 --------------------------------------
run_py = '''"""AIWorld launcher.

Why this file exists: the bundled interpreter is Python's *embeddable* build. It
ships a `._pth` file, which puts the interpreter in isolated mode -- `sys.path`
ends up as [python313.zip, <python dir>, Lib/site-packages] and does NOT include
the script directory. So `python app/main.py` fails with `No module named 'app'`.
PYTHONPATH does not help either (isolated mode ignores it). This launcher patches
sys.path explicitly and pins the working directory, because `content/` and
`data/` are resolved relative to the process CWD.
"""

from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.config import get_settings  # noqa: E402  (must come after sys.path patch)

KEY = "AIWORLD_LLM_API_KEY"
LINE = "=" * 58


def main() -> None:
    import uvicorn

    settings = get_settings()
    host = "127.0.0.1" if settings.host in ("0.0.0.0", "") else settings.host
    # 只看**生效值**（.env + data/settings.json 合并后的结果），不自己解析 .env，
    # 否则一旦 settings.json 也带了 key，提示会和真实情况不一致。
    key = "set" if (settings.llm_api_key or "").strip() else "MISSING"

    print(LINE)
    print("  AIWorld")
    print(LINE)
    print("  URL       : http://" + host + ":" + str(settings.port) + "/")
    print("  API key   : " + key)
    if key != "set":
        # openai>=3 在 api_key 为空时构造客户端就抛 OpenAIError，uvicorn 起来后
        # 会当场甩一屏 traceback。这里提前拦住，给一句人话。
        print("  -> open .env and fill " + KEY + "=sk-xxxx, save, run run.bat again")
        print(LINE)
        print()
        print("[STOPPED] no API key - nothing was started.")
        raise SystemExit(1)
    print("  Stop      : Ctrl+C")
    print(LINE)
    print()

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
'''
(PKG / "run.py").write_text(run_py, encoding="utf-8", newline="\n")

# ---- 8. run.bat：全 ASCII + CRLF（LF-only 会让 if (...) 块解析出错） --------
run_bat = r"""@echo off
setlocal
title AIWorld
cd /d "%~dp0"

set "PY=%~dp0runtime\python\python.exe"

if not exist "%PY%" (
    echo [ERROR] Bundled runtime not found: runtime\python\python.exe
    echo.
    pause
    exit /b 1
)

"%PY%" run.py

echo.
echo AIWorld stopped.
echo.
pause
endlocal
"""
(PKG / "run.bat").write_bytes(run_bat.replace("\n", "\r\n").encode("ascii"))

print("built ->", PKG)
for p in sorted(PKG.iterdir()):
    print("  ", p.name)

# ---- 9. 交付前体检：绝不能带出密钥 / 文档 / 存档 ----------------------------
print("\n=== 体检 ===")
import re  # noqa: E402

KEY_RE = re.compile(r"sk-[A-Za-z0-9]{16,}")  # 真实密钥特征；`sk-...` 占位符不算
leaks: list[str] = []
for f in PKG.rglob("*"):
    if not f.is_file() or f.is_relative_to(PKG / "runtime"):
        continue
    rel = f.relative_to(PKG).as_posix()
    if f.suffix in (".json", ".env", ".txt", ".py", ".js", ".html", ".css", ".bat", ".pyi"):
        txt = f.read_text(encoding="utf-8", errors="replace")
        hit = KEY_RE.search(txt)
        if hit:
            leaks.append(f"含密钥 {hit.group()[:12]}... : {rel}")
    if f.name in ("save.json", "events.jsonl"):
        leaks.append(f"不该有存档 : {rel}")
for bad in ("docs", "tests", ".git", ".workbuddy", "__pycache__"):
    if (PKG / bad).exists():
        leaks.append(f"不该有的目录/文件 : {bad}")
worlds = sorted(p.name for p in (PKG / "content").iterdir() if p.is_dir())
print("世界:", worlds)
sjson = json.loads((PKG / "data" / "settings.json").read_text(encoding="utf-8"))
print("settings.json 键:", sorted(sjson))
print("llm_api_key 是否已删:", "llm_api_key" not in sjson)
print("泄漏项:", leaks or "无")
if leaks:
    raise SystemExit("体检不通过")
