#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
项目二 · 环境与方案可行性探测

回答三个问题：
  1. 向量计算能用什么（numpy 装不装得上）
  2. 稀疏检索怎么办（FTS5 的中文分词到底行不行）
  3. 没有 API Key 时，embedding 用什么方案

用法（从仓库根跑）：
    & G:\\转型\\.venv\\Scripts\\python.exe ai-lab\\projects\\project2-rag\\probe_env.py
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys

MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def check_numpy() -> bool:
    print("=" * 74)
    print("  1. 向量计算：numpy 能不能用")
    print("=" * 74)
    try:
        import numpy  # noqa: PLC0415
        print(f"  [ok] 已装 numpy {numpy.__version__}")
        return True
    except ModuleNotFoundError:
        pass

    print("  [--] venv 里没有 numpy，尝试从清华镜像装")
    # ⚠️ 两个关键参数（都是实测踩出来的）：
    #   --only-binary :all:  强制只要预编译 wheel
    #        不加的话 pip 会尝试源码构建 —— 本机没装 C 编译器，
    #        pip 会卡在解析/构建上烧 CPU（实测烧了 301 秒 CPU 还没完，
    #        内存只占 47MB、缓存里没有下载文件，很容易误判成"网速慢"）。
    #        加上之后 numpy 14 秒装完。
    #   --timeout            避免网断时无限等
    cmd = [
        sys.executable, "-m", "pip", "install", "numpy",
        "--only-binary", ":all:",
        "--index-url", MIRROR,
        "--no-cache-dir", "--progress-bar", "off",
        "--timeout", "30", "--retries", "2",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180,
        )
    except subprocess.TimeoutExpired:
        # 第一版没处理这个 —— 脚本直接崩了，探测工具自己先崩是很糟的体验
        print("  [!!] 180 秒超时，放弃安装")
        _numpy_fallback_hint()
        return False

    if proc.returncode == 0:
        print("  [ok] 安装成功")
        return True
    print(f"  [!!] 安装失败（exit={proc.returncode}）")
    print((proc.stdout or "")[-300:])
    _numpy_fallback_hint()
    return False


def _numpy_fallback_hint() -> None:
    print("\n  → 退路：不用 numpy 也能做向量检索（纯 Python + math.sqrt）")
    print("     本项目的检索层设计成两种后端可切换，其中一个就是纯 Python。")
    print("     几千条文档下可用，只是慢；正好能对照出'为什么需要专门索引'。")


def check_fts5_chinese() -> None:
    print()
    print("=" * 74)
    print("  2. 稀疏检索：SQLite FTS5 对中文行不行")
    print("=" * 74)
    con = sqlite3.connect(":memory:")

    # 默认分词器 unicode61
    con.execute("CREATE VIRTUAL TABLE t_default USING fts5(content)")
    con.execute(
        "INSERT INTO t_default VALUES (?)",
        ("章节重排后 def 在第四章教，这一章讲函数和作用域的知识",),
    )
    print("  默认分词器（unicode61）:")
    for q in ("第四章", "函数", "作用域", "def", "章节重排"):
        n = con.execute(
            "SELECT count(*) FROM t_default WHERE t_default MATCH ?", (q,)
        ).fetchone()[0]
        print(f"    查询 {q!r:12} -> 命中 {n}")

    # trigram 分词器：按 3 个字符滑窗切，适合中文
    print()
    try:
        con.execute("CREATE VIRTUAL TABLE t_tri USING fts5(content, tokenize='trigram')")
        con.execute(
            "INSERT INTO t_tri VALUES (?)",
            ("章节重排后 def 在第四章教，这一章讲函数和作用域的知识",),
        )
        print("  trigram 分词器:")
        for q in ("第四章", "函数", "作用域", "def", "章节重排"):
            try:
                n = con.execute(
                    "SELECT count(*) FROM t_tri WHERE t_tri MATCH ?", (q,)
                ).fetchone()[0]
            except sqlite3.OperationalError as exc:
                n = f"ERR({exc})"
            print(f"    查询 {q!r:12} -> 命中 {n}")
        print()
        print("  → trigram 对中文（含 2 字词+单字混排）明显更好，且不需要外部分词库。")
        print("     代价：索引更大；查询至少要 3 个字符。")
    except sqlite3.OperationalError as exc:
        print(f"  trigram 不可用：{exc}")
        print("  → 退路：自己做字符级切分，写进普通表再用 LIKE / 自建倒排。")


def main() -> int:
    have_numpy = check_numpy()
    check_fts5_chinese()

    print()
    print("=" * 74)
    print("  3. Embedding 方案（当前没有 API Key）")
    print("=" * 74)
    print("  方案 A · 本地确定性 embedding —— 零依赖、零成本、可离线、结果可复现")
    print("           做法：字符 n-gram → 哈希到固定维度 → L2 归一化")
    print("           优点：评估能跑、CI 能跑、不用等网络")
    print("           缺点：语义泛化弱（同义词、改写问句召回差）")
    print()
    print("  方案 B · 真实 embedding API（bge-m3 / text-embedding-3）")
    print("           需要 Key。效果最好，是最终要用的方案。")
    print()
    print("  设计结论：**接口统一，两种实现可切换**")
    print("           - 默认走 A，保证今天就能把全链路跑通并量化")
    print("           - 填了 Key 自动走 B，评估基线立刻能对比")
    print("           这本身就是'存储/模型可替换'的架构练习。")
    print()
    print(f"  numpy 可用：{'是' if have_numpy else '否（走纯 Python 退路）'}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
