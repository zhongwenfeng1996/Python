#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证"Pydantic 的可变默认值是安全的"这句话到底对不对

## 为什么要验证

我在第 04 章写了这么一句：

    > 有意思的是：同样写 `sources: list[str] = []`，在 Pydantic 模型里
    > **是安全的** —— 它会给每个实例深拷贝一份。

但 `run_doc_examples.py`（真跑教程代码块）在 08 章报出：

    ValueError: mutable default <class 'list'> for field tags is not allowed:
    use default_factory

那是 `@dataclass`，不是 Pydantic。**但两者不能混为一谈** ——
所以必须实测 pydantic 的行为，不能凭印象。

另外还要验证"每个实例深拷贝一份"这句话：
如果 Pydantic 真的深拷贝，两个实例的列表就不会互相影响。

用法：
    python tools/verify_pydantic_mutable_default.py
"""

from __future__ import annotations

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import pydantic
from pydantic import BaseModel, Field


def main() -> int:
    print(f"pydantic 版本：{pydantic.VERSION}")
    print()

    # ---------- 1. Pydantic 里 `list[str] = []` 到底行不行 ----------
    print("=" * 78)
    print("  1. Pydantic 模型里写 `tags: list[str] = []`")
    print("=" * 78)
    try:
        class M1(BaseModel):
            tags: list[str] = []

        a = M1()
        b = M1()
        a.tags.append("x")
        print(f"  ✅ 允许。a.tags = {a.tags}   b.tags = {b.tags}")
        print(f"  -> 两个实例{'**互不影响**（确实深拷贝了）' if b.tags == [] else '**互相影响**！'}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ 不允许：{type(exc).__name__}: {exc}")
    print()

    # ---------- 2. dataclass 里同样的写法 ----------
    print("=" * 78)
    print("  2. @dataclass 里写 `tags: list = []`（作对照）")
    print("=" * 78)
    from dataclasses import dataclass, field
    try:
        @dataclass
        class D1:
            tags: list = []

        print("  ⚠️ 这个版本居然允许了（不同 Python 版本行为不同）")
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ 不允许：{type(exc).__name__}")
        print(f"     {exc}")
    print()

    # ---------- 3. 标准写法：default_factory ----------
    print("=" * 78)
    print("  3. 两种框架推荐的写法都是 default_factory")
    print("=" * 78)

    class M2(BaseModel):
        tags: list[str] = Field(default_factory=list)

    @dataclass
    class D2:
        tags: list = field(default_factory=list)

    m2a, m2b = M2(), M2()
    m2a.tags.append("x")
    d2a, d2b = D2(), D2()
    d2a.tags.append("x")
    print(f"  Pydantic : a={m2a.tags}  b={m2b.tags}  "
          f"-> {'✅ 独立' if m2b.tags == [] else '❌ 共享'}")
    print(f"  dataclass: a={d2a.tags}  b={d2b.tags}  "
          f"-> {'✅ 独立' if d2b.tags == [] else '❌ 共享'}")
    print()

    print("=" * 78)
    print("  结论")
    print("=" * 78)
    print("  · Pydantic **允许** `list[str] = []`，而且确实给每个实例独立一份")
    print("    —— 所以第 04 章那句话的方向是对的")
    print("  · 但 Pydantic **官方推荐**用 `Field(default_factory=list)`：")
    print("    显式表达意图，且与 dataclass 的写法统一，不容易记混")
    print("  · `@dataclass` **不接受**裸的 `= []`，必须 `field(default_factory=list)`")
    print("    —— 08 章练习 3 讲的就是这个")
    print()
    print("  → 教程里要把这三条说清楚，不能只写『Pydantic 是安全的』就完事，")
    print("     否则读者在 dataclass 里照抄会踩坑。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
