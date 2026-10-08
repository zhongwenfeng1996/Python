"""
一键重启本地 mock 服务（给验收测试用）。

单独放一个脚本，而不是塞进测试文件里，原因是：
“起服务 → 等端口就绪 → 跑测试 → 关服务”这段样板逻辑在每个项目里都要写一遍，
把它抽出来之后，测试文件只关心断言。
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MOCK = REPO_ROOT / "week01" / "mock_server.py"


def port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def wait_for_port(port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open("127.0.0.1", port):
            return True
        time.sleep(0.1)
    return False


def start_mock(port: int = 8765, delay: float = 0.0,
               slow_first_token: float = 0.05) -> subprocess.Popen:
    """起 mock 服务并等到端口就绪。delay=0 让它尽快把内容推完，加快测试。"""
    proc = subprocess.Popen(
        [sys.executable, str(MOCK), "--port", str(port),
         "--delay", str(delay), "--slow-first-token", str(slow_first_token)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if not wait_for_port(port):
        proc.terminate()
        raise RuntimeError(f"mock 服务未能在 10s 内监听 {port}")
    return proc


def stop_mock(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


if __name__ == "__main__":
    p = start_mock()
    print(f"mock 已启动 pid={p.pid} 端口=8765，Ctrl+C 停止")
    try:
        p.wait()
    except KeyboardInterrupt:
        stop_mock(p)
