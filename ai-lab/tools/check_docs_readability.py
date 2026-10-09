#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
教程文档的可读性检查：有没有"没头没尾"的代码片段。

## 为什么需要这个脚本

有两类文档问题，测试和 linter 都抓不到，但读者会卡住：

  1. **悬空变量**：代码里用了 `response` / `result` 这类变量，但从没说明它是什么。
     新手看到一个不知道来源的变量，整段代码就没法判断对错。
     （本章已出现过：6 个章节都在用 `response`，却没有任何一处定义过它。）

  2. **悬空语言标签**：代码块没写语言（``` 后面空着），阅读器不会高亮，
     而且没法判断是 Python 还是 JSON。

这个脚本用启发式规则找出可疑处，**报出来让人判断** ——
它不追求零误报，追求的是"不遗漏"。误报比漏报便宜得多。

用法：
    python ai-lab/tools/check_docs_readability.py
    python ai-lab/tools/check_docs_readability.py --dir ai-lab/python_basics/docs

退出码：0 = 没有可疑项；1 = 有（需人工确认）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# 这些变量名如果出现，必须在同一篇文档里有过"定义/来源"说明，
# 否则读者不知道它们从哪来。
TRACKED_VARS = ["response", "result", "raw", "data", "payload"]

# 判断某变量在本文件里是否被"解释过"。
# 做法：在变量名附近（前后 ~120 字）找这些"交代来源"的词。
# 窗口不能太大（会把无关段落算进来），也不能太小（一句话有分句）。
EXPLAIN_HINTS = (
    "是什么", "来自", "来自哪里", "哪来的", "从哪", "来源",
    "返回值", "返回的是", "由", "其实就是", "就是前面", "就是上面",
    "是前面", "是上面", "见本章", "见第", "指",
)

# 内建函数 + 标准库/常用库函数 + 本项目里跨文件复用的函数。
# 这个名单不必穷尽 —— 有 def 检查兜底，这里只是减少明显噪音。
BUILTIN_AND_COMMON = {
    # 内建
    "print", "len", "range", "open", "input", "int", "str", "float", "list",
    "dict", "set", "tuple", "sum", "max", "min", "sorted", "enumerate", "zip",
    "type", "isinstance", "format", "repr", "getattr", "setattr", "map",
    "filter", "any", "all", "bool", "round", "abs", "super", "hasattr",
    "callable", "iter", "next", "vars", "dir", "id", "hash", "eval", "exec",
    # json / os / pathlib / datetime / random / re
    "dumps", "loads", "load", "dump", "environ", "getenv", "mkdir", "exists",
    "glob", "resolve", "read_text", "write_text", "fromisoformat", "isoformat",
    "now", "utcnow", "timedelta", "strptime", "strftime", "choice", "choices",
    "randint", "randrange", "random", "shuffle", "sample", "search", "match",
    "findall", "sub", "compile", "escape", "most_common", "joinpath",
    # asyncio / httpx
    "gather", "create_task", "wait_for", "sleep", "run", "to_thread", "timeout",
    "post", "stream", "aclose", "get", "put", "request",
    # 本项目跨文件复用（在别的文件里定义，文档里会指明出处）
    "estimate_tokens", "cost_usd", "load_env_file", "resolve_config",
    "print_stats", "open_stream", "stream_turn", "weighted", "write_csv",
    "event_ts", "parse_ts", "clean_text", "chunk_text", "call_model",
    "process", "evaluate_one", "usable_usage", "usage_from_api", "sse",
    "start_stack", "check_answer", "stream_once", "tool_calc", "tool_echo",
}

# 已人工核实、确认**不是问题**的项。
#
# 为什么要有这份名单：检查器的价值在于"发现新问题"，不在于"每次都报同样那几条"。
# 一个永远报 5 条已知误报的检查器，用三次之后人就不看了 —— 那还不如没有。
# 把核实结论记下来，脚本就只对**新出现**的可疑项报警。
#
# 每条都必须写明"为什么不是问题"，否则过几个月没人敢删。
KNOWN_NOISE: dict[tuple[str, str], str] = {
    # --- 变量名：文档里就是把它当"通用示例名"在讲解，本身已经交代过来源 ---
    ("04-条件循环与作用域.md", "data"): "第 4 条讲的就是 bytes/str 比较，一段示例代码里的局部名，正文已解释",
    ("10-调试与报错.md", "data"): "「代码不报错但结果不对」一节里的示范变量，正文写明『先怀疑数据还是逻辑』",
    # --- 函数名：JS/JS 语法对照代码，不是 Python 函数 ---
    ("06-异常与文件.md", "catch"): "JS 对照代码 `} catch (e) {`，不是 Python 调用",
    ("08-类与对象.md", "constructor"): "JS 对照代码 `constructor(name, age) {`，不是 Python 调用",
    # --- 库函数：来自 dataclasses，文档在讲用法时已写全调用形式 ---
    ("08-类与对象.md", "field"): "dataclasses.field(default_factory=list)，库函数，文档已给完整写法",
}


def find_code_blocks(text: str) -> list[tuple[int, str, str]]:
    """返回 [(起始行号, 语言标签, 代码内容)]。"""
    blocks = []
    pattern = re.compile(r"^```(\w*)\s*$")
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = pattern.match(lines[i])
        if m:
            lang = m.group(1)
            start = i + 1
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            blocks.append((start + 1, lang, "\n".join(body)))
        i += 1
    return blocks


def check_unlabeled_blocks(path: Path, text: str) -> list[tuple[str, str]]:
    """代码块必须有语言标签（否则不高亮、也没法判断是什么）。"""
    problems: list[tuple[str, str]] = []
    for lineno, lang, body in find_code_blocks(text):
        if lang:
            continue
        # 纯文本"输出示例"通常确实没有语言，允许但要求短
        if len(body.splitlines()) > 6:
            problems.append((
                f"{path.name}:{lineno} 代码块没有语言标签，且有 {len(body.splitlines())} 行"
                f"（建议标上 python / json / text）",
                "",
            ))
    return problems


def strip_comments_and_strings(code: str) -> str:
    """
    去掉注释**和字符串字面量**，只留真正的代码。

    为什么必须去字符串（这个检查器踩过）：SSE 协议的标记本身就是字符串 `"data:"`，
    于是每一段含 `"data: [DONE]"` 的示例都被报成"悬空变量 data"。
    但那是**字符串内容**，不是变量引用 —— 报出来是纯噪音。

    实现做了简化：按行扫，遇到引号就吞到下一个同类引号，不处理嵌套与转义细节。
    教学代码里几乎没有"字符串内含未转义引号"的情况，够用。
    """
    out_lines = []
    for line in code.splitlines():
        result: list[str] = []
        quote: str | None = None
        for ch in line:
            if quote:
                if ch == quote:
                    quote = None
                continue
            if ch in "\"'":
                quote = ch
                continue
            if ch == "#":
                break                      # 行内注释，后面都不要
            result.append(ch)
        out_lines.append("".join(result))
    return "\n".join(out_lines)


def check_dangling_vars(path: Path, text: str) -> list[tuple[str, str]]:
    """
    检查"用了但没解释来源"的变量。

    判定逻辑：
      - 变量在代码块里出现过（被读取，且**不是字符串里的字面量**）
      - 它在本文件里**从未**出现在赋值左侧、函数参数、或解释性文字里
      → 报可疑
    """
    problems: list[tuple[str, str]] = []
    code_text = strip_comments_and_strings(
        "\n".join(body for _, _, body in find_code_blocks(text)))

    for var in TRACKED_VARS:
        used = re.search(rf"\b{var}\b", code_text)
        if not used:
            continue

        # 1) 该变量在本文件的代码里被赋值过？
        assigned = re.search(rf"^\s*{var}\s*(?::[^=]+)?=", code_text, re.M)
        # 2) 作为函数参数出现过？
        as_param = re.search(rf"def\s+\w+\s*\([^)]*\b{var}\b", code_text)
        # 3) 正文里在变量名附近交代过来源？
        explained = False
        for hint in EXPLAIN_HINTS:
            # 变量名出现在提示词附近（前后 120 字）就算交代过
            for pattern in (rf"\b{var}\b[^\n]{{0,120}}{hint}",
                            rf"{hint}[^\n]{{0,120}}\b{var}\b"):
                if re.search(pattern, text):
                    explained = True
                    break
            if explained:
                break

        if not (assigned or as_param or explained):
            problems.append((
                f"{path.name}: 代码里用了 `{var}`，但全文没有任何地方说明它从哪来"
                f"（新手看不懂这段代码的前提）",
                var,
            ))
    return problems


def check_undefined_helpers(path: Path, text: str) -> list[tuple[str, str]]:
    """
    检查代码里调用了一个"从没定义过、也没交代来源"的**自由函数**。

    真实案例：文档里写 `tokens = usable_usage(...)` 但 `usable_usage` 是在
    几十行之后才给的，读者在这个位置会卡住。

    只查自由函数，**不查方法调用**（`x.extend(...)`、`text.removeprefix(...)`）。
    为什么（这个检查器第一版就踩了）：把 `extend` / `remove` / `get` 这类方法名
    当自由函数报了出来，35 条里 25 条是这种误报 —— 误报太多，人就不看了，
    等于没检查。**检查器的信噪比比覆盖率更重要。**
    """
    problems: list[tuple[str, str]] = []
    for _, _, body in find_code_blocks(text):
        # 先去掉 markdown 链接语法，避免把 [x.md](x.md) 里的 "md(" 当成函数调用
        code = re.sub(r"\[[^\]]*\]\([^)]*\)", "", body)
        for m in re.finditer(r"(?<![\w.])([a-z_][a-z0-9_]{3,})\s*\(", code):
            name = m.group(1)
            # (?<![\w.]) 已经排除了 x.name(...) 这种带点的调用
            if name in BUILTIN_AND_COMMON:
                continue
            if re.search(rf"def\s+{name}\s*\(", text):
                continue
            if re.search(rf"`{name}`|`{name}\(\)`|{name}\(\)", text):
                continue
            problems.append((
                f"{path.name}: 代码里调用了 `{name}()`，但本文件没有定义它，"
                f"也没有交代它从哪来",
                name,
            ))
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="教程文档可读性检查")
    ap.add_argument("--dir", type=Path,
                    default=REPO / "ai-lab" / "python_basics" / "docs")
    args = ap.parse_args()

    docs = sorted(args.dir.glob("*.md"))
    if not docs:
        print(f"[x] {args.dir} 下没有 .md 文件")
        return 1

    print("=" * 78)
    print("  教程可读性检查 · 找'没头没尾'的代码片段")
    print("=" * 78)
    print(f"  目录：{args.dir.relative_to(REPO)}（{len(docs)} 篇）")
    print()

    all_problems: list[tuple[str, str]] = []
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        before = len(all_problems)
        all_problems += check_unlabeled_blocks(doc, text)
        all_problems += check_dangling_vars(doc, text)
        all_problems += check_undefined_helpers(doc, text)
        batch = all_problems[before:]
        known = sum(1 for _, sym in batch if (doc.name, sym) in KNOWN_NOISE)
        fresh = len(batch) - known
        if fresh:
            status = f"{fresh} 处新可疑"
        elif known:
            status = f"{known} 处已知误报"
        else:
            status = "ok"
        print(f"  [{status:>12}] {doc.name}")

    print()
    new_items: list[str] = []
    known_items: list[str] = []
    for msg, sym in all_problems:
        fname = msg.split(":", 1)[0]      # 消息格式固定为 "文件名: ..."
        if (fname, sym) in KNOWN_NOISE:
            known_items.append(
                f"{msg}\n      → 已核实不是问题：{KNOWN_NOISE[(fname, sym)]}")
        else:
            new_items.append(msg)

    if known_items:
        print("=" * 78)
        print(f"  已知误报（已人工核实，不计入失败）—— {len(known_items)} 条")
        print("=" * 78)
        for item in known_items:
            print(f"  - {item}")
        print()

    if new_items:
        print("=" * 78)
        print(f"  需要人工确认的新可疑项 —— {len(new_items)} 条")
        print("=" * 78)
        for p in new_items:
            print(f"  - {p}")
        print()
        print("  说明：这是**启发式规则**报出来的，可能是误报。")
        print("        但请逐条确认 —— 悬空变量对新手是硬门槛：")
        print("        看到一个不知道来源的变量，整段代码就没法判断对错。")
        print("        核实为误报后，加进本脚本的 KNOWN_NOISE 并写明原因。")
    else:
        print("=" * 78)
        print("  [ok] 没有发现新的'没头没尾'代码片段")
        if known_items:
            print(f"       （另有 {len(known_items)} 条已知误报，见上）")
    print("=" * 78)
    return 1 if new_items else 0


if __name__ == "__main__":
    raise SystemExit(main())
