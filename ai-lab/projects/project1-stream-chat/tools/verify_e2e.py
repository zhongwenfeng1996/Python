#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
项目一端到端验证（单进程内起服务、发请求、出报告）。

为什么单独写一个脚本，而不是只靠 pytest：
    pytest 验证的是**断言**（对/错），这个脚本输出的是**证据**（首页能不能打开、
    SSE 长什么样、TTFT 实测多少）。做项目演示和写技术文章时，
    需要的是后者 —— 一段可以直接粘出来的真实输出。

用法：
    python tools/verify_e2e.py
    python tools/verify_e2e.py --mock-port 8765 --app-port 8899

不需要任何 API Key：全程用仓库自带的 week01/mock_server.py 当模型服务。
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
REPO = PROJECT.parents[2]
MOCK = REPO / "ai-lab" / "week01" / "mock_server.py"


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def wait_port(port: int, timeout: float = 20.0,
              proc: subprocess.Popen | None = None) -> bool:
    """
    等到端口就绪。

    额外检查 proc 是否还活着 —— 这是踩过坑之后加的：
    如果端口被**别人的程序**占着（本机上 8901 被某个托盘程序占用过），
    只看端口会误判成"启动成功"，然后请求打到别人身上，
    表现为一个毫无头绪的 `WinError 10054 远程主机强迫关闭连接`。
    先确认"我们的进程活着"再看端口，能把这类问题一次性排除。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False      # 进程已退出，端口能连也没意义
        if port_open(port):
            return True
        time.sleep(0.15)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="项目一端到端验证")
    ap.add_argument("--mock-port", type=int, default=8765)
    ap.add_argument("--app-port", type=int, default=8899)
    args = ap.parse_args()

    import httpx

    print("=" * 74)
    print("  项目一 · 流式多模型对话 —— 端到端验证")
    print("=" * 74)

    mock = subprocess.Popen(
        [sys.executable, str(MOCK), "--port", str(args.mock_port),
         "--delay", "0.01", "--slow-first-token", "0.02"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    app: subprocess.Popen | None = None
    try:
        if not wait_port(args.mock_port, proc=mock):
            print("[x] mock 服务启动失败")
            return 1
        print(f"[1/5] mock 上游就绪  http://127.0.0.1:{args.mock_port}/v1")

        env = dict(__import__("os").environ)
        env.update({
            "OPENAI_BASE_URL": f"http://127.0.0.1:{args.mock_port}/v1",
            "OPENAI_API_KEY": "test",
            "PYTHONUTF8": "1",
        })
        log_path = PROJECT / "logs" / "e2e-app.err.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        err_file = log_path.open("w", encoding="utf-8")
        app = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app",
             "--host", "127.0.0.1", "--port", str(args.app_port),
             "--log-level", "info"],
            cwd=str(PROJECT / "backend"), env=env,
            stdout=err_file, stderr=subprocess.STDOUT,
        )
        if not wait_port(args.app_port, proc=app):
            err_file.flush()
            print("[x] 应用启动失败，日志如下：")
            print("-" * 74)
            try:
                print(log_path.read_text(encoding="utf-8", errors="ignore")[-3000:])
            except OSError:
                print("（读不到日志）")
            print("-" * 74)
            print("    提示：若该端口被别的程序占用，换一个端口重试，例如 --app-port 8801")
            return 1
        print(f"[2/5] 后端就绪      http://127.0.0.1:{args.app_port}/")

        base = f"http://127.0.0.1:{args.app_port}"
        with httpx.Client(base_url=base, timeout=30) as client:
            # ---- 首页 ----
            home = client.get("/")
            checks = {
                "首页返回 200": home.status_code == 200,
                "含 Vue 挂载代码": "createApp" in home.text,
                "含手写 SSE 解析": "parseSSE" in home.text,
                "含中断控制(AbortController)": "AbortController" in home.text,
            }
            print(f"[3/5] 前端页面      {home.status_code}, {len(home.content)} 字节")
            for name, ok in checks.items():
                print(f"        {'[ok]' if ok else '[FAIL]'} {name}")

            # ---- 模型列表 ----
            models = client.get("/api/models").json()
            print(f"[4/5] 模型列表      mode={models['mode']} "
                  f"共 {len(models['models'])} 个，"
                  f"可用 {sum(1 for m in models['models'] if m['available'])} 个")

            # ---- 流式对话 ----
            print("[5/5] 流式对话")
            deltas = 0
            first_at: float | None = None
            text_parts: list[str] = []
            done: dict | None = None
            started = time.time()
            with client.stream(
                "POST", "/api/chat",
                json={"model": "deepseek-chat",
                      "messages": [{"role": "user", "content": "用一句话解释什么是向量"}]},
            ) as response:
                event = ""
                for line in response.iter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        if event == "delta":
                            deltas += 1
                            if first_at is None:
                                first_at = time.time() - started
                        elif event == "done":
                            import json as _json
                            done = _json.loads(line[5:].strip())

            print(f"        收到 {deltas} 个 delta（逐字推送）")
            if first_at is not None:
                print(f"        客户端观测 TTFT   {first_at * 1000:.0f} ms")
            if done:
                print(f"        服务端记录 TTFT   {done['ttft_s'] * 1000:.0f} ms")
                print(f"        总耗时            {done['elapsed_s'] * 1000:.0f} ms")
                print(f"        token             in {done['usage']['in_tokens']} / "
                      f"out {done['usage']['out_tokens']}"
                      f"（{'估算' if done['usage']['estimated'] else '实测'}）")
                print(f"        成本              ${done['cost']['usd']:.6f} "
                      f"(¥{done['cost']['cny']:.4f})")
                print(f"        降级              {done['degraded']}")

            ok = (all(checks.values()) and deltas > 10 and done is not None
                  and done["usage"]["out_tokens"] > 0 and done["cost"]["usd"] > 0)

        print("=" * 74)
        print("[PASS] 端到端验证通过" if ok else "[FAIL] 有检查项未通过")
        print("=" * 74)
        return 0 if ok else 1
    finally:
        for proc in (app, mock):
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        try:
            err_file.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
