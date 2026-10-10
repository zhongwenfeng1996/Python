#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
语料加载 —— 把仓库自己的文档变成知识库

## 为什么用仓库自己的文档当语料

  1. **有真实答案**：内容我完全掌握，能出"有确定 gold 文档"的评估题。
     评估集的题如果靠编，后面所有指标都是假的（ADR-005 专门强调了这点）
  2. **零获取成本**：不用下载、不用版权、不用担心语料质量不可控
  3. **面试可直接演示**："这是我的项目文档，问它关于项目的问题"
  4. **形态真实**：这些是技术文档 —— 有标题层级、代码块占一半、
     段落极短。比拿干净的新闻语料更能暴露真实问题

## 排除自己

`project2-rag/` 自己的文档**绝不能进知识库** —— 否则评估时
"问项目二的设计"会从它自己的 README 里找到答案，指标虚高。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# 仓库根（本文件在 ai-lab/projects/project2-rag/rag/ 下，上溯 4 层）
REPO_ROOT = Path(__file__).resolve().parents[4]

#: 语料来源目录（相对于仓库根）。顺序即优先级，仅影响展示。
CORPUS_DIRS: tuple[str, ...] = (
    "ai-lab/python_basics/docs",              # Python 教程（基础篇 10 章 + 附录）
    "ai-lab/python_basics/backend",           # 后端篇 6 章
    "ai-lab/projects/project1-stream-chat",   # 项目一文档
    "ai-lab/docs",                            # Windows 指南等
    "ai-lab/projects/mcp-minimal",            # MCP 实验文档
    "ai-lab",                                 # ai-lab 根：README + 数据结构说明
    ".",                                      # 仓库根：STATUS.md 等
)

#: 单独的语料文件（不在上面目录的 rglob 覆盖范围内，但要入库）
EXTRA_FILES: tuple[str, ...] = (
    "STATUS.md",                              # 进度看板：项目状态的事实来源
)


def _is_excluded(p: Path) -> bool:
    s = p.as_posix()
    return any(part in s for part in EXCLUDE_PARTS)

#: 绝不入库的路径片段（自我排除 + 噪音）
#
# ⚠️ `.pytest_cache` 是实测踩到的：pytest 会在每个跑过测试的目录下留一个
#    `.pytest_cache/README.md`（里面是 pytest 自己的说明文字）。
#    第一版没排除它，结果它被当成语料进了知识库 ——
#    评估时 "问 pytest 怎么用" 会命中这个缓存说明文件，噪音。
#    规律：**任何构建/缓存产物目录都要排除**，不能只排除 .venv。
EXCLUDE_PARTS: tuple[str, ...] = (
    "project2-rag",
    # ⚠️ 项目三也必须排除 —— 踩过的坑：
    #    project3-rag-chat/README.md 里写了**项目二的全部指标**，
    #    包括"哪些题该弃答""正确答案是什么"。
    #    它一进语料，语料就从 24 篇变成 25 篇、490 块变成 503 块，
    #    等于**把答案和评估结论塞进知识库** —— 评估会虚高。
    #    规律：**任何讨论本项目评估的文档都不能进语料**，
    #    不只是 project2-rag 自己的目录。
    "project3-rag-chat",
    "node_modules",
    ".venv",
    ".git",
    "logs",
    ".pytest_cache",
    "__pycache__",
    ".ruff_cache",
    ".mypy_cache",
)


@dataclass
class Doc:
    """一篇文档。`rel_path` 是**相对仓库根**的路径 —— 评估集的 gold_doc 用它。"""

    rel_path: str
    abs_path: Path
    text: str

    @property
    def char_len(self) -> int:
        return len(self.text)


def rel_to_root(p: Path) -> str:
    """统一成 POSIX 风格的相对路径，避免 Windows 反斜杠在评估集里带来麻烦。"""
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def iter_markdown(dirs: tuple[str, ...] = CORPUS_DIRS) -> list[Path]:
    """列出所有语料文件（已排除自我和噪音）。"""
    found: list[Path] = []
    for d in dirs:
        base = REPO_ROOT / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.md")):
            if _is_excluded(p):
                continue
            found.append(p)
    # 额外指定的单文件（不在上面的目录覆盖范围内）
    for rel in EXTRA_FILES:
        p = REPO_ROOT / rel
        if p.exists() and not _is_excluded(p):
            found.append(p)
    # 去重（同一文件可能被多个目录覆盖，比如 "." 和 "ai-lab" 会重叠）
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in found:
        key = p.resolve().as_posix()
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def load_corpus(dirs: tuple[str, ...] = CORPUS_DIRS) -> list[Doc]:
    """读入全部语料。"""
    docs: list[Doc] = []
    for p in iter_markdown(dirs):
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue          # 坏文件跳过，不让整个索引失败
        if not text.strip():
            continue
        docs.append(Doc(rel_path=rel_to_root(p), abs_path=p, text=text))
    return docs


def main() -> int:
    docs = load_corpus()
    print("=" * 74)
    print("  语料加载")
    print("=" * 74)
    print(f"  仓库根：{REPO_ROOT}")
    print(f"  来源目录：{len(CORPUS_DIRS)} 个")
    print()
    total = sum(d.char_len for d in docs)
    print(f"  共 {len(docs)} 篇文档，{total:,} 字符")
    print()
    by_dir: dict[str, int] = {}
    for d in docs:
        top = "/".join(d.rel_path.split("/")[:2])
        by_dir[top] = by_dir.get(top, 0) + 1
    print("  按目录分布：")
    for k in sorted(by_dir):
        print(f"    {by_dir[k]:>3} 篇  {k}")
    print()
    print("  最大的 5 篇：")
    for d in sorted(docs, key=lambda x: -x.char_len)[:5]:
        print(f"    {d.char_len:>7,} 字符  {d.rel_path}")
    print()
    # 自我排除的验证 —— 这个必须成立，否则评估会虚高
    #
    # ⚠️ 判据必须写准，这里踩过一次：
    #    最初的规则是「路径里含 projectN-」，结果把
    #    `project1-stream-chat/` 也拦下了 —— 而**那是评估集的 gold_doc**，
    #    是合法语料（评估题大量引用它）。误报会让整个评估没法跑。
    #
    #    正确的判据：只排除**讨论本项目评估/知识库自身**的文档。
    #    具体就是这两个目录，它们写着指标、ADR 结论、
    #    "哪些题该弃答" —— 进了知识库等于把答案塞进去。
    #
    #    新增姊妹项目时，**要显式加进这个列表**，不能靠模式猜
    #    （猜宽了误伤 gold_doc，猜窄了漏掉污染）。
    self_dirs = ("project2-rag/", "project3-rag-chat/")
    leaked = [d for d in docs
              if any(s in d.rel_path for s in self_dirs)]
    if leaked:
        print("  自我排除检查：❌ 混入了讨论本项目评估的文档：")
        for d in leaked:
            print(f"      {d.rel_path}")
        print("      → 这会让评估虚高（知识库里有了答案与结论）。")
        print("      → 修法：把该目录加进 EXCLUDE_PARTS 与这里的 self_dirs。")
    else:
        print("  自我排除检查：✅ 没有讨论本项目评估的文档混进来")
    print("=" * 74)
    return 1 if leaked else 0


if __name__ == "__main__":
    raise SystemExit(main())
