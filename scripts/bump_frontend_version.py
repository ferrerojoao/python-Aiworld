"""把 web/dist/index.html 里的 ``?v=`` 刷成被引用文件的**内容哈希**。

为什么不是手改的序号：``?v=NN`` 时代改完 ``app.js`` 忘了跳号，浏览器继续跑缓存里的
旧 JS，表现就是"我改了但没变"（踩过多次，且极难自查）。改成内容哈希后，这个数只有
一个来源——文件本身。忘了跑本脚本，``tests/test_frontend_version.py`` 会直接红。

用法：
    ./.venv/Scripts/python.exe scripts/bump_frontend_version.py          # 就地刷新
    ./.venv/Scripts/python.exe scripts/bump_frontend_version.py --check  # 只校验，不写盘
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
INDEX = DIST / "index.html"

# 与 tests/test_frontend_version.py 同一套判据：只认已经带 ?v= 的本地引用。
REF = re.compile(r'((?:href|src)=")([^"?]+)\?v=([^"]*)(")')


def content_hash(path: Path) -> str:
    """被引用文件的**平台无关**内容哈希。**唯一权威**——判据侧不许再抄一份。

    ⚠️ 必须先把 CRLF 归一成 LF 再算。原因是一次真实的 CI 事故（2026-09-18）：
    本机 ``core.autocrlf=true`` → 工作区 CRLF、git blob 里是 LF；仓库又没有
    ``.gitattributes``。于是同一个 ``app.js`` 在 Windows 上是 94358 字节的 CRLF、
    在 Linux CI 上是 94278 字节的 LF —— **两种字节两种哈希**，``?v=`` 只有在算它
    的那台机器上对得上，CI 必红，而本机 ``--check`` 永远绿。

    行尾不是"内容"：浏览器拿到 LF 版和 CRLF 版跑的是同一份 JS，不该换缓存版本号。
    """
    return hashlib.sha1(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:8]


def bump(check_only: bool = False) -> int:
    # newline="" 保住原有换行符，别让本脚本顺手把 CRLF 改成 LF。
    # 用 open() 而不是 Path.read_text(newline=...) —— 后者到 3.13 才支持。
    with INDEX.open(encoding="utf-8", newline="") as fh:
        html = fh.read()
    changes: list[str] = []
    missing: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        prefix, name, old, suffix = m.groups()
        target = DIST / name
        if not target.is_file():
            missing.append(name)
            return m.group(0)
        new = content_hash(target)
        if new != old:
            changes.append(f"  {name}: ?v={old} -> ?v={new}")
        return f"{prefix}{name}?v={new}{suffix}"

    updated = REF.sub(_sub, html)

    if missing:
        print("以下被引用的文件不存在，无法计算哈希：", file=sys.stderr)
        for name in missing:
            print(f"  {name}", file=sys.stderr)
        return 2

    if changes:
        if check_only:
            print("?v= 与内容哈希不符（index.html 需要刷新）：", file=sys.stderr)
            print("\n".join(changes), file=sys.stderr)
            print(
                "\n跑一次修复：./.venv/Scripts/python.exe scripts/bump_frontend_version.py",
                file=sys.stderr,
            )
            return 1
        with INDEX.open("w", encoding="utf-8", newline="") as fh:
            fh.write(updated)
        print("已刷新 index.html：")
        print("\n".join(changes))
        return 0

    print("index.html 的 ?v= 已与内容哈希一致，无需改动。")
    return 0


def main(argv: list[str]) -> int:
    if not INDEX.is_file():
        print(f"找不到 {INDEX}", file=sys.stderr)
        return 2
    return bump(check_only="--check" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
