#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最小 MCP Server 实验 —— 纯标准库实现 JSON-RPC over stdio。

## 为什么值得手写一遍

计划 §11.3 把"MCP Server 完整实现"从 8 周冲刺里砍掉了，只保留"能讲清概念"。
这个判断在当时是合理的（时间要留给项目），但**MCP 已经变成很多 Agent 岗 JD 的显式要求**，
而"能讲清概念"和"写过一个"在面试里是两个档位 ——
前者答的是"我知道 MCP 是什么"，后者答的是"我踩过 stdio 分帧的坑"。

手写一遍的成本约 3 小时。它换来的东西很具体：
  - 能解释 MCP 和普通 function calling 的区别（协议层 vs 单次调用）
  - 知道 stdio 传输为什么要按行分帧、为什么不能写日志到 stdout
  - 知道 initialize 握手为什么必须协商协议版本和能力
  - 能在面试里说"我实现过 initialize / tools/list / tools/call"

## 这个 Server 提供什么工具

刻意选**无副作用、可断言**的三个，而不是"读文件""执行命令"：

  - `echo`  回显，用来验证协议通路
  - `now`   返回时间，用来验证"工具返回值不是模型编的"
  - `calc`  四则运算，用来验证参数校验与错误处理

**故意不做 `read_file` / `run_command`。** MCP 的真实安全风险就来自这类工具：
模型被注入诱导后会调用它们，而 Server 通常以当前用户权限运行。
在只有一个学习项目的仓库里留一个"能读任意文件"的 MCP 工具，是给自己埋雷。

## 协议要点（手写之后才真正记住的）

1. **stdio 传输按行分帧**：一条 JSON-RPC 消息占一行，`\n` 结尾，不能带原始换行。
2. **stdout 是协议通道**：任何 `print` 到 stdout 的调试信息都会破坏协议。
   所有日志必须走 **stderr**。这是新手最容易踩的坑，没有之一。
3. **initialize 是必须的握手**：客户端先发，Server 回 capabilities 与 serverInfo，
   之后才允许 tools/list / tools/call。
4. **错误分两类**：协议层错误走 JSON-RPC 的 `error` 字段（如方法不存在 → -32601）；
   业务层错误（工具内部失败）应该返回正常的 `result` 里带 `isError: true`。
   搞混了会让客户端的错误处理逻辑完全失效。
5. **通知没有 id**：`notifications/initialized` 这类消息 Server 不该回应。

运行（正常由 MCP 客户端拉起，不手动跑）：
    python mcp_server.py

自测：
    python tests/test_mcp_server.py      # 或 pytest
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

# 协议版本。2024-11-05 是广泛支持的版本；真实客户端会带自己的版本过来，
# 我们只做"能接受就接受"的宽松协商 —— 教学实现不追求严格的版本协商矩阵。
PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "ai-lab-minimal", "version": "0.1.0"}

# JSON-RPC 标准错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def log(*args: Any) -> None:
    """
    所有日志走 stderr。

    **这是本文件最重要的一行注释**：stdout 是 JSON-RPC 的传输通道，
    往里写任何非协议内容都会让客户端解析失败。症状通常是客户端报
    "Unexpected token x in JSON"，而你会以为是自己的 JSON 拼错了。
    """
    print(*args, file=sys.stderr, flush=True)


# ----------------------------------------------------------------------
# 工具定义
# ----------------------------------------------------------------------
TOOLS: list[dict] = [
    {
        "name": "echo",
        "description": "原样返回输入文本。用于验证协议通路是否正常。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要回显的文本"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "now",
        "description": "返回当前 UTC 时间（ISO 8601）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "calc",
        "description": "计算一个只含数字与 + - * / ( ) 的算术表达式。不做 eval。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "算术表达式，例如 (1+2)*3",
                },
            },
            "required": ["expression"],
        },
    },
]


class ToolError(Exception):
    """工具业务错误。会被转成 isError: true 的正常结果，而不是 JSON-RPC error。"""


def tool_echo(args: dict) -> str:
    if "text" not in args:
        raise ToolError("缺少参数 text")
    return str(args["text"])


def tool_now(_args: dict) -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# 只允许这些字符 —— 一个真实的算术表达式求值器，而不是 eval()。
#
# 为什么不用 eval：MCP 工具的参数是**模型生成的**。用 eval 等于把模型（以及
# 任何能影响模型输出的输入，比如被注入的文档）直接接到解释器上。
# 这是提示词注入最常见的落地方式。宁可多写 20 行解析，也不 eval。
_ALLOWED_CHARS = set("0123456789.+-*/() \t")


def _safe_eval(expr: str) -> float:
    """把算术表达式解析成 AST 后求值，只支持四则运算与括号。"""
    import ast

    if not expr or not set(expr) <= _ALLOWED_CHARS:
        raise ToolError(f"表达式含有不允许的字符：{expr!r}")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"表达式语法错误：{exc.msg}") from exc

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp):
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise ToolError("除数为 0")
                return left / right
            raise ToolError("只支持 + - * /")
        if isinstance(node, ast.UnaryOp):
            operand = ev(node.operand)
            if isinstance(node.op, ast.USub):
                return -operand
            if isinstance(node.op, ast.UAdd):
                return operand
            raise ToolError("只支持一元 + / -")
        # 走到这里说明出现了 Name/Call/Attribute 等 —— 一律拒绝
        raise ToolError(f"不支持的表达式节点：{type(node).__name__}")

    return ev(tree)


def tool_calc(args: dict) -> str:
    expr = args.get("expression")
    if not isinstance(expr, str):
        raise ToolError("缺少参数 expression（字符串）")
    import ast
    result = _safe_eval(expr)
    # 整数就不显示小数点
    if result == int(result):
        return f"{expr} = {int(result)}"
    return f"{expr} = {result}"


TOOL_HANDLERS = {"echo": tool_echo, "now": tool_now, "calc": tool_calc}


# ----------------------------------------------------------------------
# JSON-RPC 处理
# ----------------------------------------------------------------------
class Server:
    """一个最小的 MCP Server 状态机。"""

    def __init__(self) -> None:
        self.initialized = False

    def handle(self, message: dict) -> dict | None:
        """
        处理一条消息，返回要发回的响应（通知则返回 None）。

        注意返回值语义：None = 不需要响应（通知）。
        把通知也回一条响应，会让严格的客户端报协议错误。
        """
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}

        # 通知（没有 id）不回应
        is_notification = "id" not in message

        if method == "initialize":
            self.initialized = True
            client_version = params.get("protocolVersion") or PROTOCOL_VERSION
            log(f"[mcp] initialize from {params.get('clientInfo')}, "
                f"client protocol={client_version}")
            return self._ok(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            })

        if method == "notifications/initialized":
            log("[mcp] 客户端确认初始化完成")
            return None

        if method in ("tools/list", "tools/call") and not self.initialized:
            # 真实实现里这一步很重要：没握手就用工具，说明客户端有问题
            return self._err(msg_id, INVALID_REQUEST, "尚未 initialize")

        if method == "tools/list":
            return self._ok(msg_id, {"tools": TOOLS})

        if method == "tools/call":
            return self._call_tool(msg_id, params)

        if method == "ping":
            return self._ok(msg_id, {})

        if is_notification:
            log(f"[mcp] 忽略未知通知：{method}")
            return None
        return self._err(msg_id, METHOD_NOT_FOUND, f"未知方法：{method}")

    def _call_tool(self, msg_id: Any, params: dict) -> dict:
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            # 工具不存在属于业务错误还是协议错误？
            # 规范建议返回 isError 的正常结果，让模型能看到"这个工具不存在"并换一个。
            return self._ok(msg_id, {
                "content": [{"type": "text",
                             "text": f"没有名为 {name!r} 的工具。可用工具："
                                     f"{', '.join(TOOL_HANDLERS)}"}],
                "isError": True,
            })
        try:
            text = handler(args)
            log(f"[mcp] tools/call {name} -> ok")
            return self._ok(msg_id, {"content": [{"type": "text", "text": text}]})
        except ToolError as exc:
            # 业务错误：作为正常结果返回，模型可以据此自我修正。
            # 这正是 MCP 工具和"函数抛异常"的区别 —— 错误信息是给模型看的。
            log(f"[mcp] tools/call {name} -> tool error: {exc}")
            return self._ok(msg_id, {
                "content": [{"type": "text", "text": f"工具执行失败：{exc}"}],
                "isError": True,
            })
        except Exception as exc:  # noqa: BLE001 - 兜底，避免 Server 崩掉
            log(f"[mcp] tools/call {name} -> unexpected: {exc!r}")
            return self._err(msg_id, INTERNAL_ERROR, f"内部错误：{exc}")

    @staticmethod
    def _ok(msg_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _err(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": code, "message": message}}


def serve(stdin=None, stdout=None) -> int:
    """主循环：按行读 JSON-RPC，处理，按行写回。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    server = Server()
    log(f"[mcp] {SERVER_INFO['name']} 启动，等待 stdio 上的 JSON-RPC")

    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            # 解析失败也要回响应：协议层错误
            _write(stdout, {"jsonrpc": "2.0", "id": None,
                            "error": {"code": PARSE_ERROR,
                                      "message": f"JSON 解析失败：{exc.msg}"}})
            continue

        if not isinstance(message, dict) or "method" not in message:
            _write(stdout, {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                            "error": {"code": INVALID_REQUEST, "message": "不是合法的 JSON-RPC 请求"}})
            continue

        response = server.handle(message)
        if response is not None:
            _write(stdout, response)

    log("[mcp] stdin 关闭，退出")
    return 0


def _write(stdout, payload: dict) -> None:
    """写一条消息。必须单行 + 立即 flush —— 否则客户端会一直等。"""
    stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stdout.flush()


if __name__ == "__main__":
    raise SystemExit(serve())
