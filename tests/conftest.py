"""pytest 根 conftest — 统一 src/ 路径配置 + CI 环境 mock 外部 API + 测试用户上下文。"""
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


# ── 测试用户上下文 ─────────────────────────────────────────────────
# 每个测试自动设置 fake 用户上下文，避免 LookupError("未设置用户上下文")

@pytest.fixture(autouse=True)
def _setup_test_user_context():
    """为所有测试设置测试用户上下文。

    1. 设置 contextvar → 直接调用内部函数时可用
    2. 覆盖 FastAPI get_current_user 依赖 → TestClient 请求跳过 JWT 验证
    """
    from auth import _current_user_id, get_current_user, set_current_user_id
    from api import app

    test_user = {"id": "test-user-001", "username": "testuser"}

    async def _fake_get_current_user():
        set_current_user_id(test_user["id"])
        return test_user

    # 保存旧覆盖（如果有的话）
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _fake_get_current_user

    token = _current_user_id.set(test_user["id"])

    yield

    _current_user_id.reset(token)
    app.dependency_overrides = old_overrides


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


def _make_fake_gen_response(stream: bool = False):
    """创建假的 DashScope Generation 响应，兼容新旧格式。"""
    if stream:
        return _FakeStreamGenResponse()
    return _FakeNonStreamGenResponse()


class _FakeStreamGenResponse:
    """流式响应 — 迭代器（支持 next(iter(resp)) + for chunk in resp）。"""
    def __init__(self):
        chunks = ["这是", "CI", "环境", "生成", "的", "测试", "回答", "。"]
        self._chunks = iter(chunks)

    def __iter__(self):
        return self

    def __next__(self):
        chunk_text = next(self._chunks)
        chunk = MagicMock()
        chunk.status_code = 200
        choice = MagicMock()
        choice.message.content = chunk_text
        chunk.output.choices = [choice]
        return chunk


class _FakeNonStreamGenResponse:
    """非流式响应 — 旧格式 output.text（qwen-plus 用）。"""
    status_code = 200
    message = ""

    class output:
        text = "这是 CI 自动生成的测试回答。"

    class usage:
        input_tokens = 10
        output_tokens = 8


def _is_ci_or_fake_key() -> bool:
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    return bool(os.environ.get("CI")) or key.startswith("fake-") or key == ""


@pytest.fixture(autouse=True)
def _mock_external_apis_in_ci():
    """CI 环境 mock DashScope API 调用（embedding + generation）。

    在 dashscope API 入口层 mock，让上层测试各自的 mock 仍能生效。
    """
    if not _is_ci_or_fake_key():
        yield
        return

    # 预导入，确保模块在 sys.modules 中
    import embedder  # noqa: F401

    os.environ["DASHSCOPE_API_KEY"] = "fake-ci-mock-key"

    with patch("embedder.TextEmbedding.call") as mock_embed:
        mock_embed.side_effect = lambda **kwargs: _FakeEmbeddingResponse(
            kwargs.get("input", ["default"]), dim=1024
        )

        with patch("dashscope.Generation.call") as mock_gen:
            mock_gen.side_effect = lambda **kw: _make_fake_gen_response(
                stream=kw.get("stream", False)
            )

            yield

    os.environ["DASHSCOPE_API_KEY"] = os.environ.get("DASHSCOPE_API_KEY", "")
