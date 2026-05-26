"""
REST API for RAG Knowledge Base System.

教学点：
1. RESTful 资源设计 — 每个 URL 对应一个资源（知识库/文档），HTTP method 定义操作
2. Pydantic 模型 — 请求/响应类型校验 + FastAPI 自动生成 OpenAPI 文档（/docs）
3. SSE (Server-Sent Events) — 流式问答用单向事件流，比 WebSocket 更简单
4. multipart/form-data — 文件上传通过 FastAPI 的 UploadFile 处理
5. 共享模块 — API 层与 Streamlit UI 共享同一套 src/* 业务模块
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Generator

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from loader import load_document, SUPPORTED_TYPES
from chunker import split_pages
from embedder import add_to_kb, delete_doc_chunks
from bm25_index import build_index, delete_index
from retriever import retrieve
from generator import generate_stream
from kb_manager import (
    create_kb, delete_kb, list_kbs, add_doc, remove_doc, get_kb_docs,
    add_docs_batch, remove_docs_batch,
)
from security import validate_question, validate_kb_name, validate_file_extension
from audit import log_event, get_events, count_events

# ── App ─────────────────────────────────────────────────────────────
app = FastAPI(
    title="RAG Knowledge Base API",
    description="知识库检索问答系统 REST API — 管理知识库、上传文档、问答查询",
    version="2.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════
# Pydantic Models
# ═══════════════════════════════════════════════════════════════

class KBCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, description="知识库名称")


class KBCreateResponse(BaseModel):
    name: str
    message: str


class DocInfo(BaseModel):
    name: str
    pages: int
    chunks: int
    type: str


class KBInfo(BaseModel):
    name: str
    doc_count: int
    docs: list[DocInfo]


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="用户问题")
    top_k: int = Field(default=5, ge=1, le=20, description="返回片段数")
    rrf_k: int = Field(default=60, ge=0, le=120, description="RRF 融合参数")
    vector_weight: float = Field(default=1.0, ge=0.0, le=2.0, description="向量权重 (0=纯关键词)")
    bm25_weight: float = Field(default=1.0, ge=0.0, le=2.0, description="BM25 权重 (0=纯语义)")
    rewrite: bool = Field(default=False, description="是否启用查询改写（需配合 history）")
    history: str = Field(default="", description="对话历史文本（用于查询改写）")
    rerank: bool = Field(default=False, description="是否启用 LLM 精排")


class Reference(BaseModel):
    source: str
    page: int
    text: str
    score: float
    has_table: bool = False
    is_scanned: bool = False


class QueryResponse(BaseModel):
    answer: str
    references: list[Reference]
    query: str


class URLLoadRequest(BaseModel):
    url: str = Field(..., min_length=1, description="网页 URL")


class ErrorResponse(BaseModel):
    error: str


# ── Batch Models ──────────────────────────────────────────────

class BatchUploadItem(BaseModel):
    name: str
    status: str  # "success" | "error"
    pages: int = 0
    chunks: int = 0
    type: str = ""
    error: str = ""


class BatchUploadResponse(BaseModel):
    kb_name: str
    results: list[BatchUploadItem]
    total: int
    success: int
    failed: int


class BatchDeleteRequest(BaseModel):
    doc_names: list[str] = Field(..., min_length=1, max_length=100, description="待删除文档名列表")


class BatchDeleteItem(BaseModel):
    name: str
    status: str
    error: str = ""


class BatchDeleteResponse(BaseModel):
    kb_name: str
    results: list[BatchDeleteItem]
    total: int
    deleted: int


class BatchURLRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=20, description="网页 URL 列表")


class BatchURLItem(BaseModel):
    url: str
    status: str
    name: str = ""
    pages: int = 0
    chunks: int = 0
    error: str = ""


class BatchURLResponse(BaseModel):
    kb_name: str
    results: list[BatchURLItem]
    total: int
    success: int
    failed: int


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _process_and_index(source: str, doc_type: str, display_name: str, kb_name: str,
                       source_category: str = "file") -> DocInfo:
    """通用文档处理管线：加载 → 切分 → 嵌入 → BM25 → 注册。"""
    pages = load_document(source, doc_type)
    if not pages:
        raise HTTPException(400, f"文档内容为空: {display_name}")

    chunks = split_pages(pages, source=display_name)
    if not chunks:
        raise HTTPException(400, f"文本过短无法切分: {display_name}")

    delete_doc_chunks(kb_name, display_name)
    add_to_kb(chunks, kb_name)

    all_chunks = []
    from embedder import get_all_chunks
    all_chunks = get_all_chunks(kb_name)
    build_index(all_chunks, kb_name)

    add_doc(kb_name, display_name, len(pages), len(chunks), doc_type)

    # 审计日志
    event_type = "doc.url" if source_category == "url" else "doc.upload"
    log_event(event_type, kb_name=kb_name, details={
        "doc_name": display_name,
        "pages": len(pages),
        "chunks": len(chunks),
        "doc_type": SUPPORTED_TYPES.get(doc_type, doc_type),
    })

    return DocInfo(
        name=display_name,
        pages=len(pages),
        chunks=len(chunks),
        type=SUPPORTED_TYPES.get(doc_type, doc_type),
    )


# ═══════════════════════════════════════════════════════════════
# Health
# ═══════════════════════════════════════════════════════════════

@app.get("/health", tags=["System"])
def health_check():
    """系统健康检查 — 返回已安装的依赖列表（脱敏）。"""
    return {"status": "ok", "version": app.version}


# ═══════════════════════════════════════════════════════════════
# KB CRUD
# ═══════════════════════════════════════════════════════════════

@app.get("/api/kb", tags=["Knowledge Base"])
def api_list_kbs():
    """列出所有知识库及其文档详情。"""
    kbs: list[KBInfo] = []
    for name in list_kbs():
        try:
            docs = get_kb_docs(name)
        except ValueError:
            docs = []
        kbs.append(KBInfo(
            name=name,
            doc_count=len(docs),
            docs=[DocInfo(**d) for d in docs],
        ))
    return kbs


@app.post(
    "/api/kb", status_code=201, tags=["Knowledge Base"],
    responses={409: {"model": ErrorResponse}},
)
def api_create_kb(body: KBCreateRequest):
    """创建新知识库。名称只能包含中文、英文、数字、下划线。"""
    try:
        name = validate_kb_name(body.name)
        create_kb(name)
    except ValueError as e:
        raise HTTPException(400 if "不能为空" in str(e) else 409, str(e))
    return KBCreateResponse(name=name, message=f"知识库 '{name}' 已创建")


@app.delete(
    "/api/kb/{name}", tags=["Knowledge Base"],
    responses={404: {"model": ErrorResponse}},
)
def api_delete_kb(name: str):
    """删除知识库，同时清理其下的 ChromaDB 和 BM25 数据。"""
    try:
        delete_kb(name)
    except ValueError:
        raise HTTPException(404, f"知识库 '{name}' 不存在")
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# Docs
# ═══════════════════════════════════════════════════════════════

@app.get("/api/kb/{name}/docs", tags=["Documents"])
def api_list_docs(name: str):
    """列出某知识库的所有已入库文档。"""
    try:
        docs = get_kb_docs(name)
    except ValueError:
        raise HTTPException(404, f"知识库 '{name}' 不存在")
    return [DocInfo(**d) for d in docs]


@app.post("/api/kb/{name}/docs/upload", status_code=201, tags=["Documents"])
def api_upload_file(name: str, file: UploadFile = File(...)):
    """上传文件（PDF/TXT/MD/DOCX）到知识库。"""
    if name not in list_kbs():
        raise HTTPException(404, f"知识库 '{name}' 不存在")

    try:
        validate_file_extension(file.filename or "")
    except ValueError as e:
        raise HTTPException(400, str(e))

    ext = os.path.splitext(file.filename or "")[1].lower()
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(file.file.read())
            tmp_path = tmp.name
        return _process_and_index(tmp_path, ext, file.filename or "unnamed", name)
    except Exception as e:
        raise HTTPException(500, f"处理失败: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.post("/api/kb/{name}/docs/url", status_code=201, tags=["Documents"])
def api_load_url(name: str, body: URLLoadRequest):
    """抓取网页内容并入库。"""
    import re
    if not re.match(r"^https?://", body.url):
        raise HTTPException(400, "请输入有效的 HTTP/HTTPS 链接")

    if name not in list_kbs():
        raise HTTPException(404, f"知识库 '{name}' 不存在")

    from urllib.parse import unquote
    display = unquote(body.url.rstrip("/").rsplit("/", 1)[-1])[:50] or "网页"

    try:
        return _process_and_index(body.url, "url", display, name, source_category="url")
    except Exception as e:
        raise HTTPException(500, f"抓取失败: {e}")


@app.delete(
    "/api/kb/{name}/docs/{doc_name}", tags=["Documents"],
    responses={404: {"model": ErrorResponse}},
)
def api_delete_doc(name: str, doc_name: str):
    """从知识库中移除文档，清理其向量 chunk 并重建 BM25 索引。"""
    if name not in list_kbs():
        raise HTTPException(404, f"知识库 '{name}' 不存在")

    delete_doc_chunks(name, doc_name)
    remove_doc(name, doc_name)

    log_event("doc.delete", kb_name=name, details={"doc_name": doc_name})

    try:
        from embedder import get_all_chunks
        remaining = get_all_chunks(name)
        if remaining:
            build_index(remaining, name)
        else:
            delete_index(name)
    except Exception:
        pass

    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# Batch Operations
# ═══════════════════════════════════════════════════════════════

@app.post(
    "/api/kb/{name}/docs/upload/batch", status_code=201, tags=["Documents"],
    responses={404: {"model": ErrorResponse}},
)
def api_upload_files_batch(name: str, files: list[UploadFile] = File(...)):
    """批量上传文件 — 一次上传多个文件，返回每个文件的处理结果。

    单个文件失败不影响其他文件继续处理，最后统一重建 BM25 索引。
    """
    if name not in list_kbs():
        raise HTTPException(404, f"知识库 '{name}' 不存在")

    results: list[BatchUploadItem] = []
    valid_files = []

    for file in files:
        try:
            validate_file_extension(file.filename or "")
        except ValueError as e:
            results.append(BatchUploadItem(
                name=file.filename or "unknown", status="error", error=str(e),
            ))
            continue
        valid_files.append(file)

    # 先处理所有文件，最后统一重建 BM25
    processed: list[dict] = []
    for file in valid_files:
        ext = os.path.splitext(file.filename or "")[1].lower()
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                tmp.write(file.file.read())
                tmp_path = tmp.name
            doc_info = _process_and_index(tmp_path, ext, file.filename or "unnamed", name)
            results.append(BatchUploadItem(
                name=file.filename or "unnamed", status="success",
                pages=doc_info.pages, chunks=doc_info.chunks, type=doc_info.type,
            ))
            processed.append({
                "name": file.filename or "unnamed",
                "pages": doc_info.pages,
                "chunks": doc_info.chunks,
                "type": doc_info.type,
            })
        except Exception as e:
            results.append(BatchUploadItem(
                name=file.filename or "unnamed", status="error", error=str(e),
            ))
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    # 统一重建 BM25
    if processed:
        try:
            from embedder import get_all_chunks
            all_chunks = get_all_chunks(name)
            if all_chunks:
                from bm25_index import build_index as build_bm25_index
                build_bm25_index(all_chunks, name)
        except Exception:
            pass

    success = sum(1 for r in results if r.status == "success")
    failed = len(results) - success

    log_event("doc.upload.batch", kb_name=name, details={
        "total": len(files), "success": success, "failed": failed,
    })

    return BatchUploadResponse(
        kb_name=name, results=results, total=len(files), success=success, failed=failed,
    )


@app.post(
    "/api/kb/{name}/docs/delete/batch", tags=["Documents"],
    responses={404: {"model": ErrorResponse}},
)
def api_delete_docs_batch(name: str, body: BatchDeleteRequest):
    """批量删除文档 — 一次删除多个文档，最后统一重建 BM25 索引。"""
    if name not in list_kbs():
        raise HTTPException(404, f"知识库 '{name}' 不存在")

    results: list[BatchDeleteItem] = []
    for doc_name in body.doc_names:
        try:
            delete_doc_chunks(name, doc_name)
            remove_doc(name, doc_name)
            log_event("doc.delete", kb_name=name, details={"doc_name": doc_name})
            results.append(BatchDeleteItem(name=doc_name, status="success"))
        except Exception as e:
            results.append(BatchDeleteItem(name=doc_name, status="error", error=str(e)))

    # 统一重建 BM25
    try:
        from embedder import get_all_chunks
        remaining = get_all_chunks(name)
        if remaining:
            from bm25_index import build_index as build_bm25_index
            build_bm25_index(remaining, name)
        else:
            from bm25_index import delete_index as delete_bm25_index
            delete_bm25_index(name)
    except Exception:
        pass

    deleted = sum(1 for r in results if r.status == "success")

    log_event("doc.delete.batch", kb_name=name, details={
        "total": len(body.doc_names), "deleted": deleted,
    })

    return BatchDeleteResponse(
        kb_name=name, results=results, total=len(body.doc_names), deleted=deleted,
    )


@app.post(
    "/api/kb/{name}/docs/url/batch", status_code=201, tags=["Documents"],
    responses={404: {"model": ErrorResponse}},
)
def api_load_urls_batch(name: str, body: BatchURLRequest):
    """批量导入网页 — 一次导入多个 URL，返回每个 URL 的处理结果。"""
    import re as _url_re

    if name not in list_kbs():
        raise HTTPException(404, f"知识库 '{name}' 不存在")

    results: list[BatchURLItem] = []
    for url in body.urls:
        url = url.strip()
        if not url or not _url_re.match(r"^https?://", url):
            results.append(BatchURLItem(url=url or "(空)", status="error", error="无效的 URL"))
            continue

        from urllib.parse import unquote
        display = unquote(url.rstrip("/").rsplit("/", 1)[-1])[:50] or "网页"

        try:
            doc_info = _process_and_index(url, "url", display, name, source_category="url")
            results.append(BatchURLItem(
                url=url, status="success", name=display,
                pages=doc_info.pages, chunks=doc_info.chunks,
            ))
        except Exception as e:
            results.append(BatchURLItem(url=url, status="error", error=str(e)))

    success = sum(1 for r in results if r.status == "success")
    failed = len(body.urls) - success

    log_event("doc.url.batch", kb_name=name, details={
        "total": len(body.urls), "success": success, "failed": failed,
    })

    return BatchURLResponse(
        kb_name=name, results=results, total=len(body.urls), success=success, failed=failed,
    )


# ═══════════════════════════════════════════════════════════════
# Query
# ═══════════════════════════════════════════════════════════════

@app.post("/api/kb/{name}/query", tags=["Query"])
def api_query(name: str, body: QueryRequest):
    """同步问答 — 返回完整回答。

    原理：用户问题 → 向量嵌入 → ChromaDB 检索 + BM25 关键词检索 → RRF 融合
    → 拼接 prompt（含检索结果 + 对话历史）→ LLM 生成 → 返回

    请求体可配置 top_k、rrf_k 和检索权重，每次问答即时生效。
    """
    if name not in list_kbs():
        raise HTTPException(404, f"知识库 '{name}' 不存在")

    try:
        safe_query = validate_question(body.query)
    except ValueError as e:
        raise HTTPException(400, str(e))

    results = retrieve(
        safe_query, name,
        top_k=body.top_k,
        rrf_k=body.rrf_k,
        vector_weight=body.vector_weight,
        bm25_weight=body.bm25_weight,
        rewrite=body.rewrite,
        history_text=body.history,
        rerank=body.rerank,
    )

    if not results:
        return QueryResponse(
            answer="未找到相关内容，请尝试换个问法。",
            references=[],
            query=safe_query,
        )

    # 非流式生成
    from generator import generate
    gen_result = generate(safe_query, results)

    log_event("query", kb_name=name, details={
        "query": safe_query[:200],
        "result_count": len(results),
        "top_k": body.top_k,
        "rrf_k": body.rrf_k,
        "vector_weight": body.vector_weight,
        "bm25_weight": body.bm25_weight,
        "rewrite": body.rewrite,
        "rerank": body.rerank,
    })

    refs = [
        Reference(
            source=r.get("source", ""),
            page=r.get("page", 0),
            text=r.get("text", ""),
            score=round(r.get("score", 0.0), 4),
            has_table=r.get("has_table", False),
            is_scanned=r.get("is_scanned", False),
        )
        for r in results
    ]

    return QueryResponse(answer=gen_result.text, references=refs, query=safe_query)


@app.get("/api/kb/{name}/query/stream", tags=["Query"])
def api_query_stream(
    name: str,
    query: str = Query(..., min_length=1, max_length=2000, description="用户问题"),
    top_k: int = Query(default=5, ge=1, le=20),
    rrf_k: int = Query(default=60, ge=0, le=120),
    vector_weight: float = Query(default=1.0, ge=0.0, le=2.0),
    bm25_weight: float = Query(default=1.0, ge=0.0, le=2.0),
    rewrite: bool = Query(default=False, description="是否启用查询改写"),
    history: str = Query(default="", description="对话历史文本"),
    rerank: bool = Query(default=False, description="是否启用 LLM 精排"),
):
    """流式问答 — SSE 协议逐 token 返回回答。

    原理：与同步问答相同，但使用 GET 方法（便于浏览器 EventSource 消费），
    响应 Content-Type 为 text/event-stream，每生成一个 token 就推送给客户端。

    客户端示例 (JavaScript):
        const es = new EventSource('/api/kb/mykb/query/stream?query=什么是RAG');
        es.onmessage = (e) => { if (e.data === '[DONE]') es.close(); else text += e.data; };
    """
    if name not in list_kbs():
        raise HTTPException(404, f"知识库 '{name}' 不存在")

    try:
        safe_query = validate_question(query)
    except ValueError as e:
        raise HTTPException(400, str(e))

    results = retrieve(
        safe_query, name,
        top_k=top_k, rrf_k=rrf_k,
        vector_weight=vector_weight, bm25_weight=bm25_weight,
        rewrite=rewrite, history_text=history, rerank=rerank,
    )

    log_event("query.stream", kb_name=name, details={
        "query": safe_query[:200],
        "result_count": len(results),
        "top_k": top_k,
        "rrf_k": rrf_k,
        "vector_weight": vector_weight,
        "bm25_weight": bm25_weight,
    })

    if not results:
        def _empty():
            yield "data: 未找到相关内容。\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(_empty(), media_type="text/event-stream")

    def _stream():
        for chunk in generate_stream(safe_query, results):
            # SSE 格式: "data: <payload>\n\n"
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ═══════════════════════════════════════════════════════════════
# Audit
# ═══════════════════════════════════════════════════════════════

@app.get("/api/audit", tags=["System"])
def api_audit_log(
    kb_name: str = Query(default="", description="按知识库筛选"),
    event_type: str = Query(default="", description="按事件类型筛选"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """审计日志查询 — 分页返回操作记录，支持按知识库和事件类型筛选。

    事件类型: kb.create / kb.delete / doc.upload / doc.url / doc.delete / query / query.stream
    """
    events = get_events(kb_name=kb_name, event_type=event_type, limit=limit, offset=offset)
    total = count_events(kb_name=kb_name, event_type=event_type)
    return {
        "events": events,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ═══════════════════════════════════════════════════════════════
# Stats (简化版 — 不跟踪持久化用量，仅返回配置信息)
# ═══════════════════════════════════════════════════════════════

@app.get("/api/stats", tags=["System"])
def api_stats():
    """系统概况：知识库数量、文档总数、模型配置。"""
    kb_count = len(list_kbs())
    total_docs = 0
    for name in list_kbs():
        try:
            total_docs += len(get_kb_docs(name))
        except ValueError:
            pass

    from config import LLM_MODEL, EMBEDDING_MODEL, TOP_K, RRF_K, BATCH_SIZE
    from embedding_cache import get_stats as get_cache_stats, cache_hit_rate
    cs = get_cache_stats()
    return {
        "kb_count": kb_count,
        "total_docs": total_docs,
        "config": {
            "llm_model": LLM_MODEL,
            "embedding_model": EMBEDDING_MODEL,
            "default_top_k": TOP_K,
            "default_rrf_k": RRF_K,
            "batch_size": BATCH_SIZE,
        },
        "embedding_cache": {
            "hits": cs["hits"],
            "misses": cs["misses"],
            "total_cached": cs["total_cached"],
            "hit_rate": round(cache_hit_rate(), 4),
        },
    }
