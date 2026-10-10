#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
存储层：SQLite 存块 + 向量 + 稀疏倒排（见 ADR-003）

## 为什么是 SQLite

  - **零安装**：标准库自带，不需要 Docker（本机虚拟化没开，见 ADR-003）
  - **单文件**：一个 .db 就是完整索引，方便删了重建、也方便对比不同策略
  - **够用且能暴露边界**：几百到几千块用暴力余弦完全没问题，
    你会**亲身体会到"够用"的边界在哪**，这比直接上 pgvector 更有教学价值

## 为什么向量存 BLOB 而不是 JSON

  256 维 float32 = 1024 字节；JSON 文本要约 4~6 KB。
  用 `array` 模块打包成二进制，读取也快得多。

## 稀疏检索为什么不用 FTS5

  实测（probe_env.py）：
    - FTS5 默认 unicode61 对中文按**整块**切 → '函数'、'作用域' 全部命中 0
    - trigram 分词器**要求查询至少 3 字符** → '函数' 这种 2 字词依然命中 0
  而中文里 2 字关键词（向量、切片、函数、召回、降级）极其常见，这个限制不可接受。

  于是**自建字符 n-gram 倒排**：做 embedding 时本来就要提取 n-gram，
  顺手建倒排成本几乎为零，且完全可控。
"""

from __future__ import annotations

import array
import json
import math
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from .chunking import Chunk
from .embeddings import Embedder, _ngrams  # noqa: PLC2701  同包内部工具，刻意复用

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path  TEXT NOT NULL,
    heading_path TEXT NOT NULL DEFAULT '',
    chunk_index  INTEGER NOT NULL,
    strategy     TEXT NOT NULL,
    text         TEXT NOT NULL,
    char_len     INTEGER NOT NULL,
    dim          INTEGER NOT NULL,
    vec          BLOB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_path);

-- 稀疏倒排：term -> chunk_id，带词频（便于按 TF 打分）
CREATE TABLE IF NOT EXISTS postings (
    term     TEXT NOT NULL,
    chunk_id INTEGER NOT NULL,
    tf       INTEGER NOT NULL,
    PRIMARY KEY (term, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_postings_term ON postings(term);
CREATE INDEX IF NOT EXISTS idx_postings_chunk ON postings(chunk_id);
"""


def pack_vector(vec: list[float]) -> bytes:
    """float32 打包 —— 256 维只占 1 KB（JSON 要 4~6 KB）。"""
    return array.array("f", vec).tobytes()


def unpack_vector(blob: bytes, dim: int) -> array.array:
    a = array.array("f")
    a.frombytes(blob)
    # 防御：数据损坏时长度不符，早点报出来比算出错分数好
    if len(a) != dim:
        raise ValueError(f"向量长度不符：期望 {dim}，实际 {len(a)}")
    return a


@dataclass
class StoredChunk:
    id: int
    source_path: str
    heading_path: str
    chunk_index: int
    strategy: str
    text: str
    char_len: int


class Store:
    """
    索引的读写入口。

    `signature` 记录了"这份索引用什么配方建的"（embedder 配方 + 切块策略）。
    换 embedder 或换策略都必须重建 —— 混用会得到毫无意义的相似度。
    这是 ADR-002/003 里强调的"可替换"必须付出的代价，所以要显式检查。
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # ================================================================
        # check_same_thread=False + 一把锁 —— 为了能在 Web 服务里跨线程用
        # ================================================================
        #
        # 【踩过的坑】sqlite3 默认**禁止**连接跨线程使用：
        #     SQLite objects created in a thread can only be used in
        #     that same thread.
        #
        # 评估脚本全程单线程，所以从没碰到。但 Web 服务会：
        #   · 连接在**主线程**（启动时）创建
        #   · 检索段在**线程池**里跑（为了不阻塞事件循环）
        # 于是第一次请求就抛 ProgrammingError。
        #
        # 更麻烦的是**症状**：`/api/ask` 是异步生成器，
        # 第一段检索就抛异常 → StreamingResponse **连响应头都没发出去** →
        # 客户端一直等到超时（180s），看起来像"服务卡死"，
        # 而真实原因是一句话的线程限制。
        #
        # 修法：关掉线程检查，配一把可重入锁把访问串行化。
        # 注意 `check_same_thread=False` 本身**不保证**并发安全 ——
        # 它只是解除限制，安全要靠调用方（也就是这里的锁）。
        # 本项目读多写少、查询都是毫秒级，一把全局锁完全够。
        self._lock = threading.RLock()
        self.con = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        # WAL 让读写不互相阻塞；大批量写入也更快
        with self._lock:
            self.con.execute("PRAGMA journal_mode=WAL")
            self.con.executescript(SCHEMA)
            self.con.commit()

    # ---------------- 元数据 ----------------

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self.con.execute(
                "SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self.con.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    @property
    def signature(self) -> str:
        """当前索引的配方（没有则空串）。"""
        return self.get_meta("signature") or ""

    @property
    def count(self) -> int:
        with self._lock:
            return self.con.execute(
                "SELECT count(*) c FROM chunks").fetchone()["c"]

    # ---------------- 写入 ----------------

    def reset(self) -> None:
        """清空所有数据（重建索引前调用）。"""
        with self._lock:
            self.con.executescript(
                "DELETE FROM chunks; DELETE FROM postings; DELETE FROM meta;"
            )
            self.con.commit()

    def build(
        self,
        chunks: list[Chunk],
        embedder: Embedder,
        *,
        batch_size: int = 64,
        progress: bool = False,
    ) -> None:
        """
        建索引：算向量 + 建倒排。

        批量 embedding 而不是一条一条 —— API 实现下这是数量级的差别。
        """
        self.reset()
        dim = embedder.dim
        written = 0

        for start in range(0, len(chunks), batch_size):
            batch = chunks[start:start + batch_size]
            texts = [c.text for c in batch]
            vecs = embedder.embed(texts)
            if vecs and len(vecs[0]) != dim:
                # 本地实现的 dim 在构造时就定了；API 实现要等第一次调用才知道
                dim = len(vecs[0])

            for c, v in zip(batch, vecs):
                with self._lock:
                    cur = self.con.execute(
                        "INSERT INTO chunks(source_path,heading_path,chunk_index,"
                        "strategy,text,char_len,dim,vec) VALUES(?,?,?,?,?,?,?,?)",
                        (c.source_path, c.heading_path, c.chunk_index, c.strategy,
                         c.text, len(c.text), dim, pack_vector(v)),
                        )
                    cid = cur.lastrowid
                    # 倒排：用与 embedding 同一套 n-gram，保证两路"看到的词"一致
                    counts: dict[str, int] = {}
                    for g in _ngrams(c.text):
                        counts[g] = counts.get(g, 0) + 1
                    self.con.executemany(
                        "INSERT OR REPLACE INTO postings(term,chunk_id,tf) "
                        "VALUES(?,?,?)",
                        [(g, cid, tf) for g, tf in counts.items()],
                    )
                written += 1

            with self._lock:
                self.con.commit()
            if progress:
                print(f"    已写入 {written}/{len(chunks)}", end="\r")

        if progress:
            print()

        self.set_meta("signature", embedder.signature)
        self.set_meta("dim", str(dim))
        self.set_meta("chunks", str(written))
        self.set_meta("strategy", chunks[0].strategy if chunks else "")
        with self._lock:
            self.con.commit()

    # ---------------- 读取 ----------------

    def all_chunks(self) -> list[StoredChunk]:
        with self._lock:
            rows = self.con.execute(
                "SELECT id,source_path,heading_path,chunk_index,strategy,text,char_len "
                "FROM chunks ORDER BY id"
            ).fetchall()
        return [StoredChunk(**dict(r)) for r in rows]

    def load_matrix(self) -> tuple[list[StoredChunk], list[array.array], int]:
        """
        把全部向量读进内存 —— 暴力检索的前提。

        这是个**刻意的设计**：几百到几千块全内存完全没问题，
        而且能让"暴力 vs 索引"的对比变得具体（ADR-003 的 P1 → P2）。
        """
        dim = int(self.get_meta("dim") or 0)
        with self._lock:
            rows = self.con.execute(
                "SELECT id,source_path,heading_path,chunk_index,strategy,text,char_len,"
                "vec,dim FROM chunks ORDER BY id"
            ).fetchall()
        metas: list[StoredChunk] = []
        vecs: list[array.array] = []
        for r in rows:
            d = r["dim"]
            metas.append(StoredChunk(
                id=r["id"], source_path=r["source_path"],
                heading_path=r["heading_path"], chunk_index=r["chunk_index"],
                strategy=r["strategy"], text=r["text"], char_len=r["char_len"],
            ))
            vecs.append(unpack_vector(r["vec"], d))
            dim = d
        return metas, vecs, dim

    def query_postings(self, terms: list[str]) -> dict[int, float]:
        """
        稀疏检索的候选集：返回 {chunk_id: 该块上这些 term 的总加权词频}。

        只用来**选候选**和粗排；精确打分（逐 term 的 BM25）在 retrieve 层做，
        因为那需要每个 term 在每个块上的真实 tf（见 postings_for）。

        存储层不该懂排序，所以这里只返回原始统计量。
        """
        if not terms:
            return {}
        out: dict[int, float] = {}
        batch = 400
        for i in range(0, len(terms), batch):
            part = terms[i:i + batch]
            ph = ",".join("?" * len(part))
            with self._lock:
                rows = self.con.execute(
                    f"SELECT chunk_id, tf FROM postings WHERE term IN ({ph})", part
                ).fetchall()
            for row in rows:
                cid = row["chunk_id"]
                out[cid] = out.get(cid, 0.0) + row["tf"]
        return out

    def postings_for(self, terms: list[str]) -> dict[str, dict[int, int]]:
        """
        返回 {term: {chunk_id: tf}} —— 只取给定 term 的倒排项。

        ⚠️ 第一版为了省一次查询，把 tf 近似成 1（"出现即计一次"），
        这在 BM25 里是错的：BM25 的核心之一就是**词频饱和**
        （tf 从 1 到 2 的收益远大于从 10 到 11）。
        把 tf 抹平等于放弃了 BM25 的一半机制，指标会失真。
        """
        if not terms:
            return {}
        out: dict[str, dict[int, int]] = {}
        batch = 200                       # term 数量分批（每个 term 展开成多行结果）
        for i in range(0, len(terms), batch):
            part = terms[i:i + batch]
            ph = ",".join("?" * len(part))
            with self._lock:
                rows = self.con.execute(
                    f"SELECT term, chunk_id, tf FROM postings WHERE term IN ({ph})",
                    part
                ).fetchall()
            for row in rows:
                out.setdefault(row["term"], {})[row["chunk_id"]] = row["tf"]
        return out

    def doc_freq(self, terms: list[str]) -> dict[str, int]:
        """每个 term 出现在多少个块里 —— 算 IDF 用。"""
        out: dict[str, int] = {}
        batch = 400
        for i in range(0, len(terms), batch):
            part = terms[i:i + batch]
            ph = ",".join("?" * len(part))
            with self._lock:
                rows = self.con.execute(
                    f"SELECT term, count(*) c FROM postings WHERE term IN ({ph}) "
                    "GROUP BY term", part
                ).fetchall()
            for row in rows:
                out[row["term"]] = row["c"]
        return out

    def close(self) -> None:
        with self._lock:
            self.con.close()

    # 支持 with 语句
    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def cosine(a: array.array, b: array.array) -> float:
    """
    余弦相似度。

    两边都已 L2 归一化，所以直接点积 —— 这也是选归一化向量的原因：
    检索热路径上少一次除法。
    """
    return sum(x * y for x, y in zip(a, b))


def cosine_np(a, b) -> float:  # pragma: no cover - 可选加速路径
    """numpy 版本（装了 numpy 时用）。留作对比暴力 vs 向量化的速度差。"""
    import numpy as np
    return float(np.dot(a, b))


def l2_norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))
