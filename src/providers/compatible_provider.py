"""OpenAI-Compatible Provider — 支持所有兼容 OpenAI API 协议的国产大模型。

教学点 — 为什么要统一用 CompatibleProvider：
1. DeepSeek、豆包（Doubao）、Moonshot（Kimi）、智谱（GLM）等国产大模型
   都暴露了 OpenAI 兼容的 `/v1/chat/completions` 端点。
2. 通过切换 base_url 参数，同一个 OpenAI SDK 可以对接所有厂商。
   这就是 API 标准化带来的工程红利——一套代码覆盖多个服务。
3. 流式响应格式与 OpenAI 一致：chunk.choices[0].delta.content 是增量文本。
4. 各厂商支持的端点：
   - DeepSeek:     https://api.deepseek.com/v1
   - 豆包 (Doubao): https://ark.cn-beijing.volces.com/api/v3
   - Moonshot:     https://api.moonshot.cn/v1
   - 智谱 (GLM):    https://open.bigmodel.cn/api/paas/v4
"""

from __future__ import annotations

from collections.abc import Generator

from openai import OpenAI

from retry import retry_call
from token_tracker import TokenUsage
from .base import BaseProvider, GenerationResult


class CompatibleProvider(BaseProvider):
    """OpenAI 协议兼容 Provider — 对接所有兼容端点。

    通过 base_url 参数切换厂商，行为完全一致。
    """

    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        super().__init__(model, api_key)
        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = OpenAI(**client_kwargs)

    def generate(self, prompt: str) -> GenerationResult:
        messages = self._build_messages(prompt)
        response = retry_call(
            self.client.chat.completions.create,
            model=self.model,
            messages=messages,
            stream=False,
        )
        usage = TokenUsage(
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
        )
        return GenerationResult(
            text=response.choices[0].message.content,
            usage=usage,
        )

    def generate_stream(self, prompt: str) -> Generator[str, None, None]:
        messages = self._build_messages(prompt)
        response = retry_call(
            self.client.chat.completions.create,
            model=self.model,
            messages=messages,
            stream=True,
        )
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
