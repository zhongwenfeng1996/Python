#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
W1 · 流式对话 CLI（零依赖，纯标准库）

这是"先手写、再上框架"的练习：不用 openai SDK、不用 LangChain，
只用 urllib 发 HTTP 请求，自己解析 SSE。手写过一遍之后，
再用框架时你就知道它在替你做什么。

验收目标（对应学习计划 §3 第 1 周）：
  1. 流式输出 —— 首个 token 到达即显示，而不是等全文生成完
  2. 优雅中断 —— Ctrl+C 立刻停止，不残留后台请求
  3. token 计费 —— 每轮显示 input/output token 与估算成本
  4. 参数可调 —— temperature / max_tokens 可通过命令切换

用法::

    cp .env.example .env        # 填入你的 key
    python3 chat.py

    # 没有 key 也能验证代码（另开一个终端跑 mock 服务）
    python3 mock_server.py
    OPENAI_BASE_URL=http://127.0.0.1:8765/v1 OPENAI_API_KEY=test python3 chat.py

会话内命令：
    /exit            退出
    /clear           清空对话历史
    /stats           查看本会话累计 token 与成本
    /temp 0.7        修改 temperature
    /model gpt-4o-mini   切换模型
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# 每 100 万 token 的价格（美元，输入 / 输出）。价格会变，用前请去官网核对。
PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "deepseek-chat": (0.27, 1.10),
    "qwen-plus": (0.40, 1.20),
    "default": (0.50, 1.50),
}


# ----------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------
def load_env_file(path: Path) -> dict[str, str]:
    """极简 .env 解析（不引入 python-dotenv，减少依赖）。"""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def resolve_config(cli: argparse.Namespace) -> dict[str, str]:
    """优先级：命令行 > 环境变量 > .env 文件。"""
    file_env = load_env_file(Path(__file__).resolve().parent / ".env")

    def pick(key: str, default: str = "") -> str:
        return (getattr(cli, key.lower(), None)
                or os.environ.get(key)
                or file_env.get(key)
                or default)

    return {
        "api_key": pick("OPENAI_API_KEY"),
        "base_url": pick("OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
        "model": pick("MODEL", "deepseek-chat"),
    }


# ----------------------------------------------------------------------
# token 与成本估算
# ----------------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    """
    粗略估算：中日韩字符约 1 token/字，其余约 1 token/4 字符。
    真实项目应当用 tiktoken 或 API 返回的 usage —— 这里故意手写，
    是为了让你对"中文比英文贵得多"有体感。
    """
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + max(1, (len(text) - cjk) // 4)


def cost_usd(model: str, in_tokens: int, out_tokens: int) -> float:
    price_in, price_out = PRICES.get(model, PRICES["default"])
    return in_tokens / 1_000_000 * price_in + out_tokens / 1_000_000 * price_out


# ----------------------------------------------------------------------
# 流式请求
# ----------------------------------------------------------------------
def open_stream(cfg: dict[str, str], messages: list[dict],
                temperature: float, max_tokens: int | None,
                timeout: int = 60):
    """发起流式请求。返回的 response 是上下文管理器，可以直接迭代读取。"""
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    payload: dict = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        # 让服务端在最后一个 chunk 里带上 usage（不是所有兼容端点都支持）
        "stream_options": {"include_usage": True},
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=timeout)


def stream_turn(cfg: dict[str, str], messages: list[dict],
                temperature: float, max_tokens: int | None) -> tuple[str, dict | None, float]:
    """
    执行一轮流式对话。

    返回 (完整回复文本, usage 或 None, 首 token 延迟秒数)。
    Ctrl+C 会中断传输并关闭连接 —— 这就是"不残留后台请求"的含义。
    """
    started = time.time()
    first_token_at: float | None = None
    pieces: list[str] = []
    usage: dict | None = None

    try:
        response = open_stream(cfg, messages, temperature, max_tokens)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:500]
        raise RuntimeError(f"HTTP {exc.code} — {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"连接失败：{exc.reason}\n"
            f"检查 OPENAI_BASE_URL 是否正确、网络是否可达。"
        ) from exc

    with response:
        for raw_line in response:
            line = raw_line.decode("utf-8", "ignore").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue  # 忽略无法解析的保活行

            if chunk.get("usage"):
                usage = chunk["usage"]

            for choice in chunk.get("choices") or []:
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    if first_token_at is None:
                        first_token_at = time.time()
                    pieces.append(piece)
                    sys.stdout.write(piece)
                    sys.stdout.flush()

    text = "".join(pieces)
    ttft = (first_token_at - started) if first_token_at else 0.0
    return text, usage, ttft


# ----------------------------------------------------------------------
# 会话
# ----------------------------------------------------------------------
def print_stats(stats: dict, model: str) -> None:
    print("\n" + "-" * 56)
    print(f"会话统计（{model}）")
    print(f"  轮次          {stats['turns']}")
    print(f"  输入 token    {stats['in_tokens']:,}")
    print(f"  输出 token    {stats['out_tokens']:,}")
    print(f"  估算成本      ${stats['cost']:.4f}")
    if stats["ttft_list"]:
        avg = sum(stats["ttft_list"]) / len(stats["ttft_list"])
        print(f"  平均 TTFT     {avg:.2f}s   （首 token 延迟，比总耗时更影响体感）")
    print(f"  最长一轮      {stats['slowest']:.2f}s")
    print("-" * 56)


def main() -> int:
    parser = argparse.ArgumentParser(description="W1 流式对话 CLI")
    parser.add_argument("--model", help="模型名，如 deepseek-chat / gpt-4o-mini")
    parser.add_argument("--base-url", dest="base_url", help="API 基地址")
    parser.add_argument("--api-key", dest="api_key", help="API Key（默认读环境变量或 .env）")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-tokens", dest="max_tokens", type=int, default=None)
    parser.add_argument("--system", default="你是一个简洁、准确的中文助手。")
    args = parser.parse_args()

    cfg = resolve_config(args)
    if not cfg["api_key"]:
        print("缺少 API Key。请复制 .env.example 为 .env 并填写，或设置环境变量 OPENAI_API_KEY。",
              file=sys.stderr)
        return 2

    temperature = args.temperature
    messages: list[dict] = [{"role": "system", "content": args.system}]
    stats = {"turns": 0, "in_tokens": 0, "out_tokens": 0,
             "cost": 0.0, "ttft_list": [], "slowest": 0.0}

    print(f"模型 {cfg['model']}  ·  基地址 {cfg['base_url']}")
    print("输入消息开始对话；/exit 退出，/stats 看统计，/clear 清空历史。\n")

    while True:
        try:
            user_input = input("\n\033[1m你 ›\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input in ("/exit", "/quit"):
            break
        if user_input == "/clear":
            messages = [{"role": "system", "content": args.system}]
            print("[已清空对话历史]")
            continue
        if user_input == "/stats":
            print_stats(stats, cfg["model"])
            continue
        if user_input.startswith("/temp "):
            try:
                temperature = float(user_input.split(maxsplit=1)[1])
                print(f"[temperature = {temperature}]")
            except ValueError:
                print("[用法：/temp 0.7]")
            continue
        if user_input.startswith("/model "):
            cfg["model"] = user_input.split(maxsplit=1)[1].strip()
            print(f"[模型切换为 {cfg['model']}]")
            continue

        messages.append({"role": "user", "content": user_input})
        print("\033[36mAI ›\033[0m ", end="", flush=True)

        turn_started = time.time()
        try:
            text, usage, ttft = stream_turn(cfg, messages, temperature, args.max_tokens)
        except KeyboardInterrupt:
            # 关键：这里必须能立刻返回。response 的 with 块已经关闭连接。
            print("\n\033[33m[已中断 —— 本轮不计入历史]\033[0m")
            messages.pop()  # 丢掉这轮用户消息，避免历史里出现无人应答的孤例
            continue
        except RuntimeError as exc:
            print(f"\n\033[31m[请求失败] {exc}\033[0m")
            messages.pop()
            continue

        elapsed = time.time() - turn_started
        messages.append({"role": "assistant", "content": text})

        in_tokens = (usage or {}).get("prompt_tokens") or estimate_tokens(
            "".join(m["content"] for m in messages[:-1]))
        out_tokens = (usage or {}).get("completion_tokens") or estimate_tokens(text)
        turn_cost = cost_usd(cfg["model"], in_tokens, out_tokens)

        stats["turns"] += 1
        stats["in_tokens"] += in_tokens
        stats["out_tokens"] += out_tokens
        stats["cost"] += turn_cost
        stats["ttft_list"].append(ttft)
        stats["slowest"] = max(stats["slowest"], elapsed)

        source = "usage" if usage else "估算"
        print(f"\n\033[90m[{source}] in {in_tokens} · out {out_tokens} · "
              f"${turn_cost:.4f} · TTFT {ttft:.2f}s · 总 {elapsed:.2f}s\033[0m")

    if stats["turns"]:
        print_stats(stats, cfg["model"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
