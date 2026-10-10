#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端验收 —— 真的起一个进程、真的走 HTTP、真的解析 SSE

## 为什么不用 TestClient

项目一踩过这个坑（它的 ADR-006）：`httpx.ASGITransport` 是**进程内**传输，
不经过真实 socket，所以"客户端断开后服务端有没有停"这类行为根本测不了。

这个演示应用的核心卖点之一就是 **SSE 真实流式** ——
必须起真进程、真 HTTP，才能验证：
  · 首字延迟真的低（不是攒完一次发）
  · 分块到达（至少收到多个 chunk）
  · 中文不乱码（多字节字符跨 chunk）

## 验收项

  1. /api/health 返回语料规模与配置
  2. /api/ask 的事件顺序：meta → sources → delta… → done
  3. 能答的问题：有引用、引用编号合法、scope 通过
  4. 该弃答的问题：abstained=true
  5. **跨文档混淆被拦**：那条"项目一测试文件名"
  6. SSE 帧格式正确（事件名 + data + 空行）
  7. 中文无乱码

用法：
    python tools/verify_e2e.py                 # 自己起服务
    python tools/verify_e2e.py --port 8010
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'✅' if ok else '❌'}] {name}" + (f"  — {detail}" if detail else ""))


def wait_health(port: int, timeout: float = 120.0) -> dict:
    """等服务起来（第一次会建索引，可能要几秒）。"""
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = str(e)
            time.sleep(1.5)
    raise SystemExit(f"❌ 服务在 {timeout}s 内没起来：{last}")


def ask_stream(port: int, question: str) -> tuple[list[tuple[str, dict]], float, float]:
    """
    走真实 HTTP 发一个问题，返回 (事件列表, 首字延迟, 总耗时)。

    ## SSE 解析要点（与前端同一套逻辑）

      · 按空行切消息 —— 空行是 SSE 的分隔符，少了它浏览器会一直等
      · 一行 `event:` + 一行 `data:`
      · 必须用增量解码（多字节汉字可能跨 chunk）

    这里用 urllib 而不是 httpx，是为了**不依赖任何第三方库**就完成验收 ——
    验收脚本越少依赖越好用。
    """
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/ask",
        data=json.dumps({"question": question}).encode("utf-8"),
        method="POST")
    req.add_header("Content-Type", "application/json")

    events: list[tuple[str, dict]] = []
    t0 = time.perf_counter()
    first_delta: float | None = None

    with urllib.request.urlopen(req, timeout=180) as r:
        ct = r.headers.get("Content-Type", "")
        assert "text/event-stream" in ct, f"Content-Type 不对: {ct}"
        buf = ""
        while True:
            chunk = r.read1(4096) if hasattr(r, "read1") else r.read(4096)
            if not chunk:
                break
            buf += chunk.decode("utf-8", "replace")
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                ev, data = "message", ""
                for line in block.split("\n"):
                    if line.startswith("event:"):
                        ev = line[6:].strip()
                    elif line.startswith("data:"):
                        data += line[5:].strip()
                if not data:
                    continue
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if ev == "delta" and first_delta is None:
                    first_delta = time.perf_counter() - t0
                events.append((ev, obj))

    return events, (first_delta or 0.0), (time.perf_counter() - t0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--no-spawn", action="store_true",
                    help="服务已经在跑（不自己启动）")
    args = ap.parse_args()

    proc = None
    if not args.no_spawn:
        print(f"  启动服务（端口 {args.port}）…前几十秒在建索引")
        proc = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "backend" / "main.py"),
             "--port", str(args.port)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            cwd=str(ROOT))

    try:
        print()
        print("=" * 88)
        print("  端到端验收（真进程 + 真 HTTP + 真 SSE）")
        print("=" * 88)

        health = wait_health(args.port)
        print(f"  服务就绪：{health['docs']} 篇 / {health['chunks']} 块 "
              f"（索引 {health['build_s']}s）")
        print()

        # ---- 1. health ----
        check("/api/health 返回语料规模", health["docs"] > 10 and health["chunks"] > 100,
              f"{health['docs']} 篇 {health['chunks']} 块")
        check("/api/health 返回召回池与阈值",
              health["recall_k"] > 0 and "abstain_threshold" in health,
              f"recall_k={health['recall_k']} 阈值={health['abstain_threshold']}")

        # ---- 2. 能答的问题 ----
        print()
        print("  ── 能答的问题（期待：有引用、scope 通过）")
        q1 = "项目一现在有多少条测试通过？"
        ev1, fd1, el1 = ask_stream(args.port, q1)
        names1 = [e for e, _ in ev1]
        check("事件以 meta 开头", names1 and names1[0] == "meta", str(names1[:3]))
        check("有 sources 事件", "sources" in names1)
        check("有 delta 事件（真的流式）", names1.count("delta") > 1,
              f"{names1.count('delta')} 个 delta")
        check("有 done 事件", "done" in names1)
        check("事件顺序 meta→sources→delta→done",
              names1.index("meta") < names1.index("sources")
              < names1.index("delta") < names1.index("done"))

        d1 = next((o for e, o in ev1 if e == "done"), {})
        check("问题 1 作答（未弃答）", d1.get("abstained") is False)
        check("问题 1 有合法引用",
              bool(d1.get("cited")) and not d1.get("invalid_citations"),
              f"引用 {d1.get('cited')}")
        check("问题 1 作用域校验通过", d1.get("scope_ok") is True)

        # 中文乱码检查：回答里应包含中文，且没有替换字符
        text1 = (d1.get("final_text") or "")
        check("中文无乱码", "\ufffd" not in text1 and any(
            "\u4e00" <= c <= "\u9fff" for c in text1),
            text1[:40].replace("\n", " "))
        check("首字延迟 < 总耗时（确实流式）", fd1 < el1 and fd1 > 0,
              f"首字 {fd1:.2f}s / 总 {el1:.2f}s")

        # ---- 3. 该弃答的问题 ----
        print()
        print("  ── 该弃答的问题（期待：abstained=true）")
        for q, label in [
            ("这个仓库的 GitHub star 数是多少？", "私有/动态信息"),
            ("Transformer 的注意力机制时间复杂度是多少？", "完全离题"),
        ]:
            ev, _, _ = ask_stream(args.port, q)
            d = next((o for e, o in ev if e == "done"), {})
            check(f"弃答：{label}", d.get("abstained") is True,
                  (d.get("final_text") or "")[:30])

        # ---- 4. 跨文档混淆被机械校验拦住 ----
        print()
        print("  ── 跨文档混淆（ADR-012 的机械校验）")
        ev4, _, _ = ask_stream(args.port, "项目一的测试用例文件叫什么名字？")
        d4 = next((o for e, o in ev4 if e == "done"), {})
        # 模型可能自己就弃答了（ADR-011 说这条不稳定），两种都算通过：
        # 关键是**不能给出引用了错文档的答案**
        blocked = d4.get("abstained") is True or d4.get("scope_ok") is False
        check("跨文档混淆被拦住（弃答或作用域拦下）", blocked,
              f"abstained={d4.get('abstained')} scope_ok={d4.get('scope_ok')}")
        if d4.get("abstained_by_scope"):
            check("且能报告违规原因", bool(d4.get("scope_violations")),
                  str(d4.get("scope_violations"))[:80])

        # ---- 5. SSE 帧格式 ----
        print()
        print("  ── SSE 帧格式")
        # 重新发一次，直接看原始字节里 event:/data:/空行 是否成对
        req = urllib.request.Request(
            f"http://127.0.0.1:{args.port}/api/ask",
            data=json.dumps({"question": "项目一有多少条测试？"}).encode(),
            method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = ""
            while True:
                c = r.read(2048)
                if not c:
                    break
                raw += c.decode("utf-8", "replace")
                if '"abstained"' in raw:
                    break
        frames = [f for f in raw.split("\n\n") if f.strip()]
        check("每帧都有 event: 行", all("event:" in f for f in frames),
              f"{len(frames)} 帧")
        check("每帧都有 data: 行", all("data:" in f for f in frames))
        check("帧以空行分隔（能被浏览器识别）", len(frames) >= 4,
              f"{len(frames)} 帧")

    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    print()
    print("=" * 88)
    print(f"  通过 {len(PASS)} 项 / 失败 {len(FAIL)} 项")
    if FAIL:
        print("  失败：")
        for f in FAIL:
            print(f"    · {f}")
    print("=" * 88)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
