#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
给"依赖块外定义"的代码块加上显式的前置说明

## 为什么用脚本改而不是手工

要改的块有二十多个，分散在 10 个文件里，而且每处插入位置不同
（有的是块注释的第一行，有的是块末尾）。
手工改容易漏、也容易把 Markdown 围栏弄坏。

脚本按"块"为单位处理：只在**块内部**插入，不碰围栏本身，
所以不会破坏 Markdown 结构。

## 插什么

在块的最前面插一行注释，交代这段需要什么前置。例如：

    # 前置：`response` 是上一段 API 调用的返回字典（第 01 章讲过来源）
    #      想直接跑的话，先执行上面的 `response = json.loads(raw)`

**关键是给"最小可运行"的线索**，而不是空口说"上下文里有"。

## 幂等

已经有 `# 前置：` 注释的块会跳过 —— 可以反复运行。

用法：
    python tools/annotate_block_context.py --dry-run
    python tools/annotate_block_context.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent.parent / "docs"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

MARK = "# 前置："

#: 变量 -> 一句"它是什么 + 怎么补出来"
#: 只写给读者**真正需要**的那些：能自己一眼看懂的（如 `items`）不列。
GLOSSARY: dict[str, str] = {
    "response": "调用模型 API 后拿到的响应字典（来源见第 01 章）",
    "prompt": "送给模型的提示词字符串",
    "messages": "对话消息列表，形如 [{\"role\": \"user\", \"content\": \"...\"}]",
    "usage": "响应里的 token 用量字典（response[\"usage\"]）",
    "model_output": "模型返回的原始 JSON 字符串",
    "raw": "API 返回的原始文本（还没 json.loads）",
    "part": "循环里的当前片段",
    "parts": "切分后的片段列表",
    "words": "分词后的词列表",
    "events": "事件记录列表，形如 [{\"user_id\": 1, \"type\": \"login\"}]",
    "target": "要查找的目标值",
    "context": "上下文文本",
    "question": "用户的问题",
    "resp": "调用 API 后的响应对象",
    "fetch_data": "一个会抛异常的函数（这里只为演示 try/except 的用法）",
    "do_something": "一个占位函数，代表\"任意业务逻辑\"",
    "main": "本模块的主函数（下面的示例会调用它）",
    "failing_task": "一个会失败的协程（这里只为演示异常处理）",
    "evaluate_one": "单条评估函数（对一个问题跑一遍并返回结果）",
    "get_value": "一个异步取值函数（演示 gather 用的）",
    "process": "处理单条数据的函数",
    "call_model": "调用模型的函数（第 01 章的形式：收 messages，返回文本）",
    "estimate_tokens": "粗估 token 数的函数（按字符数近似）",
    "call_with_retry": "上面练习里写的带重试的调用函数",
    "score": "分数变量",
    "age": "待判断的年龄",
    "is_admin": "布尔标志：是不是管理员",
    "is_owner": "布尔标志：是不是所有者",
    "is_absent": "布尔标志：是不是缺席",
    "my_dict": "任意一个字典",
    "count": "一个整数变量",
    "name": "一个字符串变量",
    "UserProfile": "一个类名（类在第 08 章讲，这里只是演示命名风格）",
    "json": "标准库 json（用它的函数前要 import json）",
    "urllib": "标准库 urllib（用它的函数前要 import urllib.request）",
    "request": "urllib.request 模块，或请求对象",
    "text": "待处理的文本",
    "data": "通用数据变量",
    "result": "通用结果变量",
    "items": "通用列表变量",
    "user": "用户对象",
    "answer": "答案字符串",
    "record_usage": "上面练习 3 里定义的那个函数",
}


def split_blocks(md: str):
    """产出 (起始行号, 语言, 代码行列表, 围栏结束行号)。"""
    out, in_block, lang, start, buf = [], False, "", 0, []
    for i, line in enumerate(md.splitlines(), 1):
        if line.strip().startswith("```"):
            if in_block:
                out.append((start, lang, buf, i))
                in_block, buf = False, []
            else:
                in_block, lang, start, buf = True, line.strip()[3:].strip(), i + 1, []
            continue
        if in_block:
            buf.append(line)
    return out


def names_in(src: str) -> set[str]:
    import ast
    import builtins
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
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
    return {n for n in used - defined - set(dir(builtins)) if len(n) > 2}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    total = 0
    for f in sorted(DOCS.glob("*.md")):
        md = f.read_text(encoding="utf-8")
        whole = set(re.findall(r"^\s*(\w+)\s*=", md, re.M))
        whole |= set(re.findall(r"^\s*def\s+(\w+)", md, re.M))
        whole |= set(re.findall(r"^\s*class\s+(\w+)", md, re.M))
        whole |= set(re.findall(r"^\s*import\s+(\w+)", md, re.M))

        lines = md.splitlines()
        # 从后往前改，避免行号偏移
        for start, lang, src_lines, end in reversed(split_blocks(md)):
            if lang not in ("python", "py"):
                continue
            src = "\n".join(src_lines)
            if MARK in src:
                continue                      # 幂等
            if re.search(r"找出问题|有什么问题|哪有问题|❌|反例|错例", 
                         "\n".join(src_lines[:6])):
                continue                      # 故意错例：不加前置
            try:
                compile(src, f.name, "exec")
            except SyntaxError:
                continue
            unk = names_in(src)
            # ============================================================
            # 只标注"文件里**从未定义过**"的名字
            # ============================================================
            #
            # 第一版标了 44 个块 —— 太多了，而且大部分是噪音：
            #   · `response` / `messages` 这类**行业通称**，正文交代过来源，
            #     读者看得懂，加一行注释只是把文档撑长
            #   · `count` / `name` / `text` 这类，上两行刚赋值过，读者看得见
            #
            # 真正值得标注的是：**整个文件里都没有定义、读者无法自己补出来**
            # 的那些（比如 `fetch_data`、`estimate_tokens`）。
            # 收窄之后从 44 个降到十几个，每一条都是真的有用。
            defined_somewhere = set(re.findall(r"^\s*(\w+)\s*=", md, re.M))
            defined_somewhere |= set(re.findall(r"^\s*def\s+(\w+)", md, re.M))
            defined_somewhere |= set(re.findall(r"^\s*class\s+(\w+)", md, re.M))
            defined_somewhere |= set(re.findall(r"^\s*import\s+(\w+)", md, re.M))
            defined_somewhere |= set(re.findall(
                r"^\s*from\s+[\w.]+\s+import\s+(.+)", md, re.M))
            # 展开 `from x import a, b` 那种
            expanded: set[str] = set()
            for item in defined_somewhere:
                for piece in re.split(r"[,\s]+", item):
                    if piece:
                        expanded.add(piece.strip())
            defined_somewhere = expanded

            need = sorted(n for n in unk
                          if n in GLOSSARY and n not in defined_somewhere)
            if not need:
                continue

            notes = [f"#      {GLOSSARY[n]}（`{n}`）" for n in need]
            header = [f"{MARK}这段需要下面这些前置（照抄时请一并复制）："] + notes
            # 插在块最前面（保持缩进为 0）
            new_lines = header + src_lines
            lines[start - 1:end - 1] = new_lines
            total += 1
            print(f"  {'[dry]' if args.dry_run else '[改]'} {f.name}:{start}  "
                  f"+ {len(need)} 条说明（{', '.join(need)}）")

        if not args.dry_run and total:
            f.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print()
    print(f"  共处理 {total} 个块")
    if args.dry_run:
        print("  （--dry-run，未写入）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
