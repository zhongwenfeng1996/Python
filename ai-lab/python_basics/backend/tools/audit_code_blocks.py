#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
教程代码块审计 —— 区分三类块，只对"真错误"报失败

## 为什么需要区分三类

第一版审计直接把所有语法错都报成失败，结果**把正确答案误报了**：

    04-函数.md:316   def record_usage(tokens, history=[]):
                     ← 这是**故意给的错例**，练习就问"这个函数有什么问题"
    10-调试与报错.md:92   print("hello"
                     ← 这是**故意演示**"SyntaxError 报的行号可能不准"
    10-调试与报错.md:106  print(“hello”)
                     ← 这是**故意演示**"中文标点"

这三类块**语法错是它们的重点**，报失败等于让工具说谎。

## 三类块及其判据

  1. **可运行块** —— 必须 `compile()` 通过，且**不依赖块外定义**。
     如果确实要接着上文（比如"怎么改一个已有的类"），
     必须**显式标注**（HTML 注释 `<!-- 续上文 -->`），
     否则读者单独复制就是 NameError。

  2. **故意错例** —— 题目里会说明"找出问题""下面这段会报错"。
     判据：**前 6 行内**出现这些关键词。
     这类块跳过语法检查。

  3. **非 Python 内容** —— 目录树、shell 输出、报错文本等。
     判据：语言标记不是 python，或者块里出现树形符号/明显非代码特征。

## 判据为什么用"前 6 行"而不是"整块搜索"

关键词散落在别处会误判。比如某个可运行块的注释里恰好写了
"找出问题"四个字，整块搜索就会把它当错例跳过 —— 反而漏检。
只看块的开头，是"这句话在描述这个块"最接近的近似。

用法：
    python tools/audit_code_blocks.py
    python tools/audit_code_blocks.py --chapter 08
    python tools/audit_code_blocks.py --show-errors   # 列出故意错例（核对用）
"""

from __future__ import annotations

import argparse
import ast
import builtins
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 本脚本在 python_basics/backend/tools/ 下，上溯三层才是 python_basics/
DOCS = HERE.parent.parent / "docs"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

BUILTINS = set(dir(builtins))

#: 块开头出现这些词 → 判定为"故意错例"，跳过语法检查
#:
#: ⚠️ 词表要覆盖**题干的常见说法**。踩过的坑：
#:    08 章练习 3 的题干写的是"下面代码**会报错**，为什么？"，
#:    而词表里只有"报错" —— 子串其实能匹配上"会报错"…
#:    但真正漏掉它的原因是：那句话在**块前面**，而这里只看了块的开头。
#:    所以块内的关键词判据不够，必须同时看**块前面的题干文字**
#:    （见 looks_like_error_demo 的 stem_before 参数）。
ERROR_DEMO_HINTS = (
    "找出问题", "有什么问题", "哪有问题", "会报错", "报错", "会报",
    "错误", "❌", "不推荐", "反例", "错例", "陷阱", "坑", "为什么错",
    "报出", "失败的写法", "下面这段有", "不能这样", "为什么？",
)

#: 标记"这一段接着上文"的显式注释 —— 有它就不再检查未定义名字
CONTINUATION_MARK = "续上文"

#: 标记"这一段需要块外的前置"的显式注释 —— 由
#: `annotate_block_context.py` 写入。有它也不再检查未定义名字。
#:
#: ⚠️ 这两个标记必须与 annotate_block_context.py 里的 MARK 保持一致。
#:    第一版我漏了这个，导致"标注过前置的块"仍然被报成问题 ——
#:    两个脚本口径不一致，正是这个项目上反复出现的那类错
#:    （诊断口径与生产不一致，见 ADR-008 / ADR-010）。
PREREQ_MARK = "# 前置："

#: 非 Python 内容的特征（目录树 / shell 提示符 / 报错输出）
NON_CODE_HINTS = ("├", "│", "└", "PS>", "$ ", "Traceback (most", "...\n")


def split_blocks(md: str):
    """
    产出 (起始行号, 语言, 代码, 是否未闭合, 块前面的文字)。

    为什么连"块前面的文字"一起返回：判断一个块是不是"故意演示错误"，
    关键线索经常在**题干里**而不在块里 —— 比如
    "下面代码**会报错**，为什么？" 这句在块上方，
    只看块本身就会把它误判成"坏了"。
    """
    out = []
    in_block, lang, start, buf = False, "", 0, []
    lines = md.splitlines()
    for i, line in enumerate(lines, 1):
        if line.strip().startswith("```"):
            if in_block:
                stem = "\n".join(lines[max(0, start - 8):start - 1])
                out.append((start, lang, "\n".join(buf), False, stem))
                in_block, buf = False, []
            else:
                in_block, lang, start, buf = True, line.strip()[3:].strip(), i + 1, []
            continue
        if in_block:
            buf.append(line)
    if in_block:                       # 没闭合
        out.append((start, lang, "\n".join(buf), True, ""))
    return out


def head_text(src: str, n: int = 6) -> str:
    return "\n".join(src.splitlines()[:n])


def looks_like_error_demo(src: str, stem_before: str = "") -> bool:
    """
    判断这个块是不是"故意演示错误"。

    三条判据（命中任一即可）：

      ① 块**开头**出现"找出问题 / 报错 / ❌"这类词

      ② **块前面的题干文字**里有问句式提示。
         ⚠️ 这一条是补上的 —— 踩过的坑：
         08 章练习 3 的题干是"下面代码**会报错**，为什么？"，
         那句话在块**前面**，只查块本身就会漏掉它。
         漏掉的后果是：把一个**正确的教学示例**报成失败。

      ③ 块本身有语法错，但块内注释解释了"这里就是错的"
         —— 比如讲"SyntaxError 报的行号可能不准"那一节：
             print("hello"          # 少了右括号
         这个块语法错是刻意的，判据 ① ② 都抓不到它。
    """
    head = head_text(src)
    if any(h in head for h in ERROR_DEMO_HINTS):
        return True
    # ② 看块前面的题干文字（取最近 260 字符，够覆盖一句话加标点）
    stem = stem_before[-260:]
    if any(h in stem for h in ("会报错", "有什么问题", "找出问题", "哪有问题",
                               "为什么错", "不能这样", "下面这段有")):
        return True
    # ③ 语法错 + 块内有解释性注释
    try:
        compile(src, "<probe>", "exec")
    except SyntaxError:
        # 有语法错。看看块里有没有"少/多/错/注意"这类解释词在注释里
        comments = "\n".join(l for l in src.splitlines()
                             if l.strip().startswith("#"))
        return any(w in comments for w in ("少", "多", "错", "注意", "问题"))
    return False


def looks_like_non_code(src: str) -> bool:
    h = head_text(src, 4)
    return any(k in src for k in NON_CODE_HINTS) and not re.search(
        r"^\s*(def |class |import |from |for |if |print\()", src, re.M)


def undef_names(src: str) -> list[str]:
    """块里用了、但既没 import 也没定义、也不是内建的名字。"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    defined: set[str] = set()
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs):
                    defined.add(arg.arg)
                if a.vararg:
                    defined.add(a.vararg.arg)
                if a.kwarg:
                    defined.add(a.kwarg.arg)
        elif isinstance(node, ast.Name):
            (defined if isinstance(node.ctx, ast.Store) else used).add(node.id)
        elif isinstance(node, ast.Import):
            for a in node.names:
                defined.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                defined.add(a.asname or a.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, ast.comprehension):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    defined.add(n.id)
    return sorted(n for n in used - defined - BUILTINS if len(n) > 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", default="")
    ap.add_argument("--show-notes", action="store_true",
                    help="列出「依赖块外定义」的提示（人工核对用）")
    ap.add_argument("--show-errors", action="store_true",
                    help="列出被判为「故意错例」的块（核对判据是否过宽）")
    args = ap.parse_args()

    files = sorted(DOCS.glob("*.md"))
    if args.chapter:
        files = [f for f in files if f.name.startswith(args.chapter)]
    if not files:
        print(f"❌ 没找到文档（{DOCS}）")
        return 1

    print("=" * 94)
    print("  教程代码块审计（区分：可运行 / 故意错例 / 非代码）")
    print("=" * 94)
    print()

    tot_run = tot_err = tot_nc = 0
    fails: list[str] = []
    err_demos: list[str] = []
    all_notes: list[str] = []

    for f in files:
        md = f.read_text(encoding="utf-8")
        n_run = n_err = n_nc = 0
        problems: list[str] = []
        notes: list[str] = []

        for start, lang, src, unclosed, stem in split_blocks(md):
            if unclosed:
                problems.append(f"代码块没闭合（从第 {start} 行开始）")
                continue
            if lang not in ("python", "py"):
                n_nc += 1
                continue
            if looks_like_non_code(src):
                n_nc += 1
                continue
            if looks_like_error_demo(src, stem):
                n_err += 1
                err_demos.append(f"{f.name}:{start}  {head_text(src, 1).strip()[:58]}")
                continue

            n_run += 1
            # ① 语法必须过 —— **这是硬失败**
            try:
                compile(src, f"{f.name}:{start}", "exec")
            except SyntaxError as e:
                problems.append(f"语法错 @ 第 {start} 行：{e.msg}"
                                f"（{e.text.strip() if e.text else ''}）")
                continue
            # ② 不该依赖块外定义 —— **这是提示，不是失败**
            #
            # 为什么降级：实测这类块有二十多个，而绝大多数是**故意的** ——
            # 比如讲 `try/except` 时用的 `fetch_data`（重点是异常处理，
            # 不该为了"能跑"而把那个函数也写出来），
            # 或者讲命名规范时举的 `UserProfile`（只是演示 PascalCase）。
            #
            # 把它们一律报成失败，工具就会永远红着、没人看 ——
            # 那等于没有工具。所以：**硬失败只留语法错**，
            # 这类列成"提示"，并且已经给最需要交代的块加了 `# 前置：` 标注。
            if CONTINUATION_MARK in src or PREREQ_MARK in src:
                continue
            unk = undef_names(src)
            if unk:
                notes.append(f"第 {start} 行依赖块外定义：{unk}")

        tot_run += n_run
        tot_err += n_err
        tot_nc += n_nc
        status = "✅" if not problems else "❌"
        extra = f"  （+{len(notes)} 处提示）" if notes else ""
        print(f"  {status} {f.name:<32} 可运行 {n_run:>2}  故意错例 {n_err:>2}  "
              f"非代码 {n_nc:>2}{extra}")
        for p in problems:
            print(f"       ❌ {p}")
            fails.append(f"{f.name}: {p.splitlines()[0]}")
        for nt in notes:
            all_notes.append(f"{f.name}: {nt}")

    print()
    print("=" * 94)
    print(f"  可运行块 {tot_run} · 故意错例 {tot_err} · 非代码块 {tot_nc}")
    print(f"  硬失败（语法错）{len(fails)} 处 · 提示（依赖块外定义）{len(all_notes)} 处")
    print()

    if args.show_errors:
        print("  被判为「故意错例」的块（核对判据有没有过宽）：")
        for e in err_demos:
            print(f"    {e}")
        print()

    if fails:
        print("  ❌ 需要修的（这些是照抄必报错的）:")
        for x in fails:
            print(f"    · {x}")
    else:
        print("  ✅ 没有语法错 —— 所有能跑的块都语法正确")

    if all_notes and args.show_notes:
        print()
        print("  提示（依赖块外定义 —— 多为故意，已标注的除外）:")
        for x in all_notes:
            print(f"    {x}")
    elif all_notes:
        print(f"  （另有 {len(all_notes)} 处'依赖块外定义'的提示，"
              f"加 --show-notes 查看）")
    print()
    print("  约定：")
    print("    · 硬失败只算**语法错** —— 那是照抄必报错的")
    print("    · '依赖块外定义'算提示：实测二十多个块里绝大多数是故意的")
    print("      （讲 try/except 时用的 fetch_data、讲命名规范时举的 UserProfile）")
    print("      把这类也报成失败，工具就会永远红着、没人看 —— 等于没有工具")
    print("    · 跨块延续的示例应标注 `# 前置：` 或 `<!-- 续上文 -->`")
    print("=" * 94)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
