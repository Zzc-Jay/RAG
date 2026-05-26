"""重排序 — 用 LLM 对初步检索结果进行精排，提升 top-K 精度。

教学点：
1. 检索的两阶段设计：粗排（向量+BM25，快但粗糙）→ 精排（LLM，慢但精准）
2. LLM 打分比 Cross-Encoder 更灵活（能理解语义），但成本更高、速度更慢
3. 批量打分 vs 逐个打分：一个 prompt 评估全部候选，减少 API 调用次数
4. 解析容错：LLM 可能输出非预期格式，用正则兜底
"""

from __future__ import annotations

import re

from providers import get_provider
from config import LLM_MODEL


_RERANK_PROMPT = """评估每个文档片段与问题的语义相关性，为每个片段打一个 0-10 的整数分数。
10=完全匹配，0=完全无关。只输出 "[编号] 分数" 格式，每行一个，不要解释。

问题：{query}

{passages}

相关性分数："""


def _parse_scores(raw: str, count: int) -> list[float]:
    """从 LLM 响应中解析各文档的相关性分数。

    支持格式：
    - "[1] 8" / "[1]: 8" / "1. 8" / "[1] 8分"
    - 多行 / 逗号或分号分隔
    返回长度为 count 的分数列表，解析失败的文档给 0 分。
    """
    scores: dict[int, float] = {}

    # 逐个匹配 [数字] 分数 或 数字. 分数 模式
    pattern = r"\[?(\d+)\]?[:\s.、]+(\d+(?:\.\d+)?)"
    matches = re.findall(pattern, raw)

    for idx_str, score_str in matches:
        idx = int(idx_str) - 1  # convert to 0-indexed
        if 0 <= idx < count:
            try:
                s = float(score_str)
                scores[idx] = max(0.0, min(10.0, s))
            except ValueError:
                pass

    # 为所有位置填充分数（解析失败的给 0）
    return [scores.get(i, 0.0) for i in range(count)]


def rerank(
    query: str,
    documents: list[dict],
    top_k: int = 5,
    model: str | None = None,
) -> list[dict]:
    """对候选文档进行 LLM 精排，返回 top-K。

    若候选数 <= top_k，跳过打分直接返回（避免不必要的 API 调用）。
    LLM 调用失败时，退化为按原始分数排序返回 top-K。
    """
    if len(documents) <= top_k:
        return documents

    if model is None:
        model = LLM_MODEL

    # 构建待打分 passages（截断长文本）
    passages_lines: list[str] = []
    for i, doc in enumerate(documents):
        text = doc["text"][:400]
        passages_lines.append(f"[{i + 1}] {text}")

    prompt = _RERANK_PROMPT.format(
        query=query,
        passages="\n\n".join(passages_lines),
    )

    try:
        provider = get_provider(model)
        result = provider.generate(prompt)
        scores = _parse_scores(result.text, len(documents))
    except Exception:
        # LLM 调用失败：退化到原始排序
        scored = documents[:]
        scored.sort(key=lambda d: d.get("score", 0.0), reverse=True)
        return scored[:top_k]

    # 附加分数并排序
    for i, s in enumerate(scores):
        documents[i]["rerank_score"] = s

    documents.sort(key=lambda d: d.get("rerank_score", 0.0), reverse=True)
    return documents[:top_k]
