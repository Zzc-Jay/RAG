"""LLM Provider 抽象基类。

教学点 — 为什么要抽象 Provider？
1. 不同 LLM 的 API 形状不同：
   - DashScope: prompt 参数（单段文本）
   - OpenAI/Anthropic: messages 参数（对话格式，含 system/user/assistant 角色）
2. 流式响应的迭代方式也不同：
   - DashScope: for chunk in resp（普通迭代器）
   - OpenAI: for chunk in resp（普通迭代器）
   - Anthropic: with client.messages.stream(...) as s: for text in s.text_stream（上下文管理器）
3. 通过抽象基类，generator.py 不关心底层调用细节，只需 provider.generate_stream(prompt)。
   新增厂商只需实现 BaseProvider，不改业务代码——这就是开闭原则（OCP）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Generator
from dataclasses import dataclass

from token_tracker import TokenUsage


@dataclass
class GenerationResult:
    """生成结果，包含回答文本和 token 用量。"""
    text: str
    usage: TokenUsage


class BaseProvider(ABC):
    """LLM Provider 抽象基类。

    每个 provider 子类需要实现:
    - generate(prompt) → GenerationResult
    - generate_stream(prompt) → Generator[str]
    """

    def __init__(self, model: str, api_key: str):
        self.model = model
        self.api_key = api_key

    @abstractmethod
    def generate(self, prompt: str) -> GenerationResult:
        """非流式生成，返回完整结果含 token 用量。"""

    @abstractmethod
    def generate_stream(self, prompt: str) -> Generator[str, None, None]:
        """流式生成，逐 token yield 文本片段。"""

    @staticmethod
    def _build_messages(prompt_text: str) -> list[dict[str, str]]:
        """将单段 prompt 文本转为 chat messages 格式。

        DashScope 使用原生 prompt 参数，不需要 messages，所以它的 provider
        不调用此方法。OpenAI/Anthropic 等 chat 风格 API 调用此方法将 prompt
        包装为单个 user 消息。
        """
        return [{"role": "user", "content": prompt_text}]
