"""
MCP Server 的协议测试。

这些断言覆盖的是**协议行为**，而不是"能不能跑"：
    - 握手必须协商出 protocolVersion / capabilities / serverInfo
    - 未握手就调 tools/list 必须被拒（错误码 -32600）
    - 未知方法 → -32601；未知工具 → isError=true 的正常结果（不是协议错误）
    - 通知不该有响应（用"下一条响应仍能正确配对 id"来间接验证）
    - 参数校验：缺参数、非法字符、除零、除零以外的注入尝试
    - calc 不能是 eval：`__import__('os')` 这类必须被拒

重点在最后一类：MCP 工具的参数是**模型生成的**，
所以"工具本身安全"是 MCP 安全的第一道也是最后一道防线。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_client import MCPClient  # noqa: E402


@pytest.fixture
def client():
    with MCPClient() as c:
        c.initialize()
        yield c


# ----------------------------------------------------------------------
# 握手
# ----------------------------------------------------------------------
class TestInitialize:
    def test_initialize_returns_protocol_and_capabilities(self):
        with MCPClient() as c:
            info = c.initialize()
        assert info["protocolVersion"]
        assert "tools" in info["capabilities"]
        assert info["serverInfo"]["name"] == "ai-lab-minimal"

    def test_tools_list_rejected_before_initialize(self):
        """没握手就用工具 —— 必须拒绝，否则客户端状态机是坏的。"""
        with MCPClient() as c:
            response = c.request("tools/list")
        assert "error" in response
        assert response["error"]["code"] == -32600
        assert "initialize" in response["error"]["message"]


# ----------------------------------------------------------------------
# 协议层错误语义
# ----------------------------------------------------------------------
class TestProtocolErrors:
    def test_unknown_method_returns_method_not_found(self, client):
        response = client.request("no/such/method")
        assert response["error"]["code"] == -32601

    def test_notification_gets_no_response(self, client):
        """
        发一条通知，紧接着发一条请求。
        如果 Server 错给通知回了响应，下一条读到的就是那条多余响应，
        id 配对会失败 —— 这正是"通知无响应"的间接验证方式。
        """
        client.notify("notifications/initialized")
        client.notify("some/unknown/notification")
        result = client.list_tools()          # 若上面多回了一条，这里 id 会对不上
        assert result, "通知之后协议应能继续正常使用"

    def test_invalid_json_gets_parse_error(self):
        """直接往 stdin 写一行坏 JSON，必须回 -32700 而不是崩掉。"""
        import subprocess
        from mcp_client import SERVER
        proc = subprocess.run(
            [sys.executable, str(SERVER)],
            input='{"jsonrpc": "2.0", "id": 1, "method": broken}\n',
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        line = proc.stdout.strip().splitlines()[0]
        import json as _json
        payload = _json.loads(line)
        assert payload["error"]["code"] == -32700


# ----------------------------------------------------------------------
# 工具清单
# ----------------------------------------------------------------------
class TestToolsList:
    def test_lists_exactly_the_declared_tools(self, client):
        names = {t["name"] for t in client.list_tools()}
        assert names == {"echo", "now", "calc"}

    def test_every_tool_has_a_schema(self, client):
        for tool in client.list_tools():
            assert tool.get("description"), f"{tool['name']} 缺 description（模型靠它选工具）"
            assert tool.get("inputSchema", {}).get("type") == "object"

    def test_no_dangerous_tools_exposed(self, client):
        """
        故意暴露的能力检查：这个学习仓库不该有能读任意文件/执行命令的 MCP 工具。
        模型被注入后调用这类工具，是 MCP 最现实的攻击面。
        """
        names = {t["name"] for t in client.list_tools()}
        dangerous = {"read_file", "write_file", "run_command", "exec", "shell", "delete"}
        assert not (names & dangerous), f"暴露了危险工具：{names & dangerous}"


# ----------------------------------------------------------------------
# 工具调用
# ----------------------------------------------------------------------
class TestToolsCall:
    def test_echo_roundtrip(self, client):
        result = client.call_tool("echo", {"text": "你好 MCP"})
        assert result.get("isError") is not True
        assert client.text_of(result) == "你好 MCP"

    def test_now_returns_parseable_iso_time(self, client):
        from datetime import datetime
        result = client.call_tool("now")
        text = client.text_of(result)
        # 能解析 = 这是一个真实时间，而不是模型编的字符串
        parsed = datetime.fromisoformat(text)
        assert parsed.tzinfo is not None, "应是带时区的 UTC 时间"

    def test_calc_basic_arithmetic(self, client):
        assert client.text_of(client.call_tool("calc", {"expression": "(1+2)*3"})) == "(1+2)*3 = 9"
        assert client.text_of(client.call_tool("calc", {"expression": "7/2"})) == "7/2 = 3.5"

    def test_calc_division_by_zero_is_tool_error_not_crash(self, client):
        result = client.call_tool("calc", {"expression": "1/0"})
        assert result["isError"] is True
        assert "除数为 0" in client.text_of(result)
        # Server 必须还活着 —— 工具错误不能把进程带走
        assert client.list_tools()


class TestToolErrorsAreModelReadable:
    """
    业务错误必须走 isError=true 的正常结果，而不是 JSON-RPC error。

    为什么关键：JSON-RPC 的 error 是给**客户端程序**看的，模型看不到内容；
    而 isError 的 content 会作为工具返回值喂回模型，模型才能据此自我修正
    （"参数错了，我换个写法"）。搞混了，Agent 就无法从工具错误中恢复。
    """

    def test_missing_argument_is_iserror_result(self, client):
        result = client.call_tool("calc", {})
        assert result["isError"] is True
        assert "expression" in client.text_of(result)

    def test_unknown_tool_is_iserror_result_not_protocol_error(self, client):
        response = client.request("tools/call", {"name": "nope", "arguments": {}})
        assert "result" in response, "应是正常结果（带 isError），不是协议错误"
        assert response["result"]["isError"] is True
        # 错误信息要告诉模型有哪些可用工具，这样它才能换一个
        assert "echo" in client.text_of(response["result"])

    def test_error_message_mentions_the_problem(self, client):
        result = client.call_tool("calc", {"expression": "1+abc"})
        assert result["isError"] is True
        assert client.text_of(result)


class TestCalcIsNotEval:
    """
    安全断言：calc 绝不能变成 eval。

    MCP 工具的参数来自模型，模型又受用户输入和检索文档影响。
    一旦工具里有 eval / os.system，提示词注入就直接变成代码执行。
    """

    @pytest.mark.parametrize("payload", [
        "__import__('os').system('echo hi')",
        "open('x','w')",
        "1+1; import os",
        "__builtins__",
        "().__class__.__bases__",
        "eval('1+1')",
        "[].append(1)",
        "1 if True else 2",
    ])
    def test_injection_payloads_rejected(self, client, payload):
        result = client.call_tool("calc", {"expression": payload})
        assert result["isError"] is True, f"应拒绝：{payload}"
        # 且 Server 仍然存活可用
        assert client.list_tools()

    def test_rejected_payload_does_not_execute(self, client, tmp_path):
        """用一个会产生可见副作用的 payload 验证它真的没被执行。"""
        marker = tmp_path / "pwned.txt"
        payload = f"__import__('pathlib').Path(r'{marker}').write_text('x')"
        result = client.call_tool("calc", {"expression": payload})
        assert result["isError"] is True
        assert not marker.exists(), "注入 payload 竟然被执行了"
