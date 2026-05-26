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


# ── 假 Embedding（确定性向量，相同文本 → 相同向量）────────────────

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


class _FakeProvider:
    def __init__(self):
        self.model = "fake-ci-model"
        self.api_key = "fake-ci-key"

    def generate(self, prompt: str, token_tracker=None):
        from providers.base import GenerationResult
        from token_tracker import TokenUsage
        # 判断场景：精排 → 返回分数；问答 → 返回回答
        if "相关性分数" in prompt:
            text = "[1] 8\n[2] 9"
        elif "改写" in prompt:
            text = prompt.split("\n")[-1].replace("当前问题：", "").strip()
        else:
            text = "这是 CI 环境自动生成的测试回答。"
        return GenerationResult(
            text=text,
            usage=TokenUsage(input_tokens=10, output_tokens=max(1, len(text) // 3)),
        )

    def generate_stream(self, prompt: str, token_tracker=None):
        text = self.generate(prompt, token_tracker).text
        for char in text:
            yield char


# ── CI 环境自动 mock ──────────────────────────────────────────────

def _is_ci_or_fake_key() -> bool:
    """判断是否需要 mock 外部 API：CI 环境或 API Key 是假值。"""
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    return bool(os.environ.get("CI")) or key.startswith("fake-") or key == ""


@pytest.fixture(autouse=True)
def _mock_external_apis_in_ci():
    """当 API Key 为假值时，mock 所有外部 API 调用。

    只在 embedder / generator / query_rewriter / reranker 边界 mock，
    内部组件（loader/chunker/ChromaDB/BM25）真实运行。
    """
    if not _is_ci_or_fake_key():
        yield
        return

    # 预导入，确保模块在 sys.modules 中再 patch
    import embedder  # noqa: F401
    import generator  # noqa: F401
    import query_rewriter  # noqa: F401
    import reranker  # noqa: F401

    os.environ["DASHSCOPE_API_KEY"] = "fake-ci-mock-key"

    with patch("embedder.TextEmbedding.call") as mock_embed:
        mock_embed.side_effect = lambda **kwargs: _FakeEmbeddingResponse(
            kwargs.get("input", ["default"]), dim=1024
        )

        with patch("generator.get_provider") as mock_gen, \
             patch("query_rewriter.get_provider") as mock_qr, \
             patch("reranker.get_provider") as mock_rr:

            fake = _FakeProvider()
            mock_gen.return_value = fake
            mock_qr.return_value = fake
            mock_rr.return_value = fake

            yield

    # 恢复
    os.environ["DASHSCOPE_API_KEY"] = os.environ.get("DASHSCOPE_API_KEY", "")
