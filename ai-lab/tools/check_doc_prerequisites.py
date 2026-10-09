#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
教程"前置倒挂"检查：某章的示例有没有用到**后面的章节才教**的东西。

## 为什么要查这个

学习者按顺序读，在第 01 章看到 `def call_model(...)` 会直接卡住 ——
不是因为它难，而是因为**第 05 章才讲函数**。
这类问题读起来"就是莫名别扭"，但很难指出具体哪里不对，
因为它不是语法错误，是**教学顺序**的问题。

判断标准（"向前依赖"原则）：
    第 N 章的示例，只应该用到第 1..N 章教过的东西（+ 本语言最基础的常识）。

## 怎么确定"哪个特性在第几章教"

下面 FEATURE_CHAPTER 是人工维护的映射。这不是自动推断出来的 ——
**必须人工维护**，因为"这个语法算不算已经教过"是教学判断，不是文本匹配能得到的。

新增章节或调整顺序时，记得同步这张表。

用法：
    python ai-lab/tools/check_doc_prerequisites.py
    python ai-lab/tools/check_doc_prerequisites.py --chapter 01

退出码：0 = 没有倒挂；1 = 有。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# 第一层的章节文件名（用于显示）
DOCS = REPO / "ai-lab" / "python_basics" / "docs"
BACKEND_DEMOS = REPO / "ai-lab" / "python_basics" / "backend" / "demos"

# 第二层（后端篇）的章节编号。
#
# 为什么从 11 开始而不用 01-06：
#   后端篇**整层都以前 10 章为前置**（README 里写明的硬门槛）。
#   用 11-16 编号，`introduced_at <= chapter_no` 这条判断就自动成立 ——
#   基础篇教过的任何东西在后端篇里都不算前置倒挂，不需要额外写规则。
BACKEND_OFFSET = 10

# 后端篇新增的章节映射（叠加在 FEATURE_CHAPTER 之上）
#
# ⚠️ 这张表也会过期。写它的时候踩过一次：最初把 `ASGITransport` 排在第 06 章
#    （测试），但第 04、05 章的示例**本来就要用它在进程内发请求** ——
#    于是两章都报了倒挂。修正方式是承认事实：**"怎么在进程内请求一个 app"
#    是第 04 章就要会的基础技能，不是测试专属。**
BACKEND_FEATURE_CHAPTER: dict[str, int] = {
    "Pydantic BaseModel": BACKEND_OFFSET + 1,   # 第 01 章 Pydantic 基础
    "Literal": BACKEND_OFFSET + 1,
    "field_validator": BACKEND_OFFSET + 2,
    "FastAPI app": BACKEND_OFFSET + 4,
    "@app. 路由": BACKEND_OFFSET + 4,
    "ASGITransport": BACKEND_OFFSET + 4,        # 第 04 章：进程内请求（测试的基础）
    "StreamingResponse": BACKEND_OFFSET + 5,
    "async generator": BACKEND_OFFSET + 5,
    "pytest fixture": BACKEND_OFFSET + 6,
}

# 特性 -> 首次出现的章节号。
# 只收"学习者必须被教过才能读懂"的东西；纯语法糖（比如 f-string）也会造成卡顿，所以也收。
#
# ⚠️ 这张表必须与真实章节顺序同步！调整章节顺序后忘了改这里，
#    检查器会报一堆假倒挂（本仓库重排 04/05 章时就踩过：`def` 明明已经在
#    第 04 章教了，表里还写着 5，于是第 04 章自己报了 18 处"倒挂"）。
#
# 当前顺序（2026-10 重排后）：
#   01 变量  02 字符串  03 列表字典  04 函数  05 循环与作用域
#   06 异常与文件  07 模块  08 类  09 异步  10 调试
#
# 「只看名字就懂、不算前置」的白名单见 NOT_A_PREREQUISITE。
FEATURE_CHAPTER: dict[str, int] = {
    "f-string": 2,
    "列表推导式": 3,
    "字典推导式": 3,
    "切片": 3,
    ".get(": 3,          # 字典的 get 方法
    "def 定义函数": 4,    # ← 重排后从 5 提到 4（函数在"作用域"之前）
    "lambda": 4,
    "for 循环": 5,
    "while 循环": 5,
    "try/except": 6,
    "with 语句": 6,
    "读写文件": 6,
    "import": 7,
    "虚拟环境": 7,
    "class": 8,
    "isinstance": 8,
    "dataclass": 8,
    "Pydantic": 8,
    "async": 9,
    "await": 9,
    "asyncio": 9,
    "装饰器": 8,
    "logging": 10,
}

# 「看名字就知道意思」的东西 —— 不算前置倒挂。
#
# 这是刻意的尺度选择（和用户确认过）：只禁"需要理解才能用"的东西。
#   - `for` / `if` / `import` / `with`：语法自解释，只要该节开头一句话交代
#     它是什么，读者照抄也能跑，且不会因为"没被教过"而彻底卡死。
#   - `def` / `class` / `async` / 装饰器 / 推导式 / 切片：不懂含义就会写出错代码，
#     或者理解整段逻辑时断链 —— 这些必须"先教后用"。
#
# 效果：这个名单里的特性不再触发报警，也不会因为它们去强行改动章节顺序。
NOT_A_PREREQUISITE = {
    "for 循环", "while 循环", "import", "with 语句", "读写文件",
}

# 这些不算倒挂：它们是"读到就能猜出来"的常识，或者本章紧接着就会讲
WHITELIST_CONTEXT = (
    "第 %d 章", "见第", "后面会讲", "后面再讲", "第 0", "会在第",
    "下一章", "稍后", "先不用", "不用深究",
)

# 每章正文里出现这些词时，如果是"讲解"而非"预告"，就算倒挂
SEVERITY = {
    "def 定义函数": "中", "class": "高", "async": "高", "await": "高",
    "asyncio": "高", "import": "中", "try/except": "中", "with 语句": "低",
}


def blocks_of(text: str) -> list[tuple[int, int, str]]:
    """返回 [(起行, 止行, 内容)] 的代码块列表。"""
    out = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith("```"):
            start = i
            lang = lines[i][3:].strip()
            i += 1
            body = []
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            out.append((start + 1, i + 1, "\n".join(body)))
        i += 1
    return out


def looks_like_code(code: str) -> bool:
    """粗略判断一个代码块是不是 Python（排除 JS / JSON / 文本/目录树）。"""
    js_markers = ("=>", "const ", "let ", "function ", "console.log", "//")
    if any(m in code for m in js_markers):
        # JS 对照块整体排除（但混着 Python 的要注意，这里先粗筛）
        if "const " in code or "let " in code or "function " in code:
            return False
    json_like = code.strip().startswith("{") and ":" in code
    return not json_like


def strip_comments(code: str) -> str:
    """
    去掉行内注释，只保留真正的代码。

    为什么必须做（这个检查器第一版就踩了）：`# class UserProfile` 这种**注释里**
    提到 class 也被报了出来，于是清单里混着大量噪音。
    但注释里的东西读者不需要"能读懂"—— 他只要认字就行。
    判断"用了某个语法"必须只看代码本身。
    """
    out = []
    for line in code.splitlines():
        # 简单处理：找不在字符串里的 #。教学代码里几乎没有带 # 的字符串字面量，
        # 所以这里用最简规则 —— 宁可少删（保守），不要误删代码。
        idx = line.find("#")
        out.append(line if idx < 0 else line[:idx])
    return "\n".join(out)


def find_violations(chapter_no: int, text: str) -> list[str]:
    """找出本章代码块里用到了"超出本章"的特性。"""
    problems: list[str] = []
    for start, end, code in blocks_of(text):
        if not looks_like_code(code):
            continue
        source = strip_comments(code)      # ★ 只看代码，不看注释
        for feature, introduced_at in FEATURE_CHAPTER.items():
            if feature in NOT_A_PREREQUISITE:
                continue          # 看名字就懂的语法，不算前置
            if introduced_at <= chapter_no:
                continue          # 已经教过了
            if not _feature_present(feature, source):
                continue
            # 检查附近（代码块前后 8 行）有没有"见第 X 章"这类预告
            lines = text.splitlines()
            lo = max(0, start - 9)
            hi = min(len(lines), end + 8)
            context = "\n".join(lines[lo:hi])
            if any(w.replace("%d", "") in context for w in WHITELIST_CONTEXT):
                continue
            problems.append(
                f"{start}-{end} 行用了「{feature}」（第 {introduced_at} 章才教）"
                f"  严重度：{SEVERITY.get(feature, '低')}"
            )
    return problems


def _feature_present(feature: str, code: str) -> bool:
    """判断某个特性的特征是否出现在代码里。"""
    if feature == "f-string":
        return bool(re.search(r'f["\']', code))
    if feature == "列表推导式":
        return bool(re.search(r"\[[^\]\n]*\bfor\b[^\]\n]*\]", code))
    if feature == "字典推导式":
        return bool(re.search(r"\{[^}\n]*\bfor\b[^}\n]*\}", code))
    if feature == "切片":
        return bool(re.search(r"\w\[[^\]\n]*:[^\]\n]*\]", code))
    if feature == ".get(":
        return ".get(" in code
    if feature == "for 循环":
        return bool(re.search(r"^\s*for\s+\w+", code, re.M))
    if feature == "while 循环":
        return bool(re.search(r"^\s*while\s+", code, re.M))
    if feature == "try/except":
        return bool(re.search(r"^\s*try\s*:", code, re.M)) and "except" in code
    if feature == "with 语句":
        return bool(re.search(r"^\s*with\s+", code, re.M))
    if feature == "读写文件":
        return bool(re.search(r"\bopen\s*\(", code))
    if feature == "def 定义函数":
        return bool(re.search(r"^\s*(async\s+)?def\s+\w+", code, re.M))
    if feature == "lambda":
        return "lambda" in code
    if feature == "import":
        return bool(re.search(r"^\s*(import|from)\s+\w+", code, re.M))
    if feature == "虚拟环境":
        return "venv" in code
    if feature == "class":
        return bool(re.search(r"^\s*class\s+\w+", code, re.M))
    if feature == "isinstance":
        return "isinstance(" in code
    if feature == "dataclass":
        return "@dataclass" in code or "dataclasses" in code
    if feature == "Pydantic":
        # 只在**真的用了** Pydantic 时才算，而不是"提到了这个包名"。
        #
        # 为什么（这个检查器踩过）：requirements.txt / pyproject.toml 那种
        # 依赖清单里写着 `pydantic`，被当成了"第 07 章就在用 Pydantic"。
        # 但那是"列出要装的包"，不是"用它写代码" —— 报出来是纯噪音。
        # 同理，依赖清单里出现 fastapi / uvicorn 也不该算前置。
        return bool(re.search(r"\bBaseModel\b|\bmodel_validate\b|\bField\s*\(", code))
    if feature in ("async", "await"):
        return bool(re.search(r"\bawait\b", code)) or "async def" in code
    if feature == "asyncio":
        return "asyncio" in code
    if feature == "装饰器":
        return bool(re.search(r"^\s*@\w+", code, re.M))
    if feature == "logging":
        return "logging" in code
    # ---- 后端篇（第二层）新增 ----
    if feature == "Pydantic BaseModel":
        return bool(re.search(r"\(\s*BaseModel\s*\)", code))
    if feature == "field_validator":
        return "@field_validator" in code
    if feature == "Literal":
        return bool(re.search(r"\bLiteral\[", code))
    if feature == "FastAPI app":
        return bool(re.search(r"\bFastAPI\s*\(", code))
    if feature == "@app. 路由":
        return bool(re.search(r"@app\.(get|post|put|delete|patch)\s*\(", code))
    if feature == "StreamingResponse":
        return "StreamingResponse" in code
    if feature == "async generator":
        # async def 里有 yield —— 这是"异步生成器"，第一层没讲过
        return bool(re.search(r"async\s+def[^\n]*\n(?:.*\n)*?\s+yield\b", code))
    if feature == "pytest fixture":
        return "@pytest.fixture" in code
    if feature == "ASGITransport":
        return "ASGITransport" in code
    return False


def find_backend_violations(chapter_no: int, text: str) -> list[str]:
    """
    后端篇的检查。

    和后端篇的文档不同，这里要检查的是**完整的 .py 文件**（不是文档里的片段），
    所以直接扫整个文件，不用找代码块。先去掉注释与字符串。

    特性表叠加 BACKEND_FEATURE_CHAPTER —— 后端篇自带的新特性从第 11 章起算，
    而基础篇的一切（1..10）都视为已教过（README 里写明了这个硬门槛）。
    """
    problems: list[str] = []
    merged = {**FEATURE_CHAPTER, **BACKEND_FEATURE_CHAPTER}
    source = _strip(text)
    for feature, introduced_at in merged.items():
        if feature in NOT_A_PREREQUISITE:
            continue
        if introduced_at <= chapter_no:
            continue
        if _feature_present(feature, source):
            problems.append(
                f"用了「{feature}」，但它在本层第 {introduced_at - BACKEND_OFFSET} 章才教")
    return problems


def _strip(code: str) -> str:
    """去注释与字符串。

    为什么后端篇的示例也必须要做这一步：那是一个完整 .py 文件，
    里面的**文档字符串**会大量提到 "FastAPI"、"StreamingResponse" 这些词，
    不去掉的话每个文件都会报满倒挂。
    """
    out = []
    in_docstring = False
    for line in code.splitlines():
        stripped = line.strip()
        # 三引号文档字符串：成对出现，简单处理
        if stripped.startswith('"""') or stripped.startswith("'''"):
            quotes = stripped[:3]
            if stripped.count(quotes) >= 2 and len(stripped) > 3:
                out.append("")          # 单行 docstring
                continue
            in_docstring = not in_docstring
            out.append("")
            continue
        if in_docstring:
            out.append("")
            continue
        result: list[str] = []
        quote = None
        for ch in line:
            if quote:
                if ch == quote:
                    quote = None
                continue
            if ch in "\"'":
                quote = ch
                continue
            if ch == "#":
                break
            result.append(ch)
        out.append("".join(result))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="教程前置知识倒挂检查")
    ap.add_argument("--chapter", default="", help="只查某一章，如 01")
    ap.add_argument("--dir", type=Path, default=DOCS,
                    help="改成别的目录 —— 用来对比「改动前 / 改动后」，"
                         "确认一次修改是真的减少了倒挂、还是只是把问题挪了个位置")
    ap.add_argument("--no-backend", action="store_true",
                    help="跳过第二层（后端篇）的检查")
    args = ap.parse_args()

    docs_dir = args.dir
    chapters = sorted(p for p in docs_dir.glob("[0-9][0-9]-*.md"))
    if args.chapter:
        chapters = [p for p in chapters if p.name.startswith(args.chapter)]

    print("=" * 78)
    print("  前置知识倒挂检查 · 第 N 章的示例有没有用到第 M>N 章才教的东西")
    print("=" * 78)
    print(f"  第一层（基础篇）：{docs_dir}")
    if not args.no_backend:
        print(f"  第二层（后端篇）：{BACKEND_DEMOS}")
    print()

    total = 0

    print("—— 第一层 · 基础篇 ——")
    for doc in chapters:
        no = int(doc.name[:2])
        text = doc.read_text(encoding="utf-8")
        problems = find_violations(no, text)
        total += len(problems)
        if problems:
            print(f"[{len(problems)} 处] {doc.name}")
            for p in problems:
                print(f"        - {p}")
        else:
            print(f"[  ok  ] {doc.name}")

    # ---- 第二层：后端篇 ----
    if not args.no_backend:
        print()
        print("—— 第二层 · 后端篇（示例是完整 .py 文件）——")
        demos = sorted(BACKEND_DEMOS.glob("[0-9][0-9]_*.py")) if BACKEND_DEMOS.exists() else []
        demos = [d for d in demos if not d.name.startswith("__")]
        if not demos:
            print(f"[  --  ] 没找到示例（{BACKEND_DEMOS}）")
        for demo in demos:
            # 文件名 01_xxx.py -> 后端篇第 1 章 -> 全局第 11 章
            local_no = int(demo.name[:2])
            global_no = BACKEND_OFFSET + local_no
            problems = find_backend_violations(
                global_no, demo.read_text(encoding="utf-8"))
            total += len(problems)
            if problems:
                print(f"[{len(problems)} 处] {demo.name}")
                for p in problems:
                    print(f"        - {p}")
            else:
                print(f"[  ok  ] {demo.name}")

    print()
    print("=" * 78)
    if total:
        print(f"  共 {total} 处倒挂。")
        print("  说明：这不一定都要改 —— 有些是刻意预告（读起来没问题），")
        print("        但「需要理解才能用」的那几类（def / class / async / 装饰器）")
        print("        会让学习者真的卡住，必须调顺序或改写法。")
    else:
        print("  [ok] 没有发现前置倒挂（两层都干净）")
    print("=" * 78)
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
