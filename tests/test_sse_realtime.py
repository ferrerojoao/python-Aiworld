"""SSE 实时性守卫（P1-5，2026-09-17）。

这个文件补的是 TestClient 结构上验不了的那半条不变量。

背景：2026-09-17 清理脚本时删掉了 `verify_sse.py`（它指向早已消失的世界名），
其中**有真价值的那半条**断言——"阶段速报先到、终态候选后到"——被提升进了
`tests/test_api.py`。但那条只能验**事件顺序**：TestClient 会把整条流缓冲下来，
所以"stage 有没有在候选算完之前就送到浏览器"这件事，它**在结构上看不见**。
（httpx 的 ASGITransport 也一样，它同样收完整个 body 才返回。）

于是这里起一个**真 uvicorn**（真 socket、chunked 传输），用 httpx 逐块读，
记录每个事件的到达时刻，再断言两者之间确实存在时间差。

造时间差的办法：`_write_turn` 在调编剧之前就先发一条 `("writer", "编排成文中")`
（`app/runtime/turn.py` 里的 `_progress`），所以只要把**第一次** LLM 调用拖慢，
就必然出现「stage 早到 / candidate 晚到」。若整条流被缓冲到最后一次性发出，
两个时刻会几乎相同 → 差值趋于 0 → 断言必红。这就是这条守卫的判别力来源。
"""

from __future__ import annotations

import asyncio
import re
import shutil
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from app.config import Settings
from app.core.llm import FakeLLM
from app.main import create_app
from tests.conftest import GENERIC_LLM_RESPONSE, WORLD_ROOT

#: 第一次 LLM 调用（编剧）的假耗时。取得够大，让时间差远超机器抖动。
WRITER_DELAY = 1.5
#: 断言余量。缓冲成一次到达时差值≈0，不可能越过这条线。
MARGIN = 0.8

_EVENT_RE = re.compile(r"^event: (\w+)", re.MULTILINE)


class _SlowFirstCallLLM(FakeLLM):
    """只把**第一次**调用拖慢：那次正好在第一条 stage 之后、candidate 之前。"""

    def __init__(self, responses: dict, delay: float):
        super().__init__(responses)
        self._delay = delay
        self.call_count = 0

    async def complete_json(self, messages, schema, *, model="fake", temperature=0.2, worker=""):
        # worker（角色 → 主 / 辅出口）必须原样转发：TraceRecorder 现在总会带上它，
        # 本类 override 少了这个参数就会 TypeError，而报错形态是"回合失败"、
        # 不是"签名不对"——2026-09-20 拆出口时实测踩到。
        self.call_count += 1
        if self.call_count == 1:
            await asyncio.sleep(self._delay)
        return await super().complete_json(
            messages, schema, model=model, temperature=temperature, worker=worker
        )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live_server(tmp_path):
    """真 uvicorn 跑在真端口上（后台线程），yield base_url。"""
    world_root = tmp_path / "qinghsi"
    shutil.copytree(WORLD_ROOT, world_root, ignore=shutil.ignore_patterns("saves"))
    settings = Settings(
        content_root=tmp_path,
        data_dir=tmp_path / "data",
        candidate_ttl_days=7,
    )
    app = create_app(
        settings=settings,
        llm=_SlowFirstCallLLM({"*": GENERIC_LLM_RESPONSE}, WRITER_DELAY),
    )

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 20
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn 20s 内没起来")
        time.sleep(0.02)

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def test_stage_reaches_browser_long_before_the_candidate(live_server):
    """不变量：阶段速报必须在候选算完**之前**送达，而不是攒到最后一起发。"""
    with httpx.Client(timeout=60.0) as client:
        created = client.post(
            f"{live_server}/api/sessions",
            json={"world_id": "qinghsi", "save_name": "main"},
        )
        assert created.status_code == 200, created.text
        sid = created.json()["sid"]

        arrivals: list[tuple[str, float]] = []
        started = time.monotonic()
        buffer = ""
        with client.stream(
            "POST",
            f"{live_server}/api/sessions/{sid}/turn",
            json={"input": "去网吧找朱明"},
        ) as response:
            assert response.status_code == 200, response.read()
            for chunk in response.iter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    hit = _EVENT_RE.match(block)
                    if hit:
                        arrivals.append((hit.group(1), time.monotonic() - started))

    kinds = [kind for kind, _ in arrivals]
    assert "stage" in kinds, f"没收到任何阶段速报：{kinds}"
    assert kinds[-1] == "candidate", f"终态事件必须压轴：{kinds}"

    first_stage_at = next(at for kind, at in arrivals if kind == "stage")
    candidate_at = arrivals[-1][1]
    gap = candidate_at - first_stage_at
    assert gap > MARGIN, (
        f"阶段速报到终态候选只差 {gap:.3f}s（编剧假耗时 {WRITER_DELAY}s）——"
        "整条流像是被缓冲到最后一次性发出，前端的「正在连接…」会一直空转。"
        f"到达记录={[(k, round(t, 3)) for k, t in arrivals]}"
    )
