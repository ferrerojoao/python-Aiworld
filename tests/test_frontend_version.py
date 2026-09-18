"""守住 `?v=` 这条不变量：它必须等于被引用文件的**内容哈希**。

背景：`?v=NN` 原先是手改的，改了 `app.js` 忘了跳号 → 浏览器继续跑旧 JS，用户看到的
是"改了没变"（踩过多次）。现在版本号由文件内容决定，这个测试就是那道闸：改了前端
没跑脚本 → 这里直接红，并告诉你跑哪个命令。

哈希算法**只有一份**：`scripts.bump_frontend_version.content_hash`。这里不重写——
2026-09-18 的 CI 事故就是"判据侧抄了第二份、两边对行尾处理不一致"造成的：本机
`core.autocrlf=true` 算的是 CRLF 的哈希，Linux CI 检出 LF 后算出另一个值，
`test` job 从引入内容哈希那天起一直红，而本机 `--check` 永远绿。
"""
from __future__ import annotations

import re
from pathlib import Path

from scripts.bump_frontend_version import content_hash

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
        want = content_hash(target)
        assert ver == want, (
            f"{name} 的 ?v={ver} 与内容哈希 {want} 不符。\n"
            "改了前端资源就要跑：./.venv/Scripts/python.exe scripts/bump_frontend_version.py"
        )


def test_content_hash_ignores_line_endings(tmp_path: Path) -> None:
    """回归守卫：同一个文件的 CRLF 版与 LF 版必须算出**同一个**哈希。

    这条是 2026-09-18 CI 事故的直接产物。判据必须与"文件是哪台机器检出的"无关，
    否则 `?v=` 只在算它的那台机器上成立。
    """
    lf = tmp_path / "lf.js"
    crlf = tmp_path / "crlf.js"
    lf.write_bytes(b"const a = 1;\nconst b = 2;\n")
    crlf.write_bytes(b"const a = 1;\r\nconst b = 2;\r\n")
    assert content_hash(lf) == content_hash(crlf), "行尾不同不该改变内容哈希"

    # 真改了内容就必须变——否则上面那条会退化成"恒等断言"
    changed = tmp_path / "changed.js"
    changed.write_bytes(b"const a = 1;\r\nconst b = 3;\r\n")
    assert content_hash(changed) != content_hash(crlf), "内容真变了哈希必须跟着变"
