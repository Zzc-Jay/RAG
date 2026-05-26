"""REST API 端点测试 — 使用 FastAPI TestClient，无需启动真实服务器。

教学点：
1. TestClient 模拟 HTTP 请求/响应，不占用真实端口
2. 每个测试在独立的临时 KB 中操作，测试完自动清理
3. 覆盖 CRUD + 文件上传 + URL 导入 + 流式问答 + 错误场景
"""

from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient
from api import app

client = TestClient(app)

TEST_KB = "_pytest_api_test"


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _cleanup_kb():
    """每个测试前后清理临时 KB（忽略 Windows 文件锁定错误）。"""
    import shutil
    from kb_manager import delete_kb, list_kbs
    # Pre-cleanup: 从注册表移除
    try:
        delete_kb(TEST_KB)
    except (ValueError, PermissionError):
        pass
    # 强制清理残留目录（ChromaDB 在 Windows 上可能锁文件）
    from config import CHROMA_DIR, BM25_DIR
    for base in (CHROMA_DIR, BM25_DIR):
        target = os.path.join(base, TEST_KB)
        if os.path.exists(target):
            try:
                shutil.rmtree(target)
            except (PermissionError, OSError):
                pass
    yield
    try:
        delete_kb(TEST_KB)
    except (ValueError, PermissionError):
        pass
    for base in (CHROMA_DIR, BM25_DIR):
        target = os.path.join(base, TEST_KB)
        if os.path.exists(target):
            try:
                shutil.rmtree(target)
            except (PermissionError, OSError):
                pass


def _create_test_txt(content: str, filename: str = "test.txt") -> str:
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path, filename


# ═══════════════════════════════════════════════════════════════
# Health
# ═══════════════════════════════════════════════════════════════

def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert "version" in resp.json()


# ═══════════════════════════════════════════════════════════════
# KB CRUD
# ═══════════════════════════════════════════════════════════════

def test_create_kb():
    resp = client.post("/api/kb", json={"name": TEST_KB})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == TEST_KB
    assert "已创建" in data["message"]


def test_create_duplicate_kb():
    client.post("/api/kb", json={"name": TEST_KB})
    resp = client.post("/api/kb", json={"name": TEST_KB})
    assert resp.status_code == 409


def test_create_kb_invalid_name():
    resp = client.post("/api/kb", json={"name": "a" * 100})
    assert resp.status_code == 422  # Pydantic validation


def test_list_kbs():
    client.post("/api/kb", json={"name": TEST_KB})
    resp = client.get("/api/kb")
    assert resp.status_code == 200
    kbs = resp.json()
    names = [k["name"] for k in kbs]
    assert TEST_KB in names


def test_delete_kb():
    client.post("/api/kb", json={"name": TEST_KB})
    resp = client.delete(f"/api/kb/{TEST_KB}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_delete_nonexistent_kb():
    resp = client.delete("/api/kb/nonexistent_xyz_123")
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════
# Docs — Upload
# ═══════════════════════════════════════════════════════════════

def test_upload_file():
    client.post("/api/kb", json={"name": TEST_KB})

    path, fname = _create_test_txt("RAG is a technique for retrieval augmented generation. " * 20)
    try:
        with open(path, "rb") as f:
            resp = client.post(
                f"/api/kb/{TEST_KB}/docs/upload",
                files={"file": (fname, f, "text/plain")},
            )
        assert resp.status_code == 201
        info = resp.json()
        assert info["name"] == fname
        assert info["pages"] == 1
        assert info["chunks"] >= 1
    finally:
        os.unlink(path)


def test_upload_file_invalid_extension():
    client.post("/api/kb", json={"name": TEST_KB})

    path, fname = _create_test_txt("test")
    os.rename(path, path.replace(".txt", ".exe"))
    exe_path = path.replace(".txt", ".exe")
    fname = fname.replace(".txt", ".exe")
    try:
        with open(exe_path, "rb") as f:
            resp = client.post(
                f"/api/kb/{TEST_KB}/docs/upload",
                files={"file": (fname, f, "application/octet-stream")},
            )
        assert resp.status_code == 400
    finally:
        if os.path.exists(exe_path):
            os.unlink(exe_path)


def test_upload_to_nonexistent_kb():
    path, fname = _create_test_txt("test content")
    try:
        with open(path, "rb") as f:
            resp = client.post(
                "/api/kb/nonexistent_xyz/docs/upload",
                files={"file": (fname, f, "text/plain")},
            )
        assert resp.status_code == 404
    finally:
        os.unlink(path)


# ═══════════════════════════════════════════════════════════════
# Docs — URL
# ═══════════════════════════════════════════════════════════════

def test_load_url_invalid():
    client.post("/api/kb", json={"name": TEST_KB})
    resp = client.post(f"/api/kb/{TEST_KB}/docs/url", json={"url": "not-a-url"})
    assert resp.status_code == 400


def test_load_url_nonexistent_kb():
    resp = client.post(
        "/api/kb/nonexistent_xyz/docs/url",
        json={"url": "https://example.com"},
    )
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════
# Docs — List & Delete
# ═══════════════════════════════════════════════════════════════

def test_list_docs_empty():
    client.post("/api/kb", json={"name": TEST_KB})
    resp = client.get(f"/api/kb/{TEST_KB}/docs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_docs_nonexistent_kb():
    resp = client.get("/api/kb/nonexistent_xyz/docs")
    assert resp.status_code == 404


def test_delete_doc():
    client.post("/api/kb", json={"name": TEST_KB})

    path, fname = _create_test_txt("test content for deletion test. " * 15)
    try:
        with open(path, "rb") as f:
            client.post(
                f"/api/kb/{TEST_KB}/docs/upload",
                files={"file": (fname, f, "text/plain")},
            )
        resp = client.delete(f"/api/kb/{TEST_KB}/docs/{fname}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify doc is gone
        docs = client.get(f"/api/kb/{TEST_KB}/docs").json()
        assert len(docs) == 0
    finally:
        os.unlink(path)


# ═══════════════════════════════════════════════════════════════
# Batch Operations
# ═══════════════════════════════════════════════════════════════

def test_batch_upload_files():
    client.post("/api/kb", json={"name": TEST_KB})

    p1, n1 = _create_test_txt("Python is a programming language. " * 20, "file1.txt")
    p2, n2 = _create_test_txt("RAG stands for retrieval augmented generation. " * 20, "file2.txt")
    try:
        with open(p1, "rb") as f1, open(p2, "rb") as f2:
            resp = client.post(
                f"/api/kb/{TEST_KB}/docs/upload/batch",
                files=[
                    ("files", (n1, f1, "text/plain")),
                    ("files", (n2, f2, "text/plain")),
                ],
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["total"] == 2
        assert data["success"] == 2
        assert data["failed"] == 0
        assert len(data["results"]) == 2

        # Verify docs are registered
        docs = client.get(f"/api/kb/{TEST_KB}/docs").json()
        assert len(docs) == 2
    finally:
        os.unlink(p1)
        os.unlink(p2)


def test_batch_upload_partial_invalid():
    """部分文件类型不合法时，合法文件不受影响。"""
    client.post("/api/kb", json={"name": TEST_KB})

    p1, n1 = _create_test_txt("Valid content. " * 20, "valid.txt")
    try:
        with open(p1, "rb") as f1:
            resp = client.post(
                f"/api/kb/{TEST_KB}/docs/upload/batch",
                files=[
                    ("files", ("bad.exe", b"malware", "application/octet-stream")),
                    ("files", (n1, f1, "text/plain")),
                ],
            )
        data = resp.json()
        assert data["total"] == 2
        assert data["success"] == 1
        assert data["failed"] == 1

        err_items = [r for r in data["results"] if r["status"] == "error"]
        assert len(err_items) == 1
        assert "exe" in err_items[0]["error"] or "不支持" in err_items[0]["error"]
    finally:
        os.unlink(p1)


def test_batch_upload_nonexistent_kb():
    p1, n1 = _create_test_txt("test", "test.txt")
    try:
        with open(p1, "rb") as f1:
            resp = client.post(
                "/api/kb/nonexistent_xyz/docs/upload/batch",
                files=[("files", (n1, f1, "text/plain"))],
            )
        assert resp.status_code == 404
    finally:
        os.unlink(p1)


def test_batch_delete_docs():
    client.post("/api/kb", json={"name": TEST_KB})

    p1, n1 = _create_test_txt("Doc 1 content. " * 20, "doc1.txt")
    p2, n2 = _create_test_txt("Doc 2 content. " * 20, "doc2.txt")
    p3, n3 = _create_test_txt("Doc 3 content. " * 20, "doc3.txt")
    try:
        for p, n in [(p1, n1), (p2, n2), (p3, n3)]:
            with open(p, "rb") as f:
                client.post(
                    f"/api/kb/{TEST_KB}/docs/upload",
                    files={"file": (n, f, "text/plain")},
                )

        # Batch delete 2 of 3
        resp = client.post(
            f"/api/kb/{TEST_KB}/docs/delete/batch",
            json={"doc_names": [n1, n2]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["deleted"] == 2

        # Verify only 1 remains
        docs = client.get(f"/api/kb/{TEST_KB}/docs").json()
        assert len(docs) == 1
        assert docs[0]["name"] == n3
    finally:
        for p in [p1, p2, p3]:
            os.unlink(p)


def test_batch_delete_empty_list():
    """空列表应该被 Pydantic 校验拦截。"""
    client.post("/api/kb", json={"name": TEST_KB})
    resp = client.post(
        f"/api/kb/{TEST_KB}/docs/delete/batch",
        json={"doc_names": []},
    )
    assert resp.status_code == 422


def test_batch_delete_nonexistent_kb():
    resp = client.post(
        "/api/kb/nonexistent_xyz/docs/delete/batch",
        json={"doc_names": ["test.pdf"]},
    )
    assert resp.status_code == 404


def test_batch_load_urls_invalid():
    client.post("/api/kb", json={"name": TEST_KB})
    resp = client.post(
        f"/api/kb/{TEST_KB}/docs/url/batch",
        json={"urls": ["not-a-url", ""]},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["success"] == 0
    assert data["failed"] == 2


def test_batch_load_urls_nonexistent_kb():
    resp = client.post(
        "/api/kb/nonexistent_xyz/docs/url/batch",
        json={"urls": ["https://example.com"]},
    )
    assert resp.status_code == 404


def test_batch_load_urls_empty_list():
    client.post("/api/kb", json={"name": TEST_KB})
    resp = client.post(
        f"/api/kb/{TEST_KB}/docs/url/batch",
        json={"urls": []},
    )
    assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════
# Query — Sync
# ═══════════════════════════════════════════════════════════════

def test_query():
    client.post("/api/kb", json={"name": TEST_KB})

    path, fname = _create_test_txt(
        "Python is a high-level programming language. "
        "It was created by Guido van Rossum and first released in 1991. "
        "Python emphasizes code readability with its notable use of significant indentation. " * 5
    )
    try:
        with open(path, "rb") as f:
            client.post(
                f"/api/kb/{TEST_KB}/docs/upload",
                files={"file": (fname, f, "text/plain")},
            )
    finally:
        os.unlink(path)

    resp = client.post(
        f"/api/kb/{TEST_KB}/query",
        json={"query": "Who created Python?"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert len(data["answer"]) > 0
    assert "references" in data
    assert len(data["references"]) >= 1
    assert "query" in data


def test_query_empty_kb():
    # 使用独立 KB 名，避免与其他测试共享 KB 残留数据
    empty_kb = TEST_KB + "_empty"
    from kb_manager import delete_kb, create_kb
    try:
        delete_kb(empty_kb)
    except ValueError:
        pass
    client.post("/api/kb", json={"name": empty_kb})
    resp = client.post(
        f"/api/kb/{empty_kb}/query",
        json={"query": "test question?"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["references"]) == 0

    # 清理
    try:
        delete_kb(empty_kb)
    except (ValueError, PermissionError):
        pass


def test_query_with_custom_params():
    client.post("/api/kb", json={"name": TEST_KB})

    path, fname = _create_test_txt("RAG retrieval augmented generation. " * 20)
    try:
        with open(path, "rb") as f:
            client.post(
                f"/api/kb/{TEST_KB}/docs/upload",
                files={"file": (fname, f, "text/plain")},
            )
    finally:
        os.unlink(path)

    # Custom retrieval params
    resp = client.post(
        f"/api/kb/{TEST_KB}/query",
        json={
            "query": "what is RAG",
            "top_k": 3,
            "rrf_k": 0,
            "vector_weight": 0.0,
            "bm25_weight": 1.0,  # pure keyword mode
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["references"]) <= 3


def test_query_invalid():
    client.post("/api/kb", json={"name": TEST_KB})
    # Empty query should fail validation
    resp = client.post(
        f"/api/kb/{TEST_KB}/query",
        json={"query": ""},
    )
    assert resp.status_code == 422


def test_query_nonexistent_kb():
    resp = client.post(
        "/api/kb/nonexistent_xyz/query",
        json={"query": "test"},
    )
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════
# Query — Stream (SSE)
# ═══════════════════════════════════════════════════════════════

def test_query_stream():
    client.post("/api/kb", json={"name": TEST_KB})

    path, fname = _create_test_txt("Python programming language for beginners. " * 20)
    try:
        with open(path, "rb") as f:
            client.post(
                f"/api/kb/{TEST_KB}/docs/upload",
                files={"file": (fname, f, "text/plain")},
            )
    finally:
        os.unlink(path)

    with client.stream(
        "GET",
        f"/api/kb/{TEST_KB}/query/stream",
        params={"query": "What is Python?"},
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        # Stream the content — at least [DONE] should be present
        content = resp.iter_text()
        full = "".join(list(content))
        assert "[DONE]" in full


def test_query_stream_empty_kb():
    client.post("/api/kb", json={"name": TEST_KB})
    with client.stream(
        "GET",
        f"/api/kb/{TEST_KB}/query/stream",
        params={"query": "anything"},
    ) as resp:
        assert resp.status_code == 200
        full = "".join(list(resp.iter_text()))
        assert len(full) > 0


# ═══════════════════════════════════════════════════════════════
# Stats
# ═══════════════════════════════════════════════════════════════

def test_stats():
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "kb_count" in data
    assert "total_docs" in data
    assert "config" in data
    assert "llm_model" in data["config"]
    assert "embedding_model" in data["config"]
