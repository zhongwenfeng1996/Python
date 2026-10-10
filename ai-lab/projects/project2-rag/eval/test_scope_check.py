#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
作用域校验的单元测试 —— 机械校验必须自己被验证过才能信任

## 为什么必须有这个

我一共犯过两次同类错误（ADR-008、ADR-010）：**测量口径与生产不一致**。
作用域校验是同一类风险：规则写错了会**误伤可答题**，
而且它会在汇总指标里表现为"误弃答变多"，很难定位到具体规则。

所以逐条断言：
  · 应违规的必须违规
  · **不应违规的必须不违规**（这条更重要 —— 误判会损害可用性）

用法：
    python eval/test_scope_check.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from rag.scope_check import check_scope, find_entities, violated_citations  # noqa: E402


@dataclass
class P:
    """假的资料对象（只要有 source_path 就够校验用）。"""
    source_path: str


P1_CHAT = P("ai-lab/projects/project1-stream-chat/README.md")
P1_ADR = P("ai-lab/projects/project1-stream-chat/docs/ADR.md")
P2_BACK = P("ai-lab/python_basics/backend/README.md")     # 项目二后端篇
DOCS = P("ai-lab/python_basics/docs/04-函数.md")           # 基础篇
MCP = P("ai-lab/projects/mcp-minimal/README.md")
GENERIC = P("STATUS.md")                                    # 不属于任何实体
PLAN = P("前端转LLM应用工程师-学习计划.md")                 # 不属于任何实体

# (说明, 问题, 资料列表, 期望是否违规)
CASES: list[tuple[str, str, list, bool]] = [
    # ── 应违规：问题问项目一，资料却来自项目二后端篇
    ("问项目一，引用项目二后端篇（真实的跨文档混淆）",
     "项目一的测试用例文件叫什么名字？",
     [P2_BACK, P1_CHAT], True),

    # ── 不应违规：问题问项目一，资料来自项目一
    ("问项目一，引用项目一自己的文档",
     "项目一的日志写在哪个文件？",
     [P1_CHAT, P1_ADR], False),
    # ── 不应违规：通用文档不属于任何实体，引用它永远合法
    ("问项目一，但引用的是通用文档（STATUS.md）",
     "项目一现在有多少条测试通过？",
     [GENERIC, PLAN], False),
    # ── 不应违规：混合了项目一文档与通用文档
    ("问项目一，项目一文档 + 通用文档混合",
     "项目一现在有多少条测试通过？",
     [GENERIC, P1_CHAT], False),
    # ── 不应违规：问题里没有实体
    ("问题不涉及任何实体",
     "为什么 pip 装包要加 --only-binary 参数？",
     [DOCS, MCP], False),
    # ── 应违规：问基础篇，资料来自后端篇
    ("问基础篇，引用后端篇",
     "基础篇的第 4 章讲什么？",
     [P2_BACK, DOCS], True),
    # ── 应违规：问 MCP，资料来自项目一
    ("问 MCP，引用项目一",
     "MCP Server 提供了哪三个工具？",
     [P1_CHAT, MCP], True),
    # ── 不应违规：问 MCP，引用 MCP
    ("问 MCP，引用 MCP",
     "MCP Server 提供了哪三个工具？",
     [MCP, GENERIC], False),
]


def main() -> int:
    print("=" * 94)
    print("  作用域校验单元测试")
    print("=" * 94)
    print()

    # ---- 先看实体识别对不对 ----
    print("  ★ 实体识别")
    for q, expect in [
        ("项目一的测试用例文件叫什么名字？", ["项目一"]),
        ("为什么基础篇要重排章节？", ["基础篇"]),
        ("后端篇教几章？", ["后端篇"]),
        ("MCP 和 function calling 的区别？", ["MCP 实验"]),
        ("pip 装包为什么要加那个参数？", []),
    ]:
        got = [e.name for e in find_entities(q)]
        mark = "✅" if got == expect else f"❌ 期望 {expect}"
        print(f"    {mark}  {q[:40]:<42} -> {got}")
    print()

    # ---- 逐条校验 ----
    print("  ★ 作用域校验")
    n_fail = 0
    for desc, q, passages, should_violate in CASES:
        chk = check_scope(q, passages)
        got = not chk.ok
        ok = got == should_violate
        if not ok:
            n_fail += 1
        mark = "✅" if ok else "❌"
        print(f"    {mark} {desc}")
        print(f"       问题: {q}")
        print(f"       资料: {[p.source_path.split('/')[-1] for p in passages]}")
        print(f"       识别实体: {chk.entities_found}   违规: {got}"
              f"（期望 {should_violate}）")
        if chk.violations:
            for v in chk.violations:
                print(f"       └ 资料[{v.citation_index}] {v.reason}")
        print()

    print("=" * 94)
    if n_fail:
        print(f"  ❌ {n_fail}/{len(CASES)} 条断言失败 —— 规则有问题，不能直接投入使用")
    else:
        print(f"  ✅ 全部 {len(CASES)} 条断言通过")
    print()
    print("  特别注意「不应违规」那几条 —— 它们代表「误判」风险：")
    print("    · 通用文档（STATUS.md / 学习计划）不属于任何实体，引用它不该被拦")
    print("    · 问题里没有实体时不该做任何限制")
    print("  误判的代价是「本来能答的被拒」，直接损害可用性，所以它比漏判更严重。")
    print("=" * 94)
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
