"""Verify the feedback-retry loop in LLMGateway.complete_json without a real LLM.

A local mock OpenAI-compatible server answers the first call with the wrong
shape ({"directives": []}) and the second call with valid JSON; the gateway
must retry with feedback and succeed on attempt 2.
"""
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel

from app.core.llm import LLMGateway

CALLS: list[dict] = []
BAD = {"directives": []}
GOOD = {"prose": "朱明踩灭烟头，问你吃饭了没。", "time_hint": None}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        CALLS.append(body)
        n = len(CALLS)
        content = json.dumps(BAD, ensure_ascii=False) if n == 1 else json.dumps(GOOD, ensure_ascii=False)
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

    def log_message(self, *args):  # silence
        pass


class Out(BaseModel):
    prose: str
    time_hint: dict | None = None


async def main() -> None:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        gw = LLMGateway(base_url=f"http://127.0.0.1:{port}/v1", api_key="test", max_concurrency=1)
        out = await gw.complete_json([{"role": "user", "content": "写正文"}], Out, model="fake")
        assert out["prose"].startswith("朱明"), out
        assert len(CALLS) == 2, f"expected exactly 2 calls, got {len(CALLS)}"
        # The retry must carry feedback: assistant bad output + correction request.
        second = CALLS[1]["messages"]
        roles = [m["role"] for m in second]
        assert "assistant" in roles and roles[-1] == "user", second
        has_feedback = any("未通过校验" in (m.get("content") or "") for m in second)
        assert has_feedback, "retry message did not contain feedback"
        print("FEEDBACK RETRY OK: 2 calls, second one carries the bad output + correction")
        print("usage:", gw.get_usage())
    finally:
        server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())