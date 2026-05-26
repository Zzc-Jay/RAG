from __future__ import annotations

from config import TOP_K, RRF_K
from embedder import search as vector_search
from bm25_index import search_bm25


def _doc_key(doc: dict) -> str:
    """文档唯一标识（同源 + 同 chunk 视为同一文档）。"""
    return f"{doc.get('source', '')}_{doc.get('page', 0)}_{doc.get('text', '')[:80]}"


def _rrf_fuse(
    vector_results: list[dict],
    bm25_results: list[dict],
    top_k: int,
    rrf_k: int = 60,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
) -> list[dict]:
    """RRF (Reciprocal Rank Fusion) 融合两路检索结果。

    score(d) = w_vector / (k + rank_vector) + w_bm25 / (k + rank_bm25)
    其中 k = rrf_k，rank_i(d) 是文档 d 在第 i 路结果中的排名（从 1 开始）。
    权重越大则该路检索结果对最终排序影响越大。
    """
    seen: dict[str, dict] = {}

    for rank, doc in enumerate(vector_results, start=1):
        key = _doc_key(doc)
        if key not in seen:
            seen[key] = {"doc": doc, "vector_rank": rank, "bm25_rank": None}

    for rank, doc in enumerate(bm25_results, start=1):
        key = _doc_key(doc)
        if key not in seen:
            seen[key] = {"doc": doc, "vector_rank": None, "bm25_rank": rank}
        else:
            seen[key]["bm25_rank"] = rank

    fused: list[dict] = []
    for entry in seen.values():
        doc = entry["doc"]
        score = 0.0
        if entry["vector_rank"] is not None:
            score += vector_weight / (rrf_k + entry["vector_rank"])
        if entry["bm25_rank"] is not None:
            score += bm25_weight / (rrf_k + entry["bm25_rank"])
        doc["score"] = score
        fused.append(doc)

    fused.sort(key=lambda d: d["score"], reverse=True)
    return fused[:top_k]


def retrieve(
    query: str,
    kb_name: str,
    top_k: int = TOP_K,
    rrf_k: int = RRF_K,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
    token_tracker=None,
    rewrite: bool = False,
    history_text: str = "",
    rerank: bool = False,
) -> list[dict]:
    """混合检索入口 — 可选查询改写 + 粗排 → 精排管线。

    管线流程：
    1. [可选] 查询改写：基于对话历史将模糊问题独立化
    2. 粗排：向量 + BM25 → 加权 RRF 融合
    3. [可选] 精排：LLM 对候选文档打分重排序

    query: 用户问题
    kb_name: 知识库名称
    top_k: 最终返回的文档数
    rrf_k: RRF 融合参数，越大排名差异影响越小
    vector_weight: 向量检索权重（0=完全忽略语义检索）
    bm25_weight: BM25 关键词检索权重（0=完全忽略关键词检索）
    token_tracker: 可选的 TokenTracker 实例
    rewrite: 是否启用查询改写（需提供 history_text）
    history_text: 对话历史文本（用于查询改写）
    rerank: 是否启用 LLM 精排
    """
    # Step 1: 查询改写
    effective_query = query
    if rewrite and history_text:
        from query_rewriter import rewrite_query
        effective_query = rewrite_query(query, history_text)

    # Step 2: 粗排 — 决定 fetch_k
    if rerank:
        fetch_k = max(top_k * 4, 20)
    else:
        fetch_k = max(top_k * 2, 10)

    vector_results = vector_search(effective_query, kb_name, fetch_k, token_tracker=token_tracker)
    bm25_results = search_bm25(effective_query, kb_name, fetch_k)

    fused = _rrf_fuse(vector_results, bm25_results, fetch_k, rrf_k, vector_weight, bm25_weight)

    # Step 3: 精排
    if rerank and len(fused) > top_k:
        from reranker import rerank as rerank_docs
        fused = rerank_docs(effective_query, fused, top_k)

    return fused[:top_k]
