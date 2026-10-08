#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地 OpenAI 兼容流式端点 —— 没有 API Key 时也能验证 chat.py。

用途：
  1. 验证 SSE 解析是否正确（真实端点返回的格式和这里一致）
  2. 验证 TTFT 统计、中断处理、token 计费逻辑
  3. 以后写测试时可以当 mock server 复用

用法::

    python3 mock_server.py            # 默认监听 8765
    python3 mock_server.py --port 9000 --delay 0.05

然后另开一个终端：
    OPENAI_BASE_URL=http://127.0.0.1:8765/v1 OPENAI_API_KEY=test python3 chat.py
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

REPLY = "这是一段来自本地 mock 服务的回复，用于验证你的代码是否正确解析了响应。"


class Handler(BaseHTTPRequestHandler):
    delay = 0.02
    slow_first_token = 0.4

    def _read_payload(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(body or b"{}")
        except json.JSONDecodeError:
            return {}

    def _usage(self, payload: dict) -> dict:
        prompt_tokens = sum(
            len(m.get("content", "")) for m in payload.get("messages", [])
        ) or 1
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": len(REPLY),
            "total_tokens": prompt_tokens + len(REPLY),
        }

    def do_POST(self) -> None:  # noqa: N802
        payload = self._read_payload()

        # ---------- 非流式：一次性返回完整 JSON ----------
        if not payload.get("stream"):
            time.sleep(self.slow_first_token)
            response = {
                "id": "mock-completion",
                "object": "chat.completion",
                "model": payload.get("model", "mock-model"),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": REPLY},
                    "finish_reason": "stop",
                }],
                "usage": self._usage(payload),
            }
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # ---------- 流式：SSE ----------
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        def send(obj: dict) -> None:
            self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()

        # 故意让首个 token 慢一点，好让 TTFT 统计看得出差别
        time.sleep(self.slow_first_token)
        for ch in REPLY:
            send({"choices": [{"delta": {"content": ch}, "index": 0}]})
            time.sleep(self.delay)

        send({"choices": [], "usage": self._usage(payload)})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args) -> None:  # 静音访问日志
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="本地 OpenAI 兼容 mock 服务")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--delay", type=float, default=0.02, help="每个字符的间隔秒数")
    parser.add_argument("--slow-first-token", type=float, default=0.4)
    args = parser.parse_args()

    Handler.delay = args.delay
    Handler.slow_first_token = args.slow_first_token

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[mock] 监听 http://127.0.0.1:{args.port}/v1  （Ctrl+C 停止）")
    print(f"[mock] 用法：OPENAI_BASE_URL=http://127.0.0.1:{args.port}/v1 "
          f"OPENAI_API_KEY=test python3 chat.py")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[mock] 已停止")


if __name__ == "__main__":
    main()
