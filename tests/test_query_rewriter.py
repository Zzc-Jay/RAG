"""查询改写模块测试。"""

from __future__ import annotations
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from query_rewriter import rewrite_query


class FakeGenResult:
    def __init__(self, text: str):
        self.text = text


def test_rewrite_empty_history():
    """空历史时直接返回原问题，不调用 LLM。"""
    result = rewrite_query("什么是RAG？", history_text="")
    assert result == "什么是RAG？"


def test_rewrite_whitespace_history():
    """只有空白的 history 也直接返回。"""
    result = rewrite_query("test", history_text="   ")
    assert result == "test"


def test_rewrite_with_history():
    """正常改写：指代消解。"""
    with patch("query_rewriter.get_provider") as mock_get:
        mock_provider = MagicMock()
        mock_provider.generate.return_value = FakeGenResult("RAG有什么优点？")
        mock_get.return_value = mock_provider

        result = rewrite_query(
            "它有什么优点？",
            history_text="用户: 什么是RAG？\n助手: RAG是检索增强生成技术。",
        )
        assert "优点" in result
        assert mock_provider.generate.called


def test_rewrite_strips_prefix():
    """LLM 返回带前缀的文本时，应自动去除。"""
    with patch("query_rewriter.get_provider") as mock_get:
        mock_provider = MagicMock()
        mock_provider.generate.return_value = FakeGenResult("改写后的问题：RAG优缺点")
        mock_get.return_value = mock_provider

        result = rewrite_query("它有什么优缺点？", history_text="用户: 什么是RAG？")
        assert result == "RAG优缺点"


def test_rewrite_strips_prefix_colon():
    """冒号变体。"""
    with patch("query_rewriter.get_provider") as mock_get:
        mock_provider = MagicMock()
        mock_provider.generate.return_value = FakeGenResult("改写后的问题: RAG是什么")
        mock_get.return_value = mock_provider

        result = rewrite_query("什么是它？", history_text="用户: 解释RAG。")
        assert "改写后的问题" not in result


def test_rewrite_empty_result_fallback():
    """LLM 返回空文本时，fallback 到原 query。"""
    with patch("query_rewriter.get_provider") as mock_get:
        mock_provider = MagicMock()
        mock_provider.generate.return_value = FakeGenResult("")
        mock_get.return_value = mock_provider

        result = rewrite_query("短问题", history_text="用户: 你好\n助手: 你好")
        assert result == "短问题"


def test_rewrite_too_long_fallback():
    """改写结果过长时（异常），fallback 到原 query。"""
    with patch("query_rewriter.get_provider") as mock_get:
        mock_provider = MagicMock()
        mock_provider.generate.return_value = FakeGenResult("非常长的回答" * 200)
        mock_get.return_value = mock_provider

        result = rewrite_query("短", history_text="用户: 你好\n助手: 你好")
        assert result == "短"


def test_rewrite_llm_error_fallback():
    """LLM 调用失败时 fallback 到原 query。"""
    with patch("query_rewriter.get_provider") as mock_get:
        mock_get.side_effect = RuntimeError("API 错误")

        result = rewrite_query("测试问题", history_text="用户: 你好\n助手: 你好")
        assert result == "测试问题"


def test_rewrite_with_custom_model():
    """指定自定义 model 时正确传递给 provider。"""
    with patch("query_rewriter.get_provider") as mock_get:
        mock_provider = MagicMock()
        mock_provider.generate.return_value = FakeGenResult("rewritten")
        mock_get.return_value = mock_provider

        result = rewrite_query(
            "它是什么？", history_text="用户: RAG",
            model="deepseek-chat",
        )
        mock_get.assert_called_with("deepseek-chat")
