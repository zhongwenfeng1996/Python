#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最小 MCP 客户端 —— 用标准库把 Server 拉起来并走一遍完整协议。

为什么客户端也要自己写：
    直接"用 Claude Desktop 连上看能不能跑"验证不了任何细节 ——
    握手成功/工具返回错/参数校验失败，在 UI 上都长得差不多。
    自己写客户端才能断言每一条协议消息的内容。

这也是把 MCP 讲清楚的最短路径：写完这 100 行，你就知道
"MCP 和 function calling 的区别"答案是**传输与协商**，而不是"多了一层封装"。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent / "mcp_server.py"
PROTOCOL_VERSION = "2024-11-05"


class MCPClient:
    """
    一次请求一条响应（同步）的最小实现。

    真实客户端要处理：服务端主动发起的请求（sampling / roots）、
    通知、并发请求（靠 id 配对）、超时。这里不实现 —— 学习项目不需要，
    而且会让代码从 100 行涨到 500 行，把协议本身淹没掉。
    """

    def __init__(self, timeout: float = 10.0) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        self._next_id = 0
        self.timeout = timeout

    # ---------- 底层收发 ----------
    def _send(self, payload: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _recv(self) -> dict:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line:
            stderr = ""
            if self.proc.stderr is not None:
                stderr = self.proc.stderr.read()
            raise RuntimeError(f"MCP Server 没有响应就退出了。stderr:\n{stderr}")
        return json.loads(line)

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        msg_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method,
                    "params": params or {}})
        response = self._recv()
        # id 必须配对 —— 不校验的话，并发场景下会静默串答复
        if response.get("id") != msg_id:
            raise RuntimeError(f"响应 id 不匹配：期望 {msg_id}，得到 {response.get('id')}")
        return response

    def notify(self, method: str, params: dict | None = None) -> None:
        """通知：没有 id，Server 不该回响应。"""
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ---------- 高层封装 ----------
    def initialize(self) -> dict:
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "ai-lab-test-client", "version": "0.1.0"},
        })
        self.notify("notifications/initialized")
        return result["result"]

    def list_tools(self) -> list[dict]:
        return self.request("tools/list")["result"]["tools"]

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        return self.request("tools/call",
                            {"name": name, "arguments": arguments or {}})["result"]

    def text_of(self, result: dict) -> str:
        return "".join(part.get("text", "") for part in result.get("content", []))

    # ---------- 生命周期 ----------
    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            self.proc.kill()

    def __enter__(self) -> "MCPClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ----------------------------------------------------------------------
# 演示：python mcp_client.py
# ----------------------------------------------------------------------
def demo() -> int:
    """
    走一遍完整协议并打印**原始消息**。

    看原始 JSON-RPC 是理解 MCP 最快的办法 —— 比读规范快，
    也比看任何教程快，因为你能直接看到"到底在传什么"。
    """
    def show(direction: str, payload: dict) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) > 200:
            text = text[:200] + f"...（共 {len(text)} 字符）"
        print(f"  {direction} {text}")

    print("=" * 78)
    print("  MCP 最小实现 · 协议演示")
    print("=" * 78)
    print("  提示：Server 的日志走 stderr，会混在下面输出里；")
    print("        stdout 只有 JSON-RPC —— 这就是 stdio 传输的分帧约定。")
    print()

    with MCPClient() as c:
        print("—— 1) 握手 ——")
        c._next_id += 1
        c._send({"jsonrpc": "2.0", "id": c._next_id, "method": "initialize",
                 "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                            "clientInfo": {"name": "demo", "version": "1.0"}}})
        show("→", {"method": "initialize", "params": {"protocolVersion": PROTOCOL_VERSION}})
        init = c._recv()
        show("←", init)
        c.notify("notifications/initialized")
        print("  → notifications/initialized（无 id，Server 不回应）")
        print()

        print("—— 2) 列工具 ——")
        tools = c.list_tools()
        for t in tools:
            print(f"  {t['name']:<6} {t['description']}")
        print()

        print("—— 3) 调工具 ——")
        for name, args in [
            ("echo", {"text": "你好 MCP"}),
            ("now", {}),
            ("calc", {"expression": "(1+2)*3"}),
        ]:
            result = c.call_tool(name, args)
            print(f"  {name:<6} {args}  ->  {c.text_of(result)}")
        print()

        print("—— 4) 错误处理（重点看区别）——")
        # 业务错误：正常 result + isError，模型能看到内容
        r1 = c.call_tool("calc", {"expression": "1/0"})
        print(f"  业务错误  除零            -> isError={r1['isError']} "
              f"| {c.text_of(r1)}")
        r2 = c.call_tool("calc", {"expression": "__import__('os').system('x')"})
        print(f"  安全拒绝  注入 payload    -> isError={r2['isError']} "
              f"| {c.text_of(r2)[:60]}...")
        r3 = c.request("tools/call", {"name": "no_such_tool", "arguments": {}})
        print(f"  未知工具  no_such_tool    -> isError="
              f"{r3['result']['isError']} | {c.text_of(r3['result'])[:60]}...")
        # 协议错误：走 JSON-RPC error，模型看不到
        r4 = c.request("no/such/method")
        print(f"  协议错误  未知方法        -> error.code={r4['error']['code']} "
              f"| {r4['error']['message']}")
        print()

        print("—— 5) 结论 ——")
        print("  业务错误 → result.isError=true → 内容会喂回模型 → 模型能自我修正")
        print("  协议错误 → error 字段         → 只有客户端代码能看到 → 模型不知道")
        print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(demo())
