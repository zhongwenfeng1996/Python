#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一致性检查：前端示例问题 与 后端预热列表 必须一一对应

## 为什么要检查

`demo` 的体验依赖"前端能点的题，后端都预热过"：

  · 前端有、后端没预热 → 用户点了要等几十秒（看起来像卡死）
  · 后端预热了、前端没有 → 白花钱预热没人点的题

两处列表在两个文件里（`frontend/index.html` 的 chips 与
`backend/main.py` 的 `DEMO_QUESTIONS`），手工维护必然漂移。

## 判据

  · 前端每条 chips 问题都必须在后端预热列表里
  · 后端预热列表里的每条都应当在前端能看到（否则白预热）

⚠️ 但**不比对顺序** —— 前端 chips 是按"能答 → 该弃答"排的（便于演示），
   后端列表顺序无所谓。

⚠️ 也不做"完全一致"的强断言：后端**允许**多预热几条
   （比如为将来加 chips 预备），只是会打一条提示。

用法：
    python tools/check_demo_lists.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def main() -> int:
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    py = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")

    # 前端：examples 数组里的 q: '...'
    chips = re.findall(r"q:\s*'([^']+)'", html)
    # 后端：DEMO_QUESTIONS 列表里的 "..."
    block = re.search(
        r"DEMO_QUESTIONS[^=]*=\s*\[(.*?)\]", py, re.S)
    warm = re.findall(r'"([^"]+)"', block.group(1)) if block else []

    print("=" * 84)
    print("  演示列表一致性检查")
    print("=" * 84)
    print(f"  前端示例（chips）: {len(chips)} 条")
    print(f"  后端预热          : {len(warm)} 条")
    print()

    missing = [q for q in chips if q not in warm]
    extra = [q for q in warm if q not in chips]

    ok = True
    if missing:
        ok = False
        print("  ❌ 前端有、后端**没预热**（用户点了要等几十秒）：")
        for q in missing:
            print(f"      {q}")
        print("      → 把它们加进 backend/main.py 的 DEMO_QUESTIONS")
    else:
        print("  ✅ 前端每条示例都已预热")

    if extra:
        print(f"  ⚠️ 后端预热了 {len(extra)} 条前端没有的（白花钱，但不影响体验）：")
        for q in extra:
            print(f"      {q}")
    print()

    # 顺带检查：弃答类示例是否存在（演示的关键卖点）
    has_abstain_demo = any(("star" in q or "Transformer" in q or "测试用例" in q)
                           for q in chips)
    if has_abstain_demo:
        print("  ✅ 示例里有'该弃答'的题（演示的关键卖点）")
    else:
        ok = False
        print("  ❌ 示例里没有'该弃答'的题 —— 那就演示不出'会说不知道'这个卖点")
    print()

    print("=" * 84)
    print(f"  {'✅ 通过' if ok else '❌ 有不一致，需要修'}")
    print("=" * 84)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
