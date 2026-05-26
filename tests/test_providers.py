"""Provider 层单元测试 — 每个 provider 独立测试，mock 所有外部 API 调用。"""

from __future__ import annotations

import sys
import os
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ═══════════════════════════════════════════════════════════════
# Factory: get_provider
# ═══════════════════════════════════════════════════════════════

def test_get_provider_dashscope():
    from providers import get_provider
    from providers.dashscope_provider import DashScopeProvider
    provider = get_provider("qwen-plus", {"DASHSCOPE_API_KEY": "test-key"})
    assert isinstance(provider, DashScopeProvider)
    assert provider.model == "qwen-plus"
    assert provider.api_key == "test-key"


def test_get_provider_deepseek_with_dedicated_key():
    from providers import get_provider
    from providers.compatible_provider import CompatibleProvider
    provider = get_provider("deepseek-v4-flash", {"DEEPSEEK_API_KEY": "sk-ds"})
    assert isinstance(provider, CompatibleProvider)
    assert provider.model == "deepseek-v4-flash"


def test_get_provider_doubao_with_dedicated_key():
    from providers import get_provider
    from providers.compatible_provider import CompatibleProvider
    provider = get_provider("doubao-seed-2.0-pro", {"DOUBAO_API_KEY": "sk-db"})
    assert isinstance(provider, CompatibleProvider)
    assert provider.model == "doubao-seed-2.0-pro"


def test_get_provider_deepseek_fallback():
    """只有 DASHSCOPE_API_KEY 时，DeepSeek 应报错（阿里 Key 不能用于 DeepSeek）。"""
    from providers import get_provider
    with pytest.raises(RuntimeError, match="缺少.*API Key"):
        # 过去错误地把 DASHSCOPE_API_KEY 当作通用 Key fallback
        # 现在 DeepSeek 必须配置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY
        get_provider("deepseek-v4-flash", {"DASHSCOPE_API_KEY": "k"})


def test_get_provider_unknown_model_raises():
    from providers import get_provider
    with pytest.raises(ValueError, match="未知模型"):
        get_provider("nonexistent-model", {"DASHSCOPE_API_KEY": "k"})


def test_get_provider_missing_key_raises():
    from providers import get_provider
    with pytest.raises(RuntimeError, match="缺少.*API Key"):
        get_provider("deepseek-v4-flash", {})


def test_get_provider_all_qwen_variants():
    from providers import get_provider
    from providers.dashscope_provider import DashScopeProvider
    for model in ["qwen-plus", "qwen3-max"]:
        provider = get_provider(model, {"DASHSCOPE_API_KEY": "k"})
        assert isinstance(provider, DashScopeProvider)
        assert provider.model == model


# ═══════════════════════════════════════════════════════════════
# DashScopeProvider
# ═══════════════════════════════════════════════════════════════

def test_dashscope_generate():
    from providers.dashscope_provider import DashScopeProvider

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.output.text = "这是回答"
    mock_resp.usage.input_tokens = 50
    mock_resp.usage.output_tokens = 30

    with patch("providers.dashscope_provider.retry_call", return_value=mock_resp):
        provider = DashScopeProvider("qwen-plus", "fake-key")
        result = provider.generate("测试 prompt")

        assert result.text == "这是回答"
        assert result.usage.input_tokens == 50
        assert result.usage.output_tokens == 30


def test_dashscope_generate_error_status():
    from providers.dashscope_provider import DashScopeProvider

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.message = "server error"

    with patch("providers.dashscope_provider.retry_call", return_value=mock_resp):
        provider = DashScopeProvider("qwen-plus", "fake-key")
        with pytest.raises(RuntimeError, match="生成失败"):
            provider.generate("prompt")


def test_dashscope_generate_stream():
    from providers.dashscope_provider import DashScopeProvider

    class FakeChunk:
        def __init__(self, text, status_code=200):
            self.status_code = status_code
            self.output = MagicMock()
            self.output.text = text

    chunks = [
        FakeChunk(""),
        FakeChunk("Hello"),
        FakeChunk("Hello World"),
        FakeChunk("Hello World!"),
    ]

    with patch("providers.dashscope_provider.retry_call", return_value=chunks):
        provider = DashScopeProvider("qwen-plus", "fake-key")
        result = list(provider.generate_stream("test prompt"))
        assert result == ["Hello", " World", "!"]


def test_dashscope_extract_usage_no_usage():
    from providers.dashscope_provider import DashScopeProvider

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    del mock_resp.usage

    usage = DashScopeProvider._extract_usage(mock_resp)
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0


# ═══════════════════════════════════════════════════════════════
# CompatibleProvider (DeepSeek / Doubao / Moonshot / Zhipu)
# ═══════════════════════════════════════════════════════════════

def test_compatible_generate():
    from providers.compatible_provider import CompatibleProvider

    mock_msg = MagicMock()
    mock_msg.content = "兼容回答"

    mock_choice = MagicMock()
    mock_choice.message = mock_msg

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 20
    mock_usage.completion_tokens = 15

    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_resp.usage = mock_usage

    with patch("providers.compatible_provider.retry_call", return_value=mock_resp):
        provider = CompatibleProvider("deepseek-v4-flash", "sk-test")
        result = provider.generate("prompt")

        assert result.text == "兼容回答"
        assert result.usage.input_tokens == 20
        assert result.usage.output_tokens == 15


def test_compatible_generate_stream():
    from providers.compatible_provider import CompatibleProvider

    class FakeDelta:
        def __init__(self, content):
            self.content = content

    class FakeChoice:
        def __init__(self, delta):
            self.delta = delta

    chunks = [
        MagicMock(choices=[FakeChoice(FakeDelta("Hello"))]),
        MagicMock(choices=[FakeChoice(FakeDelta(" World"))]),
        MagicMock(choices=[FakeChoice(FakeDelta("!"))]),
    ]

    with patch("providers.compatible_provider.retry_call", return_value=chunks):
        provider = CompatibleProvider("deepseek-v4-flash", "sk-test")
        result = list(provider.generate_stream("prompt"))
        assert result == ["Hello", " World", "!"]


def test_compatible_custom_base_url():
    from providers.compatible_provider import CompatibleProvider
    provider = CompatibleProvider(
        "doubao-seed-2.0-pro", "sk-db",
        base_url="https://ark.cn-beijing.volces.com/api/v3"
    )
    assert str(provider.client.base_url).rstrip("/") == "https://ark.cn-beijing.volces.com/api/v3"


# ═══════════════════════════════════════════════════════════════
# TokenTracker per-provider pricing
# ═══════════════════════════════════════════════════════════════

def test_token_tracker_custom_pricing():
    from token_tracker import TokenTracker
    t = TokenTracker(pricing={"gen_input": 0.01, "gen_output": 0.05})
    t.record_generation(1000, 500)
    cost = t.estimated_cost
    assert 0.03 <= cost <= 0.04


def test_token_tracker_update_pricing():
    from token_tracker import TokenTracker
    t = TokenTracker()
    t.record_generation(1000, 1000)
    cost_before = t.estimated_cost
    t.update_pricing({"gen_input": 0.01, "gen_output": 0.05})
    cost_after = t.estimated_cost
    assert cost_after > cost_before


def test_token_tracker_pricing_with_embedding():
    from token_tracker import TokenTracker
    t = TokenTracker(pricing={
        "embedding": 0.001,
        "gen_input": 0.002,
        "gen_output": 0.003,
    })
    t.record_embedding(10000)
    t.record_generation(5000, 3000)
    cost = t.estimated_cost
    assert 0.02 <= cost <= 0.04


# ═══════════════════════════════════════════════════════════════
# GenerationResult
# ═══════════════════════════════════════════════════════════════

def test_generation_result_re_exported():
    from generator import GenerationResult
    from providers.base import GenerationResult as BaseResult
    assert GenerationResult is BaseResult


# ═══════════════════════════════════════════════════════════════
# Retry: OpenAI SDK error types (used by CompatibleProvider)
# ═══════════════════════════════════════════════════════════════

def test_is_retryable_openai_rate_limit():
    from retry import _is_retryable
    import openai
    err = openai.RateLimitError(
        message="rate limit",
        response=MagicMock(status_code=429),
        body=None,
    )
    assert _is_retryable(err) is True


def test_is_retryable_openai_connection_error():
    from retry import _is_retryable
    import openai
    err = openai.APIConnectionError(request=MagicMock())
    assert _is_retryable(err) is True
