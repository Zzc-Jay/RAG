"""嵌入向量缓存 — 基于文本 hash 的持久化缓存，避免重复调用 Embedding API。

教学点 — 内容寻址存储（Content-Addressable Storage）：
1. 对文本内容做 SHA256 hash，相同文本 = 相同 hash = 相同嵌入向量
2. 跨知识库共享：同一段文字上传到 A 库和 B 库，只调用一次 API
3. SQLite 做持久化：重启后缓存不丢失，磁盘占用小（1024 维 × 4 字节 ≈ 4KB/条）
4. 命中率 = 缓存命中数 / (命中数 + 新增数)

成本节约：DashScope text-embedding-v3 定价 ¥0.0005/1K tokens
以 800 字/块的典型 chunk 为例，约 400 tokens = ¥0.0002/块
缓存 1000 条可节约 ¥0.2，10000 条可节约 ¥2
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading

from config import DATA_DIR

CACHE_DIR = os.path.join(DATA_DIR, "embedding_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
DB_PATH = os.path.join(CACHE_DIR, "cache.db")

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """获取线程本地的 SQLite 连接（线程安全）。"""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute(
            "CREATE TABLE IF NOT EXISTS embedding_cache ("
            "  text_hash TEXT PRIMARY KEY,"
            "  embedding TEXT NOT NULL"
            ")"
        )
    return _local.conn


def _hash_text(text: str) -> str:
    """计算文本的 SHA256 hash，作为缓存 key。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── 统计 ────────────────────────────────────────────────────────────
_stats = {"hits": 0, "misses": 0}


def get_stats() -> dict[str, int]:
    """返回缓存统计：hits, misses, total_cached。"""
    conn = _get_conn()
    row = conn.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()
    return {
        "hits": _stats["hits"],
        "misses": _stats["misses"],
        "total_cached": row[0] if row else 0,
    }


def cache_hit_rate() -> float:
    """缓存命中率 0.0 ~ 1.0。"""
    total = _stats["hits"] + _stats["misses"]
    if total == 0:
        return 0.0
    return _stats["hits"] / total


# ── 核心操作 ────────────────────────────────────────────────────────

def lookup(texts: list[str]) -> tuple[list[list[float] | None], list[int]]:
    """批量查缓存。返回 (embeddings列表, 命中索引列表)。

    texts: 文本列表
    返回: ([embedding_or_None, ...], [hit_index, ...])
      - embedding_or_None: 命中时为 list[float]，未命中时为 None
      - hit_index: 命中的原始索引列表
    """
    conn = _get_conn()
    results: list[list[float] | None] = [None] * len(texts)
    hit_indices: list[int] = []

    for i, text in enumerate(texts):
        h = _hash_text(text)
        row = conn.execute(
            "SELECT embedding FROM embedding_cache WHERE text_hash = ?", (h,)
        ).fetchone()
        if row:
            results[i] = json.loads(row[0])
            hit_indices.append(i)
            _stats["hits"] += 1
        else:
            _stats["misses"] += 1

    return results, hit_indices


def store(texts: list[str], embeddings: list[list[float]]) -> None:
    """批量写入缓存。texts 和 embeddings 需一一对应。"""
    if len(texts) != len(embeddings):
        raise ValueError(f"texts 和 embeddings 长度不匹配: {len(texts)} vs {len(embeddings)}")

    conn = _get_conn()
    rows = [
        (_hash_text(t), json.dumps(e, separators=(",", ":")))
        for t, e in zip(texts, embeddings)
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO embedding_cache (text_hash, embedding) VALUES (?, ?)",
        rows,
    )
    conn.commit()


def clear() -> int:
    """清空缓存，返回删除的记录数。"""
    conn = _get_conn()
    row = conn.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()
    count = row[0] if row else 0
    conn.execute("DELETE FROM embedding_cache")
    conn.commit()
    _stats["hits"] = 0
    _stats["misses"] = 0
    return count
