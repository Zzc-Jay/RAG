from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TokenUsage:
    """单次 API 调用的 token 用量。"""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class TokenTracker:
    """Session 级 token 累计统计器。

    分别追踪 embedding 和 generation 的用量，提供费用估算。
    支持通过 pricing dict 切换不同模型的价格（per 1K tokens）。

    pricing 格式: {"embedding": float, "gen_input": float, "gen_output": float}
    不传 pricing 时使用 DashScope 默认价格。
    """

    DEFAULT_PRICE_EMBEDDING = 0.0005    # per 1K tokens
    DEFAULT_PRICE_GEN_INPUT = 0.0008    # per 1K tokens
    DEFAULT_PRICE_GEN_OUTPUT = 0.002    # per 1K tokens

    def __init__(self, pricing: dict[str, float] | None = None):
        self.embedding_tokens: int = 0
        self.generation_input: int = 0
        self.generation_output: int = 0
        self.call_count: int = 0
        self._history: list[dict] = []
        self._pricing: dict[str, float] = pricing or {}

    def update_pricing(self, pricing: dict[str, float]) -> None:
        """运行时切换模型定价（用户切换模型时调用）。"""
        self._pricing = pricing

    def record_embedding(self, tokens: int) -> None:
        """记录一次 embedding API 调用的 token 用量。"""
        self.embedding_tokens += tokens
        self.call_count += 1
        self._history.append({"type": "embedding", "tokens": tokens})

    def record_generation(self, input_tokens: int, output_tokens: int) -> None:
        """记录一次 generation API 调用的 token 用量。"""
        self.generation_input += input_tokens
        self.generation_output += output_tokens
        self.call_count += 1
        self._history.append({
            "type": "generation",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        })

    @property
    def total_tokens(self) -> int:
        return self.embedding_tokens + self.generation_input + self.generation_output

    @property
    def estimated_cost(self) -> float:
        """估算总费用（元），优先使用 _pricing，fallback 到默认价格。"""
        cost = 0.0
        cost += (self.embedding_tokens / 1000) * self._pricing.get(
            "embedding", self.DEFAULT_PRICE_EMBEDDING)
        cost += (self.generation_input / 1000) * self._pricing.get(
            "gen_input", self.DEFAULT_PRICE_GEN_INPUT)
        cost += (self.generation_output / 1000) * self._pricing.get(
            "gen_output", self.DEFAULT_PRICE_GEN_OUTPUT)
        return cost

    @property
    def summary(self) -> dict:
        return {
            "embedding_tokens": self.embedding_tokens,
            "generation_input": self.generation_input,
            "generation_output": self.generation_output,
            "total_tokens": self.total_tokens,
            "estimated_cost": round(self.estimated_cost, 6),
        }


def format_cost(cost: float) -> str:
    """格式化费用显示，最小单位为分 (¥0.01)。

    - cost < 0.01: 显示 "<¥0.01"
    - cost >= 0.01: 显示两位小数，如 "¥0.35"
    """
    if cost < 0.00005:  # 几乎为 0
        return "—"
    if cost < 0.01:
        return "<¥0.01"
    return f"¥{cost:.2f}"
