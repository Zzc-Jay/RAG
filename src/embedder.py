from __future__ import annotations
import hashlib
import os
import time

import chromadb
from dashscope import TextEmbedding

from config import BATCH_SIZE, EMBEDDING_MODEL, TOP_K, DASHSCOPE_API_KEY
from retry import retry_call
from logging_config import get_logger
from embedding_cache import lookup, store, cache_hit_rate, get_stats as get_cache_stats

logger = get_logger("embedder")


def _collection_name(kb_name: str) -> str:
    """将知识库名转为 ChromaDB 合法的 collection 名称。

    ChromaDB 只允许 [a-zA-Z0-9._-]，中文等非 ASCII 字符用 MD5 哈希映射。
    """
    h = hashlib.md5(kb_name.encode("utf-8")).hexdigest()[:12]
    return f"kb_{h}"


def _get_collection(kb_name: str):
    """获取或创建 ChromaDB collection，每个知识库一个 collection。"""
    from kb_manager import get_chroma_dir_for_kb
    persist_dir = os.path.join(get_chroma_dir_for_kb(), kb_name)
    client = chromadb.PersistentClient(path=persist_dir)
    name = _collection_name(kb_name)
    try:
        return client.get_collection(name)
    except Exception:
        return client.create_collection(name)


def _call_embedding_api(batch: list[str]) -> tuple[list[list[float]], int]:
    """调用 DashScope TextEmbedding API（带重试）。

    返回: (embeddings列表, input_tokens)
    """
    resp = retry_call(
        TextEmbedding.call,
        model=EMBEDDING_MODEL,
        input=batch,
        api_key=DASHSCOPE_API_KEY,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"嵌入失败: {resp.message}")

    # DashScope TextEmbedding: usage 是 dict，用 .get() 取值
    input_tokens = 0
    try:
        input_tokens = resp.usage.get("total_tokens", 0) or 0
    except (AttributeError, TypeError):
        pass

    embeddings = [item["embedding"] for item in resp.output["embeddings"]]
    return embeddings, input_tokens


def _batch_embed(texts: list[str]) -> tuple[list[list[float]], int]:
    """分批嵌入，优先从缓存读取，缓存未命中才调 API。

    返回: (embeddings列表, 实际调用 API 消耗的 input_tokens)
    """
    n = len(texts)
    if n == 0:
        return [], 0

    # 1. 批量查缓存
    cached, hit_indices = lookup(texts)
    hit_set = set(hit_indices)
    miss_indices = [i for i in range(n) if i not in hit_set]

    hit = len(hit_indices)
    miss = len(miss_indices)
    logger.info(f"嵌入缓存: {hit} 命中 + {miss} 未命中 (命中率 {cache_hit_rate():.0%})")

    # 2. 未命中的分批调用 API
    new_embeddings: dict[int, list[float]] = {}
    total_tokens = 0

    if miss_indices:
        miss_texts = [texts[i] for i in miss_indices]
        total_batches = (len(miss_texts) + BATCH_SIZE - 1) // BATCH_SIZE

        for bi in range(0, len(miss_texts), BATCH_SIZE):
            batch = miss_texts[bi:bi + BATCH_SIZE]
            batch_num = bi // BATCH_SIZE + 1
            t0 = time.perf_counter()
            embeddings, tokens = _call_embedding_api(batch)
            total_tokens += tokens
            elapsed = time.perf_counter() - t0
            logger.info(f"嵌入批次 {batch_num}/{total_batches} 完成 "
                        f"({len(batch)} 条，{tokens} tokens，耗时 {elapsed:.2f}s)")
            for j, emb in enumerate(embeddings):
                idx = miss_indices[bi + j]
                new_embeddings[idx] = emb

        # 3. 新嵌入写回缓存
        store(miss_texts, [new_embeddings[i] for i in miss_indices])

    # 4. 合并结果（保持原始顺序）
    result: list[list[float]] = []
    for i in range(n):
        if i in hit_set:
            result.append(cached[i])  # type: ignore[arg-type]
        else:
            result.append(new_embeddings[i])

    return result, total_tokens


def add_to_kb(chunks: list[dict], kb_name: str, token_tracker=None) -> int:
    """将 chunks 嵌入并存入知识库（增量 upsert）。

    chunks: [{"text": "...", "source": "...", "page": int, "chunk_idx": int}, ...]
    kb_name: 目标知识库名称
    token_tracker: 可选的 TokenTracker 实例，用于统计用量

    返回: embedding 消耗的 input tokens 总数
    """
    if not chunks:
        return 0

    if not DASHSCOPE_API_KEY:
        raise RuntimeError("请设置 DASHSCOPE_API_KEY 环境变量")

    texts = [c["text"] for c in chunks]
    logger.info(f"开始向量嵌入: {len(texts)} 个文本块 -> 知识库 '{kb_name}'")
    t0 = time.perf_counter()

    embeddings, input_tokens = _batch_embed(texts)

    if token_tracker is not None:
        token_tracker.record_embedding(input_tokens)

    collection = _get_collection(kb_name)

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    for i, c in enumerate(chunks):
        chunk_id = f"{c['source']}_{c['chunk_idx']}"
        ids.append(chunk_id)
        documents.append(c["text"])
        metadatas.append({
            "source": c["source"],
            "page": c["page"],
            "chunk_idx": c["chunk_idx"],
            "has_table": c.get("has_table", False),
            "is_scanned": c.get("is_scanned", False),
        })

    collection.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
    elapsed = time.perf_counter() - t0
    logger.info(f"嵌入完成: {len(chunks)} 个块 -> 知识库 '{kb_name}'，总耗时 {elapsed:.2f}s")


def search(
    query: str,
    kb_name: str,
    top_k: int = TOP_K,
    token_tracker=None,
) -> list[dict]:
    """向量相似度检索。

    返回: [{"text": "...", "source": "...", "page": int, "score": float}, ...]
    """
    if not DASHSCOPE_API_KEY:
        raise RuntimeError("请设置 DASHSCOPE_API_KEY 环境变量")

    t0 = time.perf_counter()
    resp = retry_call(
        TextEmbedding.call,
        model=EMBEDDING_MODEL,
        input=[query],
        api_key=DASHSCOPE_API_KEY,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"查询嵌入失败: {resp.message}")

    # 提取 token 用量（TextEmbedding: usage 是 dict）
    input_tokens = 0
    try:
        input_tokens = resp.usage.get("total_tokens", 0) or 0
    except (AttributeError, TypeError):
        pass
    if token_tracker is not None and input_tokens > 0:
        token_tracker.record_embedding(input_tokens)

    query_vec = resp.output["embeddings"][0]["embedding"]

    collection = _get_collection(kb_name)
    results = collection.query(query_embeddings=[query_vec], n_results=top_k)

    docs: list[dict] = []
    if results["documents"] and results["documents"][0]:
        for i in range(len(results["documents"][0])):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            distance = results["distances"][0][i] if results["distances"] else 0.0
            docs.append({
                "text": results["documents"][0][i],
                "source": meta.get("source", "unknown"),
                "page": meta.get("page", 0),
                "score": 1.0 - distance,
                "has_table": meta.get("has_table", False),
                "is_scanned": meta.get("is_scanned", False),
            })

    elapsed = time.perf_counter() - t0
    logger.info(f"向量检索完成: '{query[:50]}...' -> {len(docs)} 条结果，耗时 {elapsed:.2f}s")
    return docs


def delete_doc_chunks(kb_name: str, source: str) -> None:
    """从知识库中删除指定文档的全部 chunk。"""
    try:
        collection = _get_collection(kb_name)
        existing = collection.get(where={"source": source})
        if existing and existing["ids"]:
            collection.delete(ids=existing["ids"])
            logger.info(f"已从知识库 '{kb_name}' 删除文档 '{source}' 的 {len(existing['ids'])} 个块")
    except Exception:
        pass


def get_all_chunks(kb_name: str) -> list[dict]:
    """获取知识库中所有 chunk（用于重建 BM25 索引等场景）。

    返回: [{"text": "...", "source": "...", "page": int, "chunk_idx": int}, ...]
    """
    try:
        collection = _get_collection(kb_name)
        data = collection.get()
    except Exception:
        return []

    if not data or not data.get("ids"):
        return []

    chunks: list[dict] = []
    for i in range(len(data["ids"])):
        meta = data["metadatas"][i] if data.get("metadatas") else {}
        chunks.append({
            "text": data["documents"][i] if data.get("documents") else "",
            "source": meta.get("source", ""),
            "page": meta.get("page", 0),
            "chunk_idx": meta.get("chunk_idx", i),
            "has_table": meta.get("has_table", False),
            "is_scanned": meta.get("is_scanned", False),
        })
    return chunks
