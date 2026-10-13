#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
取出 pydantic 的真实报错格式 —— 教程里引用的输出必须与实际一致

## 为什么要跑一遍

教程里写"报错长这样"，如果那段注释是凭印象编的，
读者跑出来对不上就会怀疑自己。这个仓库有一条硬规则：
**文档里的示例输出必须与实际运行结果一致**（`verify_dataset.py` /
`verify_backend_demos.py` 都是这个思路）。

所以写示例之前先把真实输出打出来，照抄进注释。
"""

from __future__ import annotations

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import pydantic
from pydantic import BaseModel, ValidationError


class Extracted(BaseModel):
    name: str
    email: str
    company: str | None = None


def main() -> int:
    print(f"pydantic 版本：{pydantic.VERSION}")
    print()

    print("=" * 76)
    print("  情况 A：模型返回了**合法** JSON")
    print("=" * 76)
    good = '{"name": "张三", "email": "zhang@example.com"}'
    print(f"  model_output = {good!r}")
    d = Extracted.model_validate_json(good)
    print(f"  -> {d}")
    print(f"  -> d.name = {d.name!r}   d.company = {d.company!r}")
    print()

    print("=" * 76)
    print("  情况 B：模型**漏了必填字段** email")
    print("=" * 76)
    bad = '{"name": "张三"}'
    print(f"  model_output = {bad!r}")
    try:
        Extracted.model_validate_json(bad)
    except ValidationError as e:
        print("  -> 抛 ValidationError，str(e) 是：")
        for line in str(e).splitlines():
            print(f"       {line}")
    print()

    print("=" * 76)
    print("  情况 B 的结构化版本 e.errors()（更适合喂回模型，token 更少）")
    print("=" * 76)
    try:
        Extracted.model_validate_json(bad)
    except ValidationError as e:
        for err in e.errors():
            print(f"       {err}")
    print()

    print("=" * 76)
    print("  情况 C：模型返回的**根本不是 JSON**")
    print("=" * 76)
    notjson = "姓名：张三，邮箱：zhang@example.com"
    print(f"  model_output = {notjson!r}")
    try:
        Extracted.model_validate_json(notjson)
    except ValidationError as e:
        print("  -> 注意：**也是 ValidationError**（不是 JSONDecodeError）")
        for line in str(e).splitlines()[:4]:
            print(f"       {line}")
    print()

    print("=" * 76)
    print("  情况 D：把错误喂回模型时的 prompt 长什么样")
    print("=" * 76)
    try:
        Extracted.model_validate_json(bad)
    except ValidationError as e:
        retry = f"你的输出有以下错误，请修正：\n{e}"
        print("  retry_prompt 的前几行：")
        for line in retry.splitlines()[:6]:
            print(f"       {line}")
        print(f"  （整段 {len(retry)} 字符）")
    print()

    # ------------------------------------------------------------------
    # token 成本：实测数字，不凭印象
    # ------------------------------------------------------------------
    #
    # ⚠️ 我原来在教程里写的是"str(e) 是一大段带 URL 的文本，很费 token"。
    #    实测之后发现**这个说法被夸大了**：
    #      str(e)               207 字符
    #      json.dumps(errors()) 191 字符
    #    只差 16 字符（约 8%），而且 **e.errors() 里也有 url 字段** ——
    #    直接用 json.dumps 反而没省多少。
    #    真正省 token 的做法是先把 url 去掉。
    #
    #    所以正文已改成实测数字，并给出"怎么真的省下来"。
    import json as _json
    print("=" * 76)
    print("  情况 E：报错的 token 成本（实测，不凭印象）")
    print("=" * 76)
    try:
        Extracted.model_validate_json(bad)
    except ValidationError as e:
        s = str(e)
        struct_full = _json.dumps(e.errors(), ensure_ascii=False)
        no_url = [{k: v for k, v in x.items() if k != "url"} for x in e.errors()]
        struct_slim = _json.dumps(no_url, ensure_ascii=False)
        url_len = len("https://errors.pydantic.dev/2.13/v/missing")
        print(f"  str(e)                   : {len(s):>4} 字符")
        print(f"  json.dumps(e.errors())   : {len(struct_full):>4} 字符"
              f"   ← 注意：**里面也有 url 字段**")
        print(f"  去掉 url 的结构化版本     : {len(struct_slim):>4} 字符")
        print(f"  （那个文档 URL 本身就有 {url_len} 字符）")
        print()
        if len(struct_slim) < len(s):
            saved = 100 * (1 - len(struct_slim) / len(s))
            print(f"  → 真正省下来的是**去掉 url 之后**：比 str(e) 少约 {saved:.0f}%")
        print("  → 结论：'结构化就一定省 token'只看 json.dumps 是不成立的，")
        print("     必须显式剔除 url 这类给人看的字段。")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
