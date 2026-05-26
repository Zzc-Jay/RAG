from __future__ import annotations
import os
import pickle

import jieba
from rank_bm25 import BM25Okapi

def _bm25_path(kb_name: str) -> str:
    from kb_manager import get_bm25_dir_for_kb
    bm25_dir = get_bm25_dir_for_kb()
    kb_dir = os.path.join(bm25_dir, kb_name)
    os.makedirs(kb_dir, exist_ok=True)
    return os.path.join(kb_dir, "bm25.pkl")


def _tokenize(text: str) -> list[str]:
    """jieba 精确模式分词，去空格后返回词列表。"""
    return [w for w in jieba.cut(text) if w.strip()]


def build_index(chunks: list[dict], kb_name: str) -> None:
    """为知识库构建 BM25 索引并持久化到磁盘。

    chunks: list[{"text": "...", "source": "...", "page": int, "chunk_idx": int}]
    kb_name: 知识库名称
    """
    corpus: list[list[str]] = []
    docs: list[dict] = []

    for c in chunks:
        tokens = _tokenize(c["text"])
        if not tokens:
            continue
        corpus.append(tokens)
        docs.append(c)

    if not corpus:
        raise RuntimeError("知识库没有有效文本，无法构建 BM25 索引")

    model = BM25Okapi(corpus)

    path = _bm25_path(kb_name)
    with open(path, "wb") as f:
        pickle.dump({"corpus": corpus, "model": model, "docs": docs}, f)


def load_index(kb_name: str) -> dict | None:
    """加载 BM25 索引，不存在则返回 None。"""
    path = _bm25_path(kb_name)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return pickle.load(f)


def search_bm25(query: str, kb_name: str, top_k: int = 10) -> list[dict]:
    """BM25 关键词搜索，返回带分数的文档列表。

    query: 搜索查询
    kb_name: 知识库名称
    top_k: 返回结果数

    返回: [{"text": "...", "source": "...", "page": int, "score": float}, ...]
    """
    data = load_index(kb_name)
    if data is None:
        return []

    tokens = _tokenize(query)
    scores = data["model"].get_scores(tokens)
    # 取 top_k
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]

    results: list[dict] = []
    for idx, score in ranked:
        if score <= 0:
            continue
        doc = data["docs"][idx]
        results.append({
            "text": doc["text"],
            "source": doc["source"],
            "page": doc["page"],
            "score": float(score),
            "has_table": doc.get("has_table", False),
            "is_scanned": doc.get("is_scanned", False),
        })
    return results


def delete_index(kb_name: str) -> None:
    """删除知识库的 BM25 索引文件。"""
    path = _bm25_path(kb_name)
    if os.path.exists(path):
        os.remove(path)
