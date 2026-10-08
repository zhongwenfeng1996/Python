"""
pytest 公共夹具。

测试形态：**真进程 + 真 HTTP**，不用 mocks 打桩。
  mock 上游（stdlib http.server，子进程）→ FastAPI 应用（uvicorn，子进程）→ httpx 客户端

为什么不用 ASGI 内存传输（asgi-lifespan + httpx.ASGITransport）：
  那种方式更快，但它不经过真实 socket，测不出"客户端断开后上游有没有被关掉"
  —— 而"中断不残留后台请求"正是本项目的一条验收项。
  慢一点换真实覆盖，这个取舍在项目一的规模下是划算的。
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from mock_runner import start_mock, stop_mock  # noqa: E402

MOCK_PORT = 8765
APP_PORT = 8766


def _port_open(port: int, timeout: float = 0.3) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_open(port):
            return True
        time.sleep(0.1)
    return False


@pytest.fixture(scope="session")
def mock_upstream():
    """会话级：整个测试过程共用一个 mock 上游（省掉反复启动的开销）。"""
    proc = start_mock(port=MOCK_PORT, delay=0.0, slow_first_token=0.02)
    yield f"http://127.0.0.1:{MOCK_PORT}/v1"
    stop_mock(proc)


@pytest.fixture(scope="session")
def app_server(mock_upstream):
    """
    启动被测应用。

    OPENAI_BASE_URL 覆盖所有模型的 base_url，指向本地 mock ——
    这样**不需要任何真实 API Key** 就能跑完整链路，也就意味着
    这套测试在任何机器、任何 CI 上都能跑（这是能持续跑下去的测试的前提）。
    CHAT_TIMEOUT_S 压到 5s，避免出问题时测试挂 60 秒。
    """
    import os
    env = dict(os.environ)
    env.update({
        "OPENAI_BASE_URL": mock_upstream,
        "OPENAI_API_KEY": "test",
        "CHAT_TIMEOUT_S": "5",
        "FIRST_TOKEN_TIMEOUT_S": "2",
        "PYTHONUTF8": "1",
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(APP_PORT), "--log-level", "warning"],
        cwd=str(PROJECT_ROOT / "backend"),
        env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if not _wait_port(APP_PORT):
        proc.terminate()
        raise RuntimeError("应用未能在 15s 内启动（依赖装了吗？见 README）")
    yield f"http://127.0.0.1:{APP_PORT}"
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
async def client(app_server):
    async with httpx.AsyncClient(base_url=app_server, timeout=30) as c:
        yield c


@pytest.fixture
async def mock_control(mock_upstream):
    """
    访问 mock 上游的 /stats 与 /reset。

    每次测试前清零，这样"这次测试总共发了几个请求"是确定的 ——
    否则会话级共享的 mock 会让计数带上前面测试的噪音。
    """
    base = mock_upstream.replace("/v1", "")
    async with httpx.AsyncClient(base_url=base, timeout=10) as c:
        await c.get("/reset")

        class Control:
            async def stats(self) -> dict:
                return (await c.get("/stats")).json()

            async def reset(self) -> None:
                await c.get("/reset")

            async def set_fail_first(self, n: int) -> None:
                """让接下来 n 个请求返回 500，用于测试自动降级。"""
                await c.get(f"/set-fail-first?n={n}")

        control = Control()
        await control.set_fail_first(0)
        yield control
        # 测试结束后复位，避免污染后面的用例
        await control.set_fail_first(0)
