"""把 pytest 的失败摘录成 GitHub Actions annotation —— CI 红了必须能看见原因。

背景：Actions 的**私有日志**要 admin 权限才能下载（`/actions/jobs/{id}/logs` 返回
403），而 check-run 的 `annotations` 是**公开可读**的。于是 master 上的 `test` job
连续红了 20+ 次，每次能看到的只有一句 `Process completed with exit code 1.`——
等于没有信息，谁也没法从 CI 本身定位问题（本地同一条命令全绿，两边差在哪只能靠猜）。
这个脚本把有用的那几行（失败汇总 + 首个 traceback + 本次装到的依赖版本）用
`::error::` 打出去，问题就自解释了。

用法::

    python .github/scripts/annotate_pytest.py pytest.log --env pip-freeze.txt

三条硬约束（都踩过）：

1. **只发一条 annotation**：GitHub 每步最多 10 条，合并成一条就不用数数。
2. 消息里的 ``%`` / 回车 / 换行必须转义成 ``%25`` / ``%0D`` / ``%0A``，
   否则第二个换行之后的内容会被整段吃掉。
3. 单条上限 64KB；这里按**转义前**截到 12KB，留足余量。
"""

from __future__ import annotations

import pathlib
import re
import sys

#: 转义前的正文上限（转义后约 1.3 倍，离 64KB 很远）。
MAX_RAW = 12_000
#: 依赖清单只留头部，免得把 40 个包全灌进去。
MAX_ENV = 2_500
#: 首个 traceback 的截断长度。
MAX_DETAIL = 4_000

_HEAD = re.compile(r"^=+ .+ =+$")


def _escape(text: str) -> str:
    """转义 workflow command 的消息体（顺序不能反：先转 `%`）。"""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _blocks(log: str) -> list[tuple[str, str]]:
    """按 pytest 的 ``===== 标题 =====`` 分节，返回 (标题, 正文)。"""
    sections: list[tuple[str, list[str]]] = []
    for line in log.splitlines():
        if _HEAD.match(line):
            sections.append((line.strip("= "), []))
        elif sections:
            sections[-1][1].append(line)
    return [(title, "\n".join(body).strip()) for title, body in sections]


def _pick(blocks: list[tuple[str, str]], prefix: str) -> str:
    return next((body for title, body in blocks if title.startswith(prefix)), "")


def _excerpt(log: str) -> str:
    blocks = _blocks(log)
    parts: list[str] = []

    # 1) 失败汇总：一行一个用例，带异常信息 —— 信息密度最高的一段。
    summary = _pick(blocks, "short test summary info")
    if not summary:
        summary = "\n".join(ln for ln in log.splitlines() if ln.startswith(("FAILED ", "ERROR ")))
    if summary:
        parts.append("【失败汇总】\n" + summary)

    # 2) 首个 traceback：断言那几行在哪。
    for prefix in ("FAILURES", "ERRORS"):
        detail = _pick(blocks, prefix)
        if detail:
            parts.append(f"【首个 {prefix}】\n{detail[:MAX_DETAIL]}")
            break

    # 3) pytest 根本没跑起来（用法错误 / 收集期就崩）时，上面两段都不存在，只能给尾部。
    #    有上面两段时**不再附尾部**——它俩已经把同样的内容覆盖了，附上去只是让消息翻倍。
    if not parts:
        parts.append("pytest 没能输出失败汇总，原始输出尾部：\n" + log.strip()[-6_000:])
    return "\n\n".join(parts)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法: annotate_pytest.py <pytest.log> [--env <pip-freeze.txt>]", file=sys.stderr)
        return 2

    log = pathlib.Path(argv[1]).read_text(encoding="utf-8", errors="replace")
    message = _excerpt(log)

    if "--env" in argv:
        env_path = pathlib.Path(argv[argv.index("--env") + 1])
        if env_path.is_file():
            frozen = env_path.read_text(encoding="utf-8", errors="replace").strip()
            header = f"【本次环境】python {sys.version.split()[0]} on {sys.platform}"
            message += f"\n\n{header}\n{frozen[:MAX_ENV]}"

    print(f"::error::{_escape(message[:MAX_RAW])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
