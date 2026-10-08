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
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = "这是一段来自本地 mock 服务的回复，用于验证你的代码是否正确解析了响应。"

# 温度敏感回复：模拟"同一个问题在不同 temperature 下回答不同"。
#
# 为什么 mock 需要这个：评估里有一条重要指标是**输出的稳定性**（面试第一题就是
# "同一个问题问两次答案不同，是 bug 吗"）。如果 mock 永远返回同一句话，
# 那条指标测出来永远是 100%，等于没测。
#
# 设计成**确定性**的（同一 temperature → 同一答案），而不是随机：
#   评估脚本要可复现。用 random 会让"这次 72%、下次 68%"，
#   你分不清是代码变了还是运气变了 —— 那就不是评估。
#   真实模型的随机性本来就是"按 temperature 采样"，这里用温度分档近似它。
TEMP_REPLIES = {
    0: "确定性回答：向量是数学对象，表示空间中的方向和大小。",
    1: "向量就是带方向和大小的量，可以理解成一支箭头。",
    2: "向量嘛，你可以想象成一支有长度有方向的箭；在不同语境下含义会变，"
       "线性代数里它是空间中的元素，机器学习里它是特征的数值表示。",
}


def reply_for(payload: dict) -> str:
    """按 temperature 选回复，让"稳定性"可被测量。"""
    try:
        temp = float(payload.get("temperature", 0.7))
    except (TypeError, ValueError):
        temp = 0.7
    if temp <= 0.1:
        return TEMP_REPLIES[0]
    if temp <= 0.8:
        return TEMP_REPLIES[1]
    return TEMP_REPLIES[2]


# 抖动模式：每次回复追加一个随机串，模拟"真实模型每次回答都不一样"。
#
# 为什么需要它：评估脚本里有个"答案稳定性"指标。默认的 mock 是**确定性**的
# （同一 temperature 永远同一答案），于是稳定性永远 100% —— 这个指标就无法证伪。
# 一个测不出问题的指标等于没有指标。开着 --jitter 跑一次，
# 稳定性应该明显掉下来；如果没掉，说明指标写错了。
JITTER = False


def final_reply(payload: dict) -> str:
    text = reply_for(payload)
    if JITTER:
        text += f"（随机标记 {random.randint(1000, 9999)}）"
    return text


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
        reply = final_reply(payload)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": len(reply),
            "total_tokens": prompt_tokens + len(reply),
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
                    "message": {"role": "assistant", "content": final_reply(payload)},
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
        reply = final_reply(payload)
        for ch in reply:
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
    parser.add_argument("--jitter", action="store_true",
                        help="每次回复追加随机串，模拟真实模型的非确定性"
                             "（用于验证评估的'稳定性'指标真的能测出问题）")
    args = parser.parse_args()

    Handler.delay = args.delay
    Handler.slow_first_token = args.slow_first_token
    STATS.fail_first = args.fail_first
    global JITTER
    JITTER = args.jitter

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
