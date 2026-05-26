"""嵌入缓存测试 — 内容寻址存储（SHA256 hash → embedding）。

教学点：
1. 相同文本 → 相同 hash → 相同缓存 key，保证幂等
2. 跨知识库共享：同一段文字只调用一次 API
3. SQLite WAL 模式支持并发读
4. JSON 序列化 embedding 向量（1024 维 × 4 字节 ≈ 4KB/条）
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from embedding_cache import (
    _hash_text,
    lookup,
    store,
    get_stats,
    cache_hit_rate,
    clear,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个测试前后清空缓存，保证隔离。"""
    clear()
    yield
    clear()


# ═══════════════════════════════════════════════════════════════
# 哈希
# ═══════════════════════════════════════════════════════════════

def test_hash_deterministic():
    """相同文本必须产生相同 hash。"""
    h1 = _hash_text("hello world")
    h2 = _hash_text("hello world")
    assert h1 == h2
    assert len(h1) == 64  # SHA256 hex


def test_hash_different():
    """不同文本必须产生不同 hash。"""
    h1 = _hash_text("hello")
    h2 = _hash_text("world")
    assert h1 != h2


def test_hash_empty_string():
    """空字符串也应该能 hash。"""
    h = _hash_text("")
    assert len(h) == 64


def test_hash_unicode():
    """中文等 Unicode 文本也能正确 hash。"""
    h = _hash_text("你好世界")
    assert len(h) == 64


# ═══════════════════════════════════════════════════════════════
# 基本读写
# ═══════════════════════════════════════════════════════════════

def test_lookup_miss():
    """空缓存中查询应该全部未命中。"""
    texts = ["text one", "text two", "text three"]
    results, hit_indices = lookup(texts)
    assert len(results) == 3
    assert all(r is None for r in results)
    assert hit_indices == []


def test_store_and_lookup():
    """写入后查询应该命中。"""
    texts = ["alpha", "beta", "gamma"]
    embeddings = [
        [0.1, 0.2, 0.3],
        [0.4, 0.5, 0.6],
        [0.7, 0.8, 0.9],
    ]
    store(texts, embeddings)

    results, hit_indices = lookup(texts)
    assert len(hit_indices) == 3
    assert hit_indices == [0, 1, 2]
    for i, emb in enumerate(embeddings):
        assert results[i] == pytest.approx(emb)


def test_store_partial_overlap():
    """部分文本已缓存时，lookup 正确区分命中和未命中。"""
    store(["cached_text"], [[1.0, 2.0, 3.0]])

    texts = ["cached_text", "new_text", "cached_text"]
    results, hit_indices = lookup(texts)

    assert hit_indices == [0, 2]
    assert results[0] == pytest.approx([1.0, 2.0, 3.0])
    assert results[1] is None
    assert results[2] == pytest.approx([1.0, 2.0, 3.0])


def test_store_idempotent():
    """重复写入相同文本不会报错（INSERT OR IGNORE）。"""
    texts = ["unique"]
    store(texts, [[0.5, 0.5]])
    store(texts, [[0.5, 0.5]])  # 不应抛出异常

    results, hit_indices = lookup(texts)
    assert len(hit_indices) == 1


def test_store_mismatched_lengths():
    """texts 和 embeddings 长度不一致时应抛出异常。"""
    with pytest.raises(ValueError, match="长度不匹配"):
        store(["a", "b"], [[1.0, 2.0]])


def test_store_empty_lists():
    """空列表写入不应报错。"""
    store([], [])  # 不抛异常


# ═══════════════════════════════════════════════════════════════
# 统计
# ═══════════════════════════════════════════════════════════════

def test_stats_initial():
    """初始状态统计全为 0。"""
    s = get_stats()
    assert s["hits"] == 0
    assert s["misses"] == 0
    assert s["total_cached"] == 0


def test_stats_after_operations():
    """多次操作后统计应正确累加。"""
    store(["a", "b"], [[1.0], [2.0]])

    lookup(["a", "b", "c"])  # 2 hits, 1 miss
    s = get_stats()
    assert s["hits"] == 2
    assert s["misses"] == 1
    assert s["total_cached"] == 2


def test_cache_hit_rate_zero():
    """无操作时命中率为 0。"""
    clear()
    assert cache_hit_rate() == 0.0


def test_cache_hit_rate_half():
    """2 命中 2 未命中 = 50%。"""
    store(["x"], [[0.0]])
    lookup(["x", "y"])  # 1 hit, 1 miss
    assert cache_hit_rate() == 0.5


def test_cache_hit_rate_full():
    """全部命中 = 100%。"""
    store(["z"], [[0.0]])
    lookup(["z"])  # 1 hit, 0 miss
    assert cache_hit_rate() == 1.0


# ═══════════════════════════════════════════════════════════════
# 清空
# ═══════════════════════════════════════════════════════════════

def test_clear_removes_data():
    """清空后缓存数据不存在。"""
    store(["data"], [[1.0, 2.0, 3.0]])
    assert get_stats()["total_cached"] == 1

    count = clear()
    assert count == 1
    assert get_stats()["total_cached"] == 0

    results, hit_indices = lookup(["data"])
    assert hit_indices == []


def test_clear_resets_stats():
    """清空后统计归零。"""
    store(["a", "b"], [[1.0], [2.0]])
    lookup(["a", "c"])  # 1 hit, 1 miss

    clear()
    s = get_stats()
    assert s["hits"] == 0
    assert s["misses"] == 0
    assert s["total_cached"] == 0


def test_clear_empty():
    """清空空缓存应返回 0。"""
    count = clear()
    assert count == 0


# ═══════════════════════════════════════════════════════════════
# 跨知识库共享（核心价值）
# ═══════════════════════════════════════════════════════════════

def test_cross_kb_sharing():
    """同一文本无论被哪个知识库引用，缓存只需一次 API 调用。

    模拟场景：
    - 知识库 A 上传 doc1，包含文本 "Python is great"
    - 知识库 B 上传 doc2，也包含文本 "Python is great"
    - 第二次不该再查缓存未命中
    """
    shared_text = "Python is a great programming language for data science."

    # 第一次：未命中
    results1, hits1 = lookup([shared_text])
    assert hits1 == []
    assert results1[0] is None

    # 写入缓存（模拟 API 返回的 embedding）
    fake_embedding = [[float(i) for i in range(10)]]
    store([shared_text], fake_embedding)

    # 第二次（模拟另一个知识库引用相同文本）：命中
    results2, hits2 = lookup([shared_text])
    assert hits2 == [0]
    assert results2[0] == pytest.approx(fake_embedding[0])


# ═══════════════════════════════════════════════════════════════
# 大量文本性能（不测试实际 API）
# ═══════════════════════════════════════════════════════════════

def test_large_batch():
    """大批量文本的缓存读写。"""
    n = 100
    texts = [f"document text number {i}" for i in range(n)]
    embeddings = [[float(i), float(i + 1)] for i in range(n)]

    store(texts, embeddings)

    # 全部命中
    results, hit_indices = lookup(texts)
    assert len(hit_indices) == n

    s = get_stats()
    assert s["total_cached"] == n


def test_mixed_large_batch():
    """一半命中、一半未命中的大批量场景。"""
    cached_texts = [f"cached_{i}" for i in range(50)]
    cached_embs = [[float(i)] for i in range(50)]
    store(cached_texts, cached_embs)

    new_texts = [f"new_{i}" for i in range(50)]
    all_texts = cached_texts + new_texts

    results, hit_indices = lookup(all_texts)
    assert len(hit_indices) == 50
    assert hit_indices == list(range(50))

    for i in range(50):
        assert results[i] is not None  # 命中
    for i in range(50, 100):
        assert results[i] is None  # 未命中
