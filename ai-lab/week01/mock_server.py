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
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = "这是一段来自本地 mock 服务的回复，用于验证你的代码是否正确解析了响应。"


class Stats:
    """
    统计收到的请求，供自动化测试断言"降级""中断"这类行为。

    为什么 mock 服务需要这个：
      "首 token 超时自动切备用模型"和"中断后不残留后台请求"这两条验收项，
      如果不记录请求数，就只能靠肉眼观察日志 —— 那不是测试，是许愿。
      计数之后，"降级"= 收到 2 次请求且模型名不同，
      "中断"= 请求数在客户端 abort 之后不再增长。
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[dict] = []
        self.fail_first = 0          # 前 N 个请求强制返回 500（用于测试降级）

    def record(self, model: str, stream: bool, messages: int) -> int:
        with self.lock:
            index = len(self.requests)
            self.requests.append({
                "index": index, "model": model, "stream": stream,
                "messages": messages, "at": time.time(),
            })
            return index

    def should_fail(self, index: int) -> bool:
        with self.lock:
            return index < self.fail_first

    def snapshot(self) -> dict:
        with self.lock:
            models = [r["model"] for r in self.requests]
            return {
                "count": len(self.requests),
                "models": models,
                "streams": sum(1 for r in self.requests if r["stream"]),
                "requests": list(self.requests),
            }

    def reset(self) -> None:
        with self.lock:
            self.requests.clear()


STATS = Stats()


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

    # ---------- 测试辅助端点 ----------
    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/stats":
            body = json.dumps(STATS.snapshot(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.rstrip("/") == "/reset":
            STATS.reset()
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
        elif self.path.startswith("/set-fail-first"):
            # 测试用：动态设置"前 N 个请求返回 500"。
            # 有了它，才能在**不重启服务**的前提下测"主模型失败 → 自动降级"。
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            n = int((qs.get("n") or ["0"])[0])
            STATS.fail_first = n
            body = json.dumps({"fail_first": n}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        payload = self._read_payload()
        model = payload.get("model", "mock-model")
        index = STATS.record(model, bool(payload.get("stream")),
                             len(payload.get("messages", [])))

        # 故意失败：用于验证"主模型失败 -> 自动降级到备用模型"
        if STATS.should_fail(index):
            body = json.dumps(
                {"error": {"message": f"mock 强制失败 (request #{index})",
                           "type": "server_error"}},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # ---------- 非流式：一次性返回完整 JSON ----------
        if not payload.get("stream"):
            time.sleep(self.slow_first_token)
            response = {
                "id": "mock-completion",
                "object": "chat.completion",
                "model": model,
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
            try:
                send({"choices": [{"delta": {"content": ch}, "index": 0}]})
            except (BrokenPipeError, ConnectionResetError, OSError):
                # 客户端断开了。真实服务端必须**立刻停止向上游读取**，
                # 否则就是"残留的后台请求"。这里如实模拟这个行为，
                # 好让测试能通过"mock 是否继续跑完"来间接验证取消是否传播到了上游。
                return
            time.sleep(self.delay)

        try:
            send({"choices": [], "usage": self._usage(payload)})
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def log_message(self, *args) -> None:  # 静音访问日志
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="本地 OpenAI 兼容 mock 服务")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--delay", type=float, default=0.02, help="每个字符的间隔秒数")
    parser.add_argument("--slow-first-token", type=float, default=0.4)
    parser.add_argument("--fail-first", type=int, default=0,
                        help="让前 N 个请求返回 500（用于测试自动降级）")
    args = parser.parse_args()

    Handler.delay = args.delay
    Handler.slow_first_token = args.slow_first_token
    STATS.fail_first = args.fail_first

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    print(f"[mock] 监听 http://127.0.0.1:{args.port}/v1  （Ctrl+C 停止）")
    print(f"[mock] 用法：OPENAI_BASE_URL=http://127.0.0.1:{args.port}/v1 "
          f"OPENAI_API_KEY=test python3 chat.py")
    print(f"[mock] 测试辅助：GET /stats 看请求计数，GET /reset 清零")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[mock] 已停止")


if __name__ == "__main__":
    main()
