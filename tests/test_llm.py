import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest
from pydantic import BaseModel

from app.core.llm import LLMGateway, extract_json


def test_extract_json_from_fenced_block():
    text = '```json\n{"a": 1}\n```'
    assert extract_json(text) == {"a": 1}


def test_extract_json_from_plain():
    assert extract_json('{"a": 2}') == {"a": 2}


def test_extract_json_from_trailing_text():
    assert extract_json('结果如下：{"a": 3} 完') == {"a": 3}


def test_extract_json_raises_for_garbage():
    with pytest.raises(ValueError):
        extract_json("nothing here")


class _ScriptedServer(BaseHTTPRequestHandler):
    """按预设顺序回话的 mock OpenAI 兼容端点，记录每次收到的请求体。

    ``script`` / ``calls`` 必须是**类属性**：HTTPServer 每个请求都重新实例化
    handler，只有类上那份是共享的 —— 测试也是直接写在类上。
    """

    script: ClassVar[list[dict]] = []
    calls: ClassVar[list[dict]] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.calls.append(json.loads(self.rfile.read(length) or b"{}"))
        content = json.dumps(self.script[len(self.calls) - 1], ensure_ascii=False)
        payload = json.dumps(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # 安静跑，别把访问日志糊进测试输出
        pass


class _Out(BaseModel):
    prose: str
    mood: str


def test_complete_json_retries_once_with_feedback_when_shape_is_wrong():
    """首次回包结构不对（缺 prose）时，网关必须带着反馈重试一次并成功。

    这条覆盖的是 complete_json 的「反馈重试」环——与 test_llm_retry.py 里那批
    传输层重试（SDK max_retries / 退避 / trace）是不同机制，那个文件管不到这里。
    """
    bad: dict = {"prose": {"nested": "不是字符串"}, "mood": "平静"}
    good = {"prose": "朱明踩灭烟头，问你吃饭了没。", "mood": "戒备"}
    _ScriptedServer.script = [bad, good]
    _ScriptedServer.calls = []

    server = HTTPServer(("127.0.0.1", 0), _ScriptedServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        gw = LLMGateway(
            base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
            api_key="test",
            max_concurrency=1,
        )
        out = asyncio.run(gw.complete_json([{"role": "user", "content": "写正文"}], _Out, model="fake"))
    finally:
        server.shutdown()

    assert out["prose"] == good["prose"]
    assert len(_ScriptedServer.calls) == 2, f"应恰好重试一次，实际发了 {len(_ScriptedServer.calls)} 次"
    retry_messages = _ScriptedServer.calls[1]["messages"]
    roles = [m["role"] for m in retry_messages]
    # 反馈的形状：把模型上一轮的坏输出作为 assistant 消息回灌，再追加一条纠正指令。
    assert "assistant" in roles and roles[-1] == "user", retry_messages
    assert any("未通过校验" in (m.get("content") or "") for m in retry_messages), "重试未携带校验反馈"