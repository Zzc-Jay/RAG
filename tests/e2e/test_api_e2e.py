"""API 端到端测试 — 通过 FastAPI TestClient 测试完整管线。

每条测试走完整链路：请求 → 业务逻辑 → 真实存储（ChromaDB/BM25/SQLite）→ 响应。
仅 mock 外部 API 边界（DashScope Embedding + LLM），内部组件全部真实运行。
"""

from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient

from api import app

client = TestClient(app)


# ═══════════════════════════════════════════════════════════════
# 全链路黄金路径
# ═══════════════════════════════════════════════════════════════

def test_e2e_full_workflow(kb_lifecycle, test_txt_file):
    """完整 API 管线：创建 KB → 上传文档 → 查询 → 验证回答和引用。"""
    kb = kb_lifecycle

    # 上传文档
    fname = os.path.basename(test_txt_file)
    with open(test_txt_file, "rb") as f:
        resp = client.post(
            f"/api/kb/{kb}/docs/upload",
            files={"file": (fname, f, "text/plain")},
        )
    assert resp.status_code == 201
    assert resp.json()["name"] == fname
    assert resp.json()["chunks"] > 0

    # 查询
    resp = client.post(
        f"/api/kb/{kb}/query",
        json={"query": "Who created Python?", "top_k": 3},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["answer"]) > 0
    assert len(data["references"]) >= 1
    assert data["query"] == "Who created Python?"


def test_e2e_streaming_workflow(kb_lifecycle, test_txt_file):
    """SSE 流式管线：上传文档后通过流式端点查询。"""
    kb = kb_lifecycle

    fname = os.path.basename(test_txt_file)
    with open(test_txt_file, "rb") as f:
        client.post(
            f"/api/kb/{kb}/docs/upload",
            files={"file": (fname, f, "text/plain")},
        )

    with client.stream(
        "GET",
        f"/api/kb/{kb}/query/stream",
        params={"query": "What is RAG?", "top_k": 3},
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        full = "".join(list(resp.iter_text()))
        assert "[DONE]" in full
        # 应该有实际数据（不只是 [DONE]）
        assert len(full) > len("[DONE]\n\n")


# ═══════════════════════════════════════════════════════════════
# 查询改写
# ═══════════════════════════════════════════════════════════════

def test_e2e_query_rewrite(kb_lifecycle, test_txt_file):
    """查询改写：两轮对话中代指消解，rewrite=True 时正常工作。"""
    kb = kb_lifecycle

    fname = os.path.basename(test_txt_file)
    with open(test_txt_file, "rb") as f:
        client.post(
            f"/api/kb/{kb}/docs/upload",
            files={"file": (fname, f, "text/plain")},
        )

    # 第一轮：直接询问 RAG
    resp1 = client.post(
        f"/api/kb/{kb}/query",
        json={"query": "什么是RAG？", "top_k": 3},
    )
    assert resp1.status_code == 200
    answer1 = resp1.json()["answer"]
    assert len(answer1) > 0

    # 第二轮：使用代词「它」，启用改写
    history_text = f"用户：什么是RAG？\n助手：{answer1}\n"
    resp2 = client.post(
        f"/api/kb/{kb}/query",
        json={
            "query": "它有什么优点？",
            "top_k": 3,
            "rewrite": True,
            "history": history_text,
        },
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["answer"]) > 0
    # 改写后应能检索到文档（query 在 KB 内容中）
    assert len(data2["references"]) >= 1


# ═══════════════════════════════════════════════════════════════
# LLM 精排
# ═══════════════════════════════════════════════════════════════

def test_e2e_rerank(kb_lifecycle, test_txt_file, test_txt_file2):
    """LLM 精排：多文档查询 + rerank=True，验证结果数量正确。"""
    kb = kb_lifecycle

    # 上传两个不同文档
    for path in (test_txt_file, test_txt_file2):
        fname = os.path.basename(path)
        with open(path, "rb") as f:
            resp = client.post(
                f"/api/kb/{kb}/docs/upload",
                files={"file": (fname, f, "text/plain")},
            )
            assert resp.status_code == 201

    # 查询 + 精排
    resp = client.post(
        f"/api/kb/{kb}/query",
        json={"query": "AI technology", "top_k": 2, "rerank": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["answer"]) > 0
    # rerank 后结果数应 ≤ top_k
    assert len(data["references"]) <= 2
    for ref in data["references"]:
        assert "score" in ref


# ═══════════════════════════════════════════════════════════════
# 批量操作
# ═══════════════════════════════════════════════════════════════

def test_e2e_batch_operations(kb_lifecycle):
    """批量上传 → 批量删除 → 验证剩余文档。"""
    kb = kb_lifecycle

    # 创建 3 个临时 TXT
    files = []
    try:
        for i, label in enumerate(["alpha", "beta", "gamma"], 1):
            fd, path = tempfile.mkstemp(suffix=".txt")
            os.close(fd)
            content = f"Document {label} about artificial intelligence. " * 30
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            files.append((path, f"doc_{label}.txt"))

        # 批量上传
        upload_files = [
            ("files", (fname, open(path, "rb"), "text/plain"))
            for path, fname in files
        ]
        resp = client.post(f"/api/kb/{kb}/docs/upload/batch", files=upload_files)
        assert resp.status_code == 201
        data = resp.json()
        assert data["total"] == 3
        assert data["success"] == 3
        assert data["failed"] == 0
    finally:
        # 关闭文件句柄
        for path, _ in files:
            pass

    # 批量删除前 2 个
    resp = client.post(
        f"/api/kb/{kb}/docs/delete/batch",
        json={"doc_names": ["doc_alpha.txt", "doc_beta.txt"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["deleted"] == 2

    # 验证只剩 1 个
    resp = client.get(f"/api/kb/{kb}/docs")
    assert resp.status_code == 200
    docs = resp.json()
    assert len(docs) == 1
    assert docs[0]["name"] == "doc_gamma.txt"

    # 清理临时文件
    for path, _ in files:
        try:
            os.unlink(path)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════
# 错误场景 — 404
# ═══════════════════════════════════════════════════════════════

def test_e2e_kb_not_found():
    """不存在的 KB：查询、上传、删除均返回 404。"""
    ghost = "_e2e_ghost_nonexistent"

    resp = client.post(
        f"/api/kb/{ghost}/query",
        json={"query": "test"},
    )
    assert resp.status_code == 404

    with open(__file__, "rb") as f:
        resp = client.post(
            f"/api/kb/{ghost}/docs/upload",
            files={"file": ("test.txt", f, "text/plain")},
        )
    assert resp.status_code == 404

    resp = client.delete(f"/api/kb/{ghost}")
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════
# 错误场景 — 400 非法文件
# ═══════════════════════════════════════════════════════════════

def test_e2e_upload_invalid_file(kb_lifecycle):
    """上传不支持的文件类型应返回 400。"""
    kb = kb_lifecycle

    # 创建 .exe 临时文件
    fd, path = tempfile.mkstemp(suffix=".exe")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(b"MZ\x00\x00")  # DOS header
    try:
        with open(path, "rb") as f:
            resp = client.post(
                f"/api/kb/{kb}/docs/upload",
                files={"file": ("bad.exe", f, "application/octet-stream")},
            )
        assert resp.status_code == 400
    finally:
        os.unlink(path)


# ═══════════════════════════════════════════════════════════════
# 多轮对话
# ═══════════════════════════════════════════════════════════════

def test_e2e_multiturn_conversation(kb_lifecycle, test_txt_file):
    """三轮对话：Python → 谁创造的 → 什么时候发布，每次携带历史。"""
    kb = kb_lifecycle

    fname = os.path.basename(test_txt_file)
    with open(test_txt_file, "rb") as f:
        client.post(
            f"/api/kb/{kb}/docs/upload",
            files={"file": (fname, f, "text/plain")},
        )

    history_parts: list[str] = []

    # Turn 1: 直接问 Python
    resp = client.post(
        f"/api/kb/{kb}/query",
        json={"query": "什么是Python？", "top_k": 3},
    )
    assert resp.status_code == 200
    a1 = resp.json()["answer"]
    assert len(a1) > 0
    assert len(resp.json()["references"]) >= 1
    history_parts.append(f"用户：什么是Python？")
    history_parts.append(f"助手：{a1}")

    # Turn 2: 追问创造者（代词）
    resp = client.post(
        f"/api/kb/{kb}/query",
        json={
            "query": "谁创造的？",
            "top_k": 3,
            "rewrite": True,
            "history": "\n".join(history_parts),
        },
    )
    assert resp.status_code == 200
    a2 = resp.json()["answer"]
    assert len(a2) > 0
    history_parts.append("用户：谁创造的？")
    history_parts.append(f"助手：{a2}")

    # Turn 3: 追问时间
    resp = client.post(
        f"/api/kb/{kb}/query",
        json={
            "query": "什么时候发布的？",
            "top_k": 3,
            "rewrite": True,
            "history": "\n".join(history_parts),
        },
    )
    assert resp.status_code == 200
    a3 = resp.json()["answer"]
    assert len(a3) > 0
    assert len(resp.json()["references"]) >= 1
