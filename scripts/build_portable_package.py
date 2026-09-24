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

import datetime
import shutil
import subprocess
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
#     🔴 清单 = **app/ 里的全部第三方 import**，不是"上次那 5 个"。
#     2026-09-24 补 pillow：当天新加的 app/world/images.py 在模块级
#     `from PIL import Image, ImageOps`，老清单缺它 ⇒ 包打出来**连服务都起不来**
#     （不是某个功能坏，是 import app.main 就炸）。改完 app/ 的 import 记得回来对。
#     核法：扫 app/**.py 的 ast import，减掉 sys.stdlib_module_names。
(PKG / "requirements-runtime.txt").write_text(
    "fastapi==0.141.1\n"
    "uvicorn==0.52.4\n"
    "pydantic==2.13.5\n"
    "openai==3.13.0\n"
    "httpx==0.28.1\n"
    "pillow==12.3.0\n",
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

Double-clicking run.bat is the documented way in, but it is no longer required:
if this file happens to be started by some other Python, reexec_with_bundled()
restarts it with the bundled one.
"""

from __future__ import annotations

import os
import pathlib
import socket
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

KEY = "AIWORLD_LLM_API_KEY"
LINE = "=" * 58
BUNDLED_PY = (ROOT / "runtime" / "python" / "python.exe").resolve()
BUILD_FILE = ROOT / "BUILD.txt"


def build_stamp() -> str:
    """这个包是**什么时候、从哪个提交**打出来的。

    2026-09-24 教训：用户换上新包后报"那台电脑上运行的还是早期版本"，而当时两个
    文件夹在界面上**完全无法分辨**（新旧 banner 一字不差、包里也没有版本标记），
    只能靠猜。这一行就是那个缺失的标记 —— 判据很简单：**打开窗口先看 Build 和
    Folder，就知道自己刚启动的是哪一份、在哪个目录**。
    """
    try:
        return BUILD_FILE.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown (BUILD.txt missing - not a freshly built package)"


def refuse_busy_port(host: str, port: int) -> None:
    """"换了新包却看到旧版本"几乎都是这个原因，必须在起服务前说清。

    早期那一份服务还挂在同一个端口上，新这份要么还没填 key、要么根本绑不上端口，
    而浏览器打开的地址回答的**是旧那一份**。不说清的话，用户会一直以为自己跑的
    是新版（2026-09-24 用户报障）。

    用裸 socket connect_ex 探，不用 HTTP 客户端 —— 免得被 HTTP_PROXY 之类带偏
    （本机实测过：走代理探 127.0.0.1 会拿到代理吐的 502，看着像有人在）。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        if sock.connect_ex((host, port)) != 0:
            return
    print(LINE)
    print("  AIWorld cannot start")
    print(LINE)
    print("  Port " + str(port) + " on " + host + " is ALREADY TAKEN.")
    print()
    print("  Something else is answering there - most likely an OLDER copy of")
    print("  AIWorld that is still running. Closing its window is not always")
    print("  enough; look for leftover python.exe in Task Manager.")
    print()
    print("  The page at http://" + host + ":" + str(port) + "/ right now is")
    print("  THAT copy, NOT this one.")
    print()
    print("  -> End every python.exe, then run run.bat again.")
    print("  -> Or pick a free port in .env (AIWORLD_PORT=8766) and open that.")
    print(LINE)
    raise SystemExit(1)


def reexec_with_bundled() -> None:
    """跑我的如果不是包内解释器，就用包内解释器把自己重新起一遍。

    双击 run.py（或在别的终端敲 python run.py）会用 PATH 上那份 Python，它没有本包
    需要的依赖，表现就是 `No module named 'uvicorn'` —— 这是本包最容易被踩的坑，
    而它的"解法"本来只是「双击 run.bat」。与其教育用户点对文件，不如自己换过去。

    主动放弃的两种情况（原地返回，交给下面的守卫给人话）：
      ① 包内解释器不存在 / 换不过去（被杀软拦、文件损坏）；
      ② env 里已有标记（兜底，正常第一遍就换成功）。
    """
    if str(pathlib.Path(sys.executable).resolve()).lower() == str(BUNDLED_PY).lower():
        return
    if not BUNDLED_PY.is_file():
        return
    if os.environ.get("AIWORLD_REEXEC") == "1":
        return
    print("[launcher] not the bundled interpreter - restarting with " + str(BUNDLED_PY), flush=True)
    env = dict(os.environ)
    env["AIWORLD_REEXEC"] = "1"
    # 用 subprocess 而不是 os.execve：Windows 上 exec* 是"起新进程 + 父进程立刻以 0
    # 退出"，在终端里会先冒出一行 shell 提示、让人以为程序已经结束。这里等它跑完并
    # 把退出码传出去（Ctrl+C 也照常传到子进程）。
    try:
        rc = subprocess.run(
            [str(BUNDLED_PY), str(pathlib.Path(__file__).resolve()), *sys.argv[1:]],
            env=env,
        ).returncode
    except OSError:
        return  # 换不过去就继续走，让人话提示来解释
    raise SystemExit(rc)


reexec_with_bundled()


def bail_missing(mod: str) -> None:
    """缺依赖时说人话 —— 让 ModuleNotFoundError 裸奔，用户会以为包是坏的。

    三种成因共用这一条通道（导入都收在 main() 的 try 里）：

    ① 解释器不是包内的 —— reexec_with_bundled() 没救回来的情况（包内解释器被杀软
       拦了、文件损坏）；
    ② 包内 site-packages 不全 —— 拷贝 / 解压丢了文件；
    ③ app/ 自己被拷坏 —— 这种 exc.name 以 app. 开头，走 main() 里另一条分支。

    用户历史上报的是 "No module named uvicorn"：uvicorn 是 main() 里第一个第三方
    import，所以**任何**依赖缺失都会以它当门面出现，看着像"缺 uvicorn"，其实未必。
    """
    running = pathlib.Path(sys.executable).resolve()
    print(LINE)
    print("  AIWorld cannot start")
    print(LINE)
    print("  Missing package : " + mod)
    print("  Running with    : " + str(running))
    if str(running).lower() != str(BUNDLED_PY).lower():
        print()
        print("  This is NOT the interpreter bundled with this package.")
        print("  Expected        : " + str(BUNDLED_PY))
        print()
        print("  -> Close this window and double-click run.bat.")
        print("     Do not run run.py directly: that picks up whatever Python")
        print("     Windows has on PATH, which lacks the required packages.")
    else:
        print()
        print("  The bundled runtime looks incomplete - a normal copy has")
        print("  Lib/site-packages/<" + mod + ">.")
        print("  -> Re-copy or re-extract the whole folder, then run run.bat.")
    print(LINE)
    raise SystemExit(1)


def main() -> None:
    # 第三方依赖与 app 自己的 import 全部收进 try：解释器不对 / site-packages 不全 /
    # app/ 被拷坏，三种情况一律变成一句人话，而不是甩一屏栈。
    try:
        # app.main 先导一遍：uvicorn 是之后才 import 它的，缺 fastapi / pydantic /
        # pillow 之类本来会变成"启动到一半"的 traceback。副作用为零 —— 下面
        # uvicorn 再导一次命中的是同一个模块。
        import app.main  # noqa: F401
        import uvicorn
        from app.config import get_settings
    except ModuleNotFoundError as exc:
        name = getattr(exc, "name", None) or "unknown"
        if name.split(".")[0] == "app":
            # app.* 自己缺文件 = 包被拷坏，不是依赖问题，别给「换个解释器」的建议。
            print(LINE)
            print("  AIWorld cannot start")
            print(LINE)
            print("  This package looks damaged: " + str(exc))
            print("  -> Re-copy or re-extract the whole folder.")
            print(LINE)
            raise SystemExit(1) from None
        bail_missing(name)

    settings = get_settings()
    host = "127.0.0.1" if settings.host in ("0.0.0.0", "") else settings.host
    # 只看**生效值**（.env + data/settings.json 合并后的结果），不自己解析 .env，
    # 否则一旦 settings.json 也带了 key，提示会和真实情况不一致。
    key = "set" if (settings.llm_api_key or "").strip() else "MISSING"

    print(LINE)
    print("  AIWorld")
    print(LINE)
    print("  URL       : http://" + host + ":" + str(settings.port) + "/")
    print("  Build     : " + build_stamp())
    print("  Folder    : " + str(ROOT))
    print("  API key   : " + key)
    if key != "set":
        # openai>=3 在 api_key 为空时构造客户端就抛 OpenAIError，uvicorn 起来后
        # 会当场甩一屏 traceback。这里提前拦住，给一句人话。
        print("  -> open .env and fill " + KEY + "=sk-xxxx, save, run run.bat again")
        print(LINE)
        print()
        print("[STOPPED] no API key - nothing was started.")
        raise SystemExit(1)
    # 放在 key 闸门之后：key 为空时本来就什么都没启动，不存在"看到的是旧版本"
    # 这种误解；而 key 填好之后还看到旧版，就是这个端口被别人占着。
    refuse_busy_port(host, settings.port)
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

# ---- 8.5 BUILD.txt：让"我跑的是哪一份"可分辨 --------------------------------
#    2026-09-24 用户报"换新包后那台电脑上运行的还是早期版本"——而当时新旧两份的
#    banner 一字不差、文件夹里也没有任何版本标记，两边只能靠猜。这一行就是那个
#    缺失的标记（和前端 ?v= 是同一个道理：**"刷新了没 / 换了没"必须可见**）。
def _git(*args: str) -> str:
    """取 git 信息；没装 git / 不在仓库里都返回空串，绝不因此让打包失败。"""
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=str(SRC),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


_stamp = _git("rev-parse", "--short", "HEAD") or "unknown"
if _git("status", "--porcelain"):
    _stamp += "+dirty"
_stamp = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat() + "  git:" + _stamp
(PKG / "BUILD.txt").write_text(_stamp + "\n", encoding="utf-8", newline="\n")

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

# ---- 10. 依赖完整性：用**包内解释器**真的 import 一遍 app.main ----------------
#     静态比对清单没用——漏一个第三方包的表现不是"某功能坏"，而是**服务起不来**。
#     2026-09-24 的 pillow 就是这么漏的（app/world/images.py 模块级 import PIL）。
#     这里直接把包内 runtime 当解释器跑一次 import，缺什么当场就报，不用等到验收。
_runtime_py = PKG / "runtime" / "python" / "python.exe"
if _runtime_py.exists():
    import os as _os
    import subprocess

    _probe = (
        "import pathlib, sys;"
        "sys.path.insert(0, str(pathlib.Path.cwd()));"
        "import app.main;"
        "print('import app.main ok')"
    )
    _env = dict(_os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    _r = subprocess.run(
        [str(_runtime_py), "-c", _probe],
        cwd=PKG,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=_env,
    )
    _out = _r.stdout.decode("utf-8", "replace").strip()
    _tail = _out.splitlines()[-1] if _out else "(no output)"
    print("依赖自检:", _tail)
    if _r.returncode != 0:
        leaks.append("包内解释器 import app.main 失败（缺依赖 / 代码错）:\n" + _out)
else:
    print("依赖自检: 跳过（runtime/python/python.exe 不存在，包不完整）")
    leaks.append("包内没有 runtime/python/python.exe —— 拷到别的电脑跑不起来")

sjson = json.loads((PKG / "data" / "settings.json").read_text(encoding="utf-8"))
print("settings.json 键:", sorted(sjson))
print("llm_api_key 是否已删:", "llm_api_key" not in sjson)
print("泄漏项:", leaks or "无")
if leaks:
    raise SystemExit("体检不通过")
