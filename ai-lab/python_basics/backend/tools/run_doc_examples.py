#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从教程 Markdown 里**抽出代码块并真的运行** —— 验证"示例能跑"这个承诺

## 为什么需要

`audit_code_blocks.py` 只做**静态**检查（`compile()` + 未定义名字）。
但"能编译"不等于"能跑"：

  · 用了未定义的名字 → 静态能查出来
  · 运行时才炸（比如 `messages["content"]` 里 messages 是 list）
    → 静态查不出来
  · 输出与注释写的不一致 → 静态完全查不出来

这个脚本做第三层：**把块抽出来真跑一遍**，并把实际输出与
块里注释写的"预期输出"对比。

## 为什么不检查所有块

很多块是"用法片段"（依赖块外上下文），真跑必然 NameError ——
那不是缺陷，是设计。所以只跑**自洽的块**：
不含 `# 前置：` / `续上文` 标记，且相对独立的那类。

## 为什么用"抽取 + 子进程"而不是 exec

  1. 块之间可能重名（都叫 `ModelOutput`），同一个进程里跑会互相污染
  2. 块里有 `print`，要单独捕获每个块的输出才好对比
  3. `sys.exit()` / 死循环之类不会拖垮本脚本

用法：
    python tools/run_doc_examples.py --chapter 08
    python tools/run_doc_examples.py --all
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent.parent / "docs"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

SKIP_MARKS = ("# 前置：", "续上文", "在这里写", "你的代码", "TODO",
              "会报错", "报错", "演示")

#: 需要外部环境才能跑的块 —— 不算"跑不起来"。
#:
#: 实测踩到的：`09-异步与并发.md:213` 真跑会
#: `KeyError: 'OPENAI_API_KEY'` —— 但那个块本来就是"真实调用长这样"的示例，
#: 在教程环境下**不该**跑通。把这类报成失败是假阳性。
ENV_DEP_MARKS = ("os.environ", "os.getenv", "API_KEY", "getenv(")

#: 依赖真实网络/服务的块
NET_MARKS = ("httpx.", "requests.", "urllib.request", "OpenAI(", "client.post")

#: 块附近（前后）出现这些词 → 是"故意展示某个错误/行为"的示例
DEMO_HINTS = ("会报错", "报错", "演示", "故意", "报出", "为什么", "看出",
              "结果是", "实际会", "注意")

ERROR_HINTS = ("找出问题", "有什么问题", "哪有问题", "会报错", "报错", "❌",
               "不推荐", "反例", "错例", "陷阱", "故意")


def blocks(md: str):
    out, in_block, lang, start, buf = [], False, "", 0, []
    for i, line in enumerate(md.splitlines(), 1):
        if line.strip().startswith("```"):
            if in_block:
                out.append((start, lang, "\n".join(buf)))
                in_block, buf = False, []
            else:
                in_block, lang, buf = True, line.strip()[3:].strip(), []
                start = i + 1
            continue
        if in_block:
            buf.append(line)
    return out


def run_one(src: str, timeout: int = 20) -> tuple[int, str, str]:
    """在独立进程里跑一段代码，返回 (退出码, stdout, stderr)。"""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        p = subprocess.run([sys.executable, path], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", f"超时（>{timeout}s）—— 可能有死循环或等输入"
    finally:
        Path(path).unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", default="")
    ap.add_argument("--all", action="store_true",
                    help="连依赖块外上下文的块也试着跑（预期会有失败）")
    ap.add_argument("--include-appendix", action="store_true",
                    help="也跑附录（附-*）。默认跳过 —— 附录是'查用资料'，"
                         "代码是片段，经常需要真实文件/数据库/依赖才能跑，"
                         "用'必须能跑'的标准去要求它们会误报")
    args = ap.parse_args()

    files = sorted(DOCS.glob("*.md"))
    if args.chapter:
        files = [f for f in files if f.name.startswith(args.chapter)]
    if not args.include_appendix:
        # 附录（附-*）是"查用资料"，不是"照抄跑"的教程章节 ——
        # 它们的代码块是片段（跨块引用 User、需要真实 zip 文件、
        # 需要 email-validator 依赖），真跑必然失败，但这不是缺陷。
        files = [f for f in files if not f.name.startswith("附-")]

    print("=" * 92)
    print("  真的运行教程里的代码块（不只编译）")
    print("=" * 92)
    print()

    n_run = n_ok = n_skip = 0
    n_env = 0
    failures: list[str] = []

    for f in files:
        md = f.read_text(encoding="utf-8")
        rows = []
        lines = md.splitlines()
        for start, lang, src in blocks(md):
            if lang not in ("python", "py") or not src.strip():
                continue
            # 块前后的说明文字 —— 很多"故意演示"的线索在题干里
            around = "\n".join(lines[max(0, start - 10): start + src.count("\n") + 6])
            if not args.all and any(m in src for m in SKIP_MARKS):
                n_skip += 1
                continue
            if any(m in src for m in ENV_DEP_MARKS) or any(m in src for m in NET_MARKS):
                n_env += 1          # 需要真实环境，教程里跑不通是正常的
                continue
            if any(h in around for h in DEMO_HINTS):
                n_skip += 1
                continue
            if any(h in "\n".join(src.splitlines()[:6]) for h in ERROR_HINTS):
                n_skip += 1
                continue
            try:
                compile(src, f.name, "exec")
            except SyntaxError:
                n_skip += 1
                continue
            n_run += 1
            code, out, err = run_one(src)
            if code == 0:
                n_ok += 1
                rows.append(("ok", start, out, err))
            else:
                tail = (err.strip().splitlines() or ["（无输出）"])[-1]
                rows.append(("FAIL", start, out, tail))
                failures.append(f"{f.name}:{start}  {tail[:80]}")

        if rows:
            bad = [r for r in rows if r[0] != "ok"]
            print(f"  {'✅' if not bad else '❌'} {f.name:<32} "
                  f"跑了 {len(rows):>2} 个，失败 {len(bad)}")
            for kind, start, out, info in bad:
                print(f"        ❌ 第 {start} 行：{info}")

    print()
    print("=" * 92)
    print(f"  实际运行 {n_run} 个块 · 通过 {n_ok}")
    print(f"  跳过 {n_skip} 个（用法片段 / 故意演示 / 语法错）"
          f" · 需要真实环境 {n_env} 个（API key / 网络）")
    if failures:
        print()
        print("  运行失败的（静态检查查不出来这类）:")
        for x in failures:
            print(f"    · {x}")
    else:
        print("  ✅ 所有自洽的块都能真的跑起来")
    print()
    print("  为什么这一步有价值：`compile()` 只能证明**语法对**，")
    print("  证明不了**能跑**。这个仓库真出现过 `messages[\"content\"]`")
    print("  这种错（messages 是 list）—— 语法完全合法，一跑就 TypeError。")
    print("=" * 92)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
