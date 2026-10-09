#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
项目二 · RAG 核心实现

模块分工：

    embeddings.py  embedding 接口 + 本地/API 两种实现（ADR-002）
    chunking.py    切块策略，三种可对比（ADR-001）
    corpus.py      语料加载：从仓库文档导入知识库
    store.py       存储层：SQLite + 向量（ADR-003）
    retrieve.py    检索：稠密 / 稀疏 / RRF 混合（ADR-004）

设计原则（与项目一一致）：
  - 每个模块可单独跑自测，不依赖整个链路
  - 「为什么这么选」写在 docs/ADR.md，代码里只标注关联的 ADR 编号
"""

from __future__ import annotations

__all__ = ["embeddings", "chunking", "corpus", "store", "retrieve"]
