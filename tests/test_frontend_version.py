"""守住 `?v=` 这条不变量：它必须等于被引用文件的**内容哈希**。

背景：`?v=NN` 原先是手改的，改了 `app.js` 忘了跳号 → 浏览器继续跑旧 JS，用户看到的
是"改了没变"（踩过多次）。现在版本号由文件内容决定（`scripts/bump_frontend_version.py`），
这个测试就是那道闸：改了前端没跑脚本 → 这里直接红，并告诉你跑哪个命令。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
INDEX = DIST / "index.html"

REF = re.compile(r'(?:href|src)="([^"?]+)\?v=([^"]*)"')


def _refs() -> list[tuple[str, str]]:
    return REF.findall(INDEX.read_text(encoding="utf-8"))


def test_index_has_versioned_assets() -> None:
    refs = _refs()
    assert refs, "web/dist/index.html 应当带 ?v= 引用前端资源"
    names = {name for name, _ in refs}
    assert {"app.js", "style.css"} <= names, f"应当同时引用 app.js 与 style.css，实际 {names}"


def test_version_query_equals_content_hash() -> None:
    for name, ver in _refs():
        target = DIST / name
        assert target.is_file(), f"index.html 引用了不存在的 {name}"
        want = hashlib.sha1(target.read_bytes()).hexdigest()[:8]
        assert ver == want, (
            f"{name} 的 ?v={ver} 与内容哈希 {want} 不符。\n"
            "改了前端资源就要跑：./.venv/Scripts/python.exe scripts/bump_frontend_version.py"
        )
