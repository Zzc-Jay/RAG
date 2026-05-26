"""DashScope Provider — 阿里云百炼（通义千问）。

教学点 — DashScope API 的特点：
1. 旧模型（qwen-plus/qwen-max/qwen-turbo）使用 prompt 参数 + output.text 响应格式，
   流式为累积文本，需要手动 diff 相邻 chunk。
2. 新模型（qwen3-max/qwen3-235b-a22b）虽然仍接受 prompt 参数，但响应使用
   output.choices[0].message.content 格式（与 OpenAI 一致），流式为增量文本。
3. 本 provider 同时兼容两种响应格式，通过检测 output.choices 是否存在来判断。
"""

from __future__ import annotations

from collections.abc import Generator

from dashscope import Generation

from retry import retry_call
from token_tracker import TokenUsage
from .base import BaseProvider, GenerationResult


class DashScopeProvider(BaseProvider):
    """DashScope（通义千问）Provider。

    直接调用 dashscope.Generation.call()，兼容旧/新两种响应格式。
    """

    def generate(self, prompt: str) -> GenerationResult:
        resp = retry_call(
            Generation.call,
            model=self.model,
            prompt=prompt,
            stream=False,
            api_key=self.api_key,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"生成失败: {resp.message}")
        text = self._extract_text(resp)
        usage = self._extract_usage(resp)
        return GenerationResult(text=text, usage=usage)

    def generate_stream(self, prompt: str) -> Generator[str, None, None]:
        resp = retry_call(
            Generation.call,
            model=self.model,
            prompt=prompt,
            stream=True,
            api_key=self.api_key,
        )
        # 检测响应格式：新模型用 choices 增量格式，旧模型用累积文本
        first = next(iter(resp), None)
        if first is None:
            return
        if first.status_code != 200:
            raise RuntimeError(f"生成失败: {first.message}")

        has_choices = self._has_choices(first)
        if has_choices:
            # 新格式：增量文本，直接 yield 每个 chunk
            if first.output.choices:
                delta = first.output.choices[0].message.content
                if delta:
                    yield delta
            for chunk in resp:
                if chunk.status_code != 200:
                    raise RuntimeError(f"生成失败: {chunk.message}")
                if chunk.output.choices:
                    delta = chunk.output.choices[0].message.content
                    if delta:
                        yield delta
        else:
            # 旧格式：累积文本，手动 diff 相邻 chunk
            prev = first.output.text or ""
            if prev:
                yield prev
            for chunk in resp:
                if chunk.status_code != 200:
                    raise RuntimeError(f"生成失败: {chunk.message}")
                current = chunk.output.text or ""
                if current:
                    delta = current[len(prev):]
                    if delta:
                        yield delta
                    prev = current

    @staticmethod
    def _extract_text(resp) -> str:
        """从响应中提取文本，兼容新旧两种响应格式。"""
        output = resp.output
        if output is None:
            return ""
        # 新格式 (qwen3-max 等): output.choices 是非空 list
        if hasattr(output, "choices") and isinstance(output.choices, list) and output.choices:
            return output.choices[0].message.content or ""
        # 旧格式 (qwen-plus 等): output.text 是字符串
        if hasattr(output, "text") and isinstance(output.text, str):
            return output.text
        return ""

    @staticmethod
    def _has_choices(first_chunk) -> bool:
        """检测流式响应的格式：choices 为非空 list 即为新格式。"""
        output = first_chunk.output
        if output is None:
            return False
        return (
            hasattr(output, "choices")
            and isinstance(output.choices, list)
            and len(output.choices) > 0
        )

    @staticmethod
    def _extract_usage(resp) -> TokenUsage:
        """从 DashScope 响应中提取 token 用量，失败时返回空。"""
        try:
            usage = resp.usage
            return TokenUsage(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
            )
        except (AttributeError, TypeError):
            return TokenUsage()
