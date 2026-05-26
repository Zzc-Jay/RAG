"""E2E 测试共享 fixtures — mock 外部 API，提供 KB 生命周期管理。

Mock 原则：
- 仅在「外部 API 边界」mock —— DashScope Embedding / LLM
- 内部组件（loader/chunker/ChromaDB/BM25/RRF）全部真实运行
- 相同文本 → 确定性向量 → ChromaDB 检索结果可预测
"""

from __future__ import annotations

import hashlib
import os
import random
import shutil
import time
from unittest.mock import MagicMock, patch

import pytest

# 预导入需要 patch 的模块，确保 AppTest 运行时模块已在 sys.modules 中
import embedder  # noqa: F401
import generator  # noqa: F401
import query_rewriter  # noqa: F401
import reranker  # noqa: F401


# ── 确定性假向量 ──────────────────────────────────────────────────

def make_fake_embedding(text: str, dim: int = 1024) -> list[float]:
    """生成确定性伪随机向量（L2 归一化）。

    相同文本永远产出相同向量，使 ChromaDB 检索结果可预测。
    不同文本的向量近似正交，模拟真实 embedding 的区分能力。
    """
    digest = hashlib.md5(text.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:4], "big")
    rng = random.Random(seed)
    vec = [rng.gauss(0, 1) for _ in range(dim)]
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec] if norm > 0 else [0.0] * dim


# ── 假 DashScope Embedding 响应 ───────────────────────────────────

class FakeEmbeddingOutput:
    """模拟 DashScope resp.output，支持下标访问。

    实际代码访问: resp.output["embeddings"] → list[dict]
    """

    def __init__(self, embeddings_list: list[list[float]]):
        self._embeddings = {
            "embeddings": [{"embedding": e} for e in embeddings_list],
        }

    def __getitem__(self, key: str):
        return self._embeddings[key]

    def __len__(self):
        return len(self._embeddings.get("embeddings", []))


class FakeEmbeddingResponse:
    """模拟 DashScope TextEmbedding.call() 返回值。

    实际代码访问:
    - resp.status_code → int
    - resp.message → str（仅错误时）
    - resp.output["embeddings"] → list[dict]
    - resp.usage.get("total_tokens", 0) → int
    """

    def __init__(self, texts: list[str], dim: int = 1024, status_code: int = 200):
        self.status_code = status_code
        self.message = "" if status_code == 200 else "mock error"
        self.output = FakeEmbeddingOutput([make_fake_embedding(t, dim) for t in texts])
        self.usage = MagicMock()
        total_tokens = sum(max(1, len(t) // 3) for t in texts)
        self.usage.get.return_value = total_tokens


# ── 假 LLM Provider ───────────────────────────────────────────────

class FakeProvider:
    """模拟 LLM Provider，根据 prompt 类型返回对应格式的响应。

    设计要点：
    - generate() 返回 GenerationResult，generate_stream() 逐字符 yield
    - 根据 prompt 内容判断场景：改写/精排/问答
    - 一个实例被三个 patch 共享，保证行为一致性
    """

    def __init__(self):
        self.model = "fake-e2e-model"
        self.api_key = "fake-e2e-key"

    def generate(self, prompt: str, token_tracker=None):
        """非流式生成。"""
        from token_tracker import TokenUsage
        from providers.base import GenerationResult

        text = self._select_response(prompt)
        input_tokens = max(1, len(prompt) // 3)
        output_tokens = max(1, len(text) // 3)
        return GenerationResult(
            text=text,
            usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        )

    def generate_stream(self, prompt: str, token_tracker=None):
        """流式生成——逐字符 yield。"""
        text = self._select_response(prompt)
        for char in text:
            yield char

    def _select_response(self, prompt: str) -> str:
        """根据 prompt 类型选择合适响应。"""
        import re

        # 查询改写 —— 返回 "当前问题：" 后的文本，做指代消解
        if "查询改写助手" in prompt or "改写后的问题" in prompt:
            match = re.search(r"当前问题：(.+)", prompt)
            if match:
                query = match.group(1).strip()
                return query.replace("它", "RAG").replace("这个", "检索增强生成")
            return "什么是RAG？"

        # 精排评分 —— 为每个文档打分 8 或 9
        if "相关性分数" in prompt:
            indices = re.findall(r"\[(\d+)\]", prompt)
            if indices:
                count = len(set(indices))
                return "\n".join(
                    f"[{i+1}] {8 if i % 2 == 0 else 9}"
                    for i in range(count)
                )
            return "[1] 8"

        # 普通问答
        return "根据参考资料，这是一个测试回答，用于验证E2E管线是否正常运转。"


# ── fixtures ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _mock_external_apis():
    """自动 mock 所有外部 API，防止测试意外调用真实服务。

    patch 路径：
    - embedder.TextEmbedding.call: embedder.py 中 `from dashscope import TextEmbedding`
    - generator.get_provider: generator.py 中 `from providers import get_provider`
    - 同理 query_rewriter.get_provider 和 reranker.get_provider
    """
    os.environ["DASHSCOPE_API_KEY"] = "fake-e2e-test-key"

    with patch("embedder.TextEmbedding.call") as mock_embed:
        mock_embed.side_effect = lambda **kwargs: FakeEmbeddingResponse(
            kwargs.get("input", ["default"]),
            dim=1024,
        )

        with patch("generator.get_provider") as mock_gen, \
             patch("query_rewriter.get_provider") as mock_qr, \
             patch("reranker.get_provider") as mock_rr:

            fake_provider = FakeProvider()
            mock_gen.return_value = fake_provider
            mock_qr.return_value = fake_provider
            mock_rr.return_value = fake_provider

            yield

    os.environ.pop("DASHSCOPE_API_KEY", None)


@pytest.fixture
def kb_name() -> str:
    """生成唯一 KB 名称，避免测试间数据污染。"""
    ts = time.strftime("%H%M%S")
    return f"_e2e_{ts}"


@pytest.fixture
def kb_lifecycle(kb_name: str):
    """知识库生命周期：创建 → yield 名称 → 强制清理。

    清理策略：
    - delete_kb() 清理 registry + audit
    - 强制 rmtree ChromaDB + BM25 目录（Windows 可能文件锁）
    """
    from kb_manager import create_kb, delete_kb
    from config import CHROMA_DIR, BM25_DIR

    create_kb(kb_name)

    yield kb_name

    try:
        delete_kb(kb_name)
    except (ValueError, PermissionError):
        pass

    for base in (CHROMA_DIR, BM25_DIR):
        target = os.path.join(base, kb_name)
        if os.path.exists(target):
            try:
                shutil.rmtree(target)
            except (PermissionError, OSError):
                pass


@pytest.fixture
def test_txt_file():
    """创建含已知内容的临时 TXT 文件。

    内容包含两个可被检索的事实：
    1. Python 由 Guido van Rossum 创建，1991 年发布
    2. RAG 结合了信息检索和语言模型生成
    """
    import tempfile

    content = (
        "Python is a high-level programming language created by Guido van Rossum. "
        "It was first released in 1991. Python emphasizes code readability with "
        "its notable use of significant indentation.\n\n"
        "RAG (Retrieval-Augmented Generation) is a technique that combines "
        "information retrieval with language model generation. RAG improves "
        "factual accuracy by grounding LLM outputs in retrieved documents."
    )
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def test_txt_file2():
    """第二个临时 TXT，内容不同，用于多文档测试。"""
    import tempfile

    content = (
        "Deep learning is a subset of machine learning that uses neural networks "
        "with many layers. It has revolutionized computer vision and natural "
        "language processing since 2012. Frameworks like PyTorch and TensorFlow "
        "are widely used for building deep learning models."
    )
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass
