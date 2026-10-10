#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实体→来源映射 —— 机械校验"引用的文档是否属于问题问的那个对象"（ADR-012）

## 要解决的真实失败

对抗集抓到的跨文档混淆：

    问："项目一的测试用例文件叫什么名字？"

    项目一的文档里**没有**任何测试文件名；
    但 `python_basics/backend/README.md`（**项目二后端篇**）里有
    `tests/test_api.py`。
    模型就拿它来回答**项目一**的问题。

这是**幻觉的一种**，只是不像凭空编造那么明显 ——
它引用的段落确实存在，只是**不属于被问的那个对象**。
提示词加了规则后仍 75% 失败（见 ADR-011），所以必须**机械校验**。

## 能机械校验的部分

关键洞察：**"某个文档属于哪个对象"是可以预先编译成表的**，
不需要语义判断。比如：

    "项目一"  -> ai-lab/projects/project1-stream-chat/**
    "项目二"/"RAG" -> ai-lab/projects/project2-rag/**（但被语料排除了）
    "基础篇"/"第一层" -> ai-lab/python_basics/docs/**
    "后端篇"/"第二层" -> ai-lab/python_basics/backend/**

于是校验规则很简单：

    如果问题里出现了实体 E，那么回答里的每个引用
    **必须**指向 E 名下的文档（或者不属于任何实体的通用文档）。

违反 → 判定为"引用了不属于该对象的资料" → 按弃答处理。

## 不能机械校验的部分（必须诚实说明）

模型对**内容**的错误描述查不出来。比如它说
"`eval/cases.smoke.jsonl` 是测试用例文件" ——
要判断这句话对不对，需要知道那个文件到底是什么，
而那是**外部知识**，机械校验做不到。

所以这个校验的作用是：**把"引用错文档"这一类幻觉彻底堵住**，
把它从"编一个像样的答案"降级成"老实说不知道"。
剩下那类内容级错误，仍然要靠评估集抽查。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Entity:
    """一个可识别的对象（项目、层、章节）及其名下的来源文件前缀。"""

    name: str
    #: 问题里出现这些词就算提到了这个实体
    aliases: list[str]
    #: 名下的文档路径前缀（POSIX 风格）
    path_prefixes: list[str]
    #: 说明（文档里要写清为什么这么划，便于以后扩充）
    note: str = ""


# ======================================================================
# 实体表
# ======================================================================
#
# ⚠️ 设计原则：**宁可漏判，不可误判**。
#    误判的代价是"本来能答的被拒"，那会直接损害可用性。
#    所以：
#      · 只收**路径上能明确归属**的实体
#      · 别名要**足够具体**（"项目一"可以，光写"项目"不行）
#      · 一个文档可以同时属于多个实体，也可以不属于任何实体
#        （不属于任何实体的 = 通用文档，引用它永远不违规）
ENTITIES: list[Entity] = [
    Entity(
        name="项目一",
        aliases=["项目一", "流式对话应用", "project1", "stream-chat"],
        path_prefixes=[
            "ai-lab/projects/project1-stream-chat/",
        ],
        note="项目一的全部文档都在这个目录下",
    ),
    Entity(
        name="项目二",
        aliases=["项目二", "project2", "RAG 项目", "知识库问答"],
        path_prefixes=[
            "ai-lab/projects/project2-rag/",
        ],
        note="项目二的文档被语料排除了，所以实际上不会出现在资料里 —— "
             "保留这条是为了让规则自洽（万一以后不排除）",
    ),
    Entity(
        name="基础篇",
        aliases=["基础篇", "第一层", "第一层基础篇", "python_basics/docs"],
        path_prefixes=[
            "ai-lab/python_basics/docs/",
        ],
        note="第一层：教程 01-10 章 + 附录 A",
    ),
    Entity(
        name="后端篇",
        aliases=["后端篇", "第二层", "第二层后端篇", "python_basics/backend"],
        path_prefixes=[
            "ai-lab/python_basics/backend/",
        ],
        note="第二层：FastAPI 后端六章",
    ),
    Entity(
        name="MCP 实验",
        aliases=["MCP", "mcp-minimal", "MCP Server"],
        path_prefixes=[
            "ai-lab/projects/mcp-minimal/",
        ],
        note="最小 MCP Server 实验",
    ),
]


# ======================================================================
# 校验
# ======================================================================


@dataclass
class ScopeViolation:
    """一处"引用了不属于问题所问对象"的违规。"""

    entity: str                     # 问题里提到的实体
    citation_index: int             # 资料编号（1-based）
    source_path: str                # 那段资料来自哪个文件
    reason: str


@dataclass
class ScopeCheck:
    """一次作用域校验的完整结果，便于报告与调试。"""

    #: 问题里识别出的实体名
    entities_found: list[str] = field(default_factory=list)
    violations: list[ScopeViolation] = field(default_factory=list)
    #: 被允许的来源前缀（多个实体时是并集）
    allowed_prefixes: list[str] = field(default_factory=list)
    #: 问题里提到了实体但资料里**没有任何**该实体的文档
    entity_absent_from_context: bool = False

    @property
    def ok(self) -> bool:
        return not self.violations


#: 编译好的别名 -> Entity 反查表（模块加载时建一次）
_ALIAS_MAP: dict[str, Entity] = {}
for _e in ENTITIES:
    for _a in _e.aliases:
        _ALIAS_MAP[_a.lower()] = _e


def find_entities(question: str) -> list[Entity]:
    """
    找出问题里提到了哪些实体。

    匹配规则：**用别名的完整字面量做子串匹配**（不区分大小写）。
    故意不做模糊匹配 —— 宁可漏判也不能误判（见实体表上方的设计原则）。
    """
    q = question.lower()
    found: list[Entity] = []
    for alias, ent in _ALIAS_MAP.items():
        if alias in q and ent not in found:
            found.append(ent)
    return found


def check_scope(question: str, passages) -> ScopeCheck:
    """
    校验：回答所引用的资料，是否属于问题所问的对象。

    参数 `passages` 是送给模型的资料列表（每个元素要有 `source_path`，
    且顺序与引用编号一一对应）。

    规则：
      · 问题里**没有**提到任何实体 → 不做限制（返回 ok）
      · 问题里提到了实体 E → 每一段资料的 source_path 必须命中
        E 的某个 path_prefix（或者不属于任何实体）
      · 如果**所有**资料都不属于 E → 标记 entity_absent_from_context
        （这时整题都该弃答，因为资料里没有该对象的东西）
    """
    ents = find_entities(question)
    res = ScopeCheck(entities_found=[e.name for e in ents])
    if not ents:
        return res

    allowed = [p for e in ents for p in e.path_prefixes]
    res.allowed_prefixes = allowed
    # 所有实体各自的前缀集合（用于判断"这个来源属于哪个实体"）
    entity_prefixes = {e.name: e.path_prefixes for e in ents}

    def belongs_to_any_entity(path: str) -> bool:
        """这个路径是否属于**任何**已登记的实体（不管是不是问题问的那个）。"""
        return any(path.startswith(p)
                   for e in ENTITIES for p in e.path_prefixes)

    def belongs_to_asked(path: str) -> bool:
        return any(path.startswith(p) for p in allowed)

    n_in_scope = 0
    for i, h in enumerate(passages, 1):
        src = getattr(h, "source_path", "") or ""
        if belongs_to_asked(src):
            n_in_scope += 1
            continue
        if not belongs_to_any_entity(src):
            # 通用文档（不属于任何实体）→ 不违规
            n_in_scope += 1
            continue
        # 属于某个实体，但不是问题问的那个 → 违规
        owner = next((name for name, pfx in entity_prefixes.items() if False), "")
        for e in ENTITIES:
            if any(src.startswith(p) for p in e.path_prefixes):
                owner = e.name
                break
        res.violations.append(ScopeViolation(
            entity="/".join(res.entities_found),
            citation_index=i,
            source_path=src,
            reason=f"该资料属于「{owner}」，而问题问的是「{'/'.join(res.entities_found)}」",
        ))

    # 一段都不属于被问的对象 → 资料里没有它的东西
    if n_in_scope == 0 and passages:
        res.entity_absent_from_context = True
    return res


def violated_citations(check: ScopeCheck) -> list[int]:
    return sorted({v.citation_index for v in check.violations})
