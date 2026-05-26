"""重排序模块测试。"""

from __future__ import annotations
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from reranker import rerank, _parse_scores


class FakeGenResult:
    def __init__(self, text: str):
        self.text = text


def make_docs(n: int) -> list[dict]:
    return [
        {"text": f"Document {i} content about AI and RAG technology.", "source": f"doc{i}.pdf", "page": i, "score": 1.0 / (i + 1)}
        for i in range(n)
    ]


# ── _parse_scores 测试 ──────────────────────────────────────────

def test_parse_scores_bracket_format():
    raw = "[1] 8\n[2] 5\n[3] 9\n[4] 3\n[5] 7"
    scores = _parse_scores(raw, 5)
    assert len(scores) == 5
    assert scores[0] == 8.0
    assert scores[2] == 9.0


def test_parse_scores_bracket_colon():
    raw = "[1]: 8\n[2]: 5\n[3]: 9"
    scores = _parse_scores(raw, 3)
    assert scores[0] == 8.0
    assert scores[2] == 9.0


def test_parse_scores_numbered():
    raw = "1. 8\n2. 5\n3. 9"
    scores = _parse_scores(raw, 3)
    assert scores[0] == 8.0


def test_parse_scores_chinese_punctuation():
    raw = "1、8\n2、5\n3、9"
    scores = _parse_scores(raw, 3)
    assert scores[0] == 8.0


def test_parse_scores_with_suffix():
    raw = "[1] 8分\n[2] 5分\n[3] 9分"
    scores = _parse_scores(raw, 3)
    assert scores[0] == 8.0


def test_parse_scores_mixed_format():
    raw = "[1] 8 [2]: 5.5 3. 9.0"
    scores = _parse_scores(raw, 3)
    assert scores[0] == 8.0
    assert scores[1] == 5.5
    assert scores[2] == 9.0


def test_parse_scores_clamp():
    raw = "[1] 15\n[2] -3\n[3] 5"
    scores = _parse_scores(raw, 3)
    assert scores[0] == 10.0  # clamped to max
    assert scores[1] == 0.0   # clamped to min
    assert scores[2] == 5.0


def test_parse_scores_partial():
    """部分文档未能解析到分数时应给 0。"""
    raw = "[1] 8\n[3] 6"
    scores = _parse_scores(raw, 5)
    assert scores[0] == 8.0
    assert scores[1] == 0.0
    assert scores[2] == 6.0
    assert scores[3] == 0.0
    assert scores[4] == 0.0


def test_parse_scores_empty():
    assert _parse_scores("", 3) == [0.0, 0.0, 0.0]
    assert _parse_scores("garbage text", 2) == [0.0, 0.0]


# ── rerank 测试 ────────────────────────────────────────────────

def test_rerank_fewer_than_top_k():
    """候选数 <= top_k 时直接返回，不调 LLM。"""
    docs = make_docs(3)
    result = rerank("test query", docs, top_k=5)
    assert len(result) == 3
    assert result == docs


def test_rerank_equal_to_top_k():
    docs = make_docs(5)
    result = rerank("test query", docs, top_k=5)
    assert len(result) == 5
    assert result == docs


def test_rerank_reorders():
    """正常 rerank 流程：LLM 返回分数后重排。"""
    docs = make_docs(6)  # 6 candidates, top_k=3

    with patch("reranker.get_provider") as mock_get:
        mock_provider = MagicMock()
        # doc[1] gets highest score, doc[3] second, etc.
        mock_provider.generate.return_value = FakeGenResult(
            "[1] 3\n[2] 9\n[3] 5\n[4] 8\n[5] 2\n[6] 6"
        )
        mock_get.return_value = mock_provider

        result = rerank("AI query", docs, top_k=3)
        assert len(result) == 3
        # Highest scores: [2] 9, [4] 8, [6] 6 → docs indices 1, 3, 5
        assert result[0]["source"] == "doc1.pdf"  # index 1 (9 pts)
        assert result[1]["source"] == "doc3.pdf"  # index 3 (8 pts)
        assert result[2]["source"] == "doc5.pdf"  # index 5 (6 pts)


def test_rerank_preserves_metadata():
    docs = make_docs(6)
    # Add extra metadata
    for d in docs:
        d["has_table"] = False
        d["is_scanned"] = False

    with patch("reranker.get_provider") as mock_get:
        mock_provider = MagicMock()
        mock_provider.generate.return_value = FakeGenResult(
            "[1] 5\n[2] 5\n[3] 5\n[4] 5\n[5] 5\n[6] 5"
        )
        mock_get.return_value = mock_provider

        result = rerank("query", docs, top_k=3)
        assert len(result) == 3
        for d in result:
            assert "rerank_score" in d
            assert "has_table" in d
            assert "is_scanned" in d


def test_rerank_llm_error_fallback():
    """LLM 调用失败时按原始 score 排序返回 top-k。"""
    docs = []
    for i in range(6):
        docs.append({
            "text": f"Doc {i}",
            "source": f"doc{i}.pdf",
            "page": i,
            "score": 1.0 - i * 0.1,  # descending scores
        })

    with patch("reranker.get_provider") as mock_get:
        mock_get.side_effect = RuntimeError("API 错误")

        result = rerank("query", docs, top_k=3)
        assert len(result) == 3
        # Should return top 3 by original score: doc0, doc1, doc2
        assert result[0]["source"] == "doc0.pdf"
        assert result[1]["source"] == "doc1.pdf"
        assert result[2]["source"] == "doc2.pdf"


def test_rerank_with_custom_model():
    docs = make_docs(6)
    with patch("reranker.get_provider") as mock_get:
        mock_provider = MagicMock()
        mock_provider.generate.return_value = FakeGenResult("[1] 1\n[2] 1\n[3] 1\n[4] 1\n[5] 1\n[6] 1")
        mock_get.return_value = mock_provider

        rerank("query", docs, top_k=3, model="qwen-turbo")
        mock_get.assert_called_once_with("qwen-turbo")
