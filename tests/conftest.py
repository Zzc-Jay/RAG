"""pytest 根 conftest — 统一 src/ 路径配置 + CI 环境 mock 外部 API。"""
from __future__ import annotations

import hashlib
import os
import random
import sys
from unittest.mock import MagicMock, patch

import pytest

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)


def _make_fake_embedding(text: str, dim: int = 1024) -> list[float]:
    digest = hashlib.md5(text.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:4], "big")
    rng = random.Random(seed)
    vec = [rng.gauss(0, 1) for _ in range(dim)]
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec] if norm > 0 else [0.0] * dim


class _FakeEmbeddingResponse:
    def __init__(self, texts: list[str], dim: int = 1024, status_code: int = 200):
        self.status_code = status_code
        self.message = "" if status_code == 200 else "mock error"
        self.output = _FakeOutput([_make_fake_embedding(t, dim) for t in texts])
        self.usage = MagicMock()
        self.usage.get.return_value = sum(max(1, len(t) // 3) for t in texts)


class _FakeOutput:
    def __init__(self, embeddings_list: list[list[float]]):
        self._d = {"embeddings": [{"embedding": e} for e in embeddings_list]}

    def __getitem__(self, key: str):
        return self._d[key]


def _is_ci_or_fake_key() -> bool:
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    return bool(os.environ.get("CI")) or key.startswith("fake-") or key == ""


@pytest.fixture(autouse=True)
def _mock_external_apis_in_ci():
    """CI 环境 mock 外部 API（embedder + DashScopeProvider 类方法）。

    不在 provider 工厂层 mock，而是在 DashScopeProvider 类方法层 mock，
    让 test_rag.py 中的 patch.object(DashScopeProvider, ...) 仍能生效。
    """
    if not _is_ci_or_fake_key():
        yield
        return

    # 预导入，确保模块在 sys.modules 中
    import embedder  # noqa: F401
    import providers.dashscope_provider  # noqa: F401

    os.environ["DASHSCOPE_API_KEY"] = "fake-ci-mock-key"

    from providers.base import GenerationResult
    from token_tracker import TokenUsage

    def _fake_generate(self, prompt):
        return GenerationResult(
            text="这是 CI 自动生成的测试回答。",
            usage=TokenUsage(input_tokens=10, output_tokens=8),
        )

    def _fake_generate_stream(self, prompt):
        for word in ["这是", "CI", "环境", "生成", "的", "测试", "回答", "。"]:
            yield word

    with patch("embedder.TextEmbedding.call") as mock_embed:
        mock_embed.side_effect = lambda **kwargs: _FakeEmbeddingResponse(
            kwargs.get("input", ["default"]), dim=1024
        )

        with patch.object(
            providers.dashscope_provider.DashScopeProvider,
            "generate",
            _fake_generate,
        ), patch.object(
            providers.dashscope_provider.DashScopeProvider,
            "generate_stream",
            _fake_generate_stream,
        ):
            yield

    os.environ["DASHSCOPE_API_KEY"] = os.environ.get("DASHSCOPE_API_KEY", "")
