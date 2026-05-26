"""查询改写 — 基于对话历史将模糊问题转为独立完整的问题。

教学点：
1. 多轮对话中的指代消解（anaphora resolution）是 RAG 检索质量的关键瓶颈
2. LLM 改写比规则改写更灵活——能处理歧义、省略、隐式引用
3. 空历史直接短路，不浪费 API 调用
4. 改写失败时 fallback 原始 query，保证系统不崩溃
"""

from __future__ import annotations

from providers import get_provider
from config import LLM_MODEL


_REWRITE_PROMPT = """你是查询改写助手。根据对话历史，将用户当前问题改写为一个完整独立的问题。
规则：
1. 如果问题已经独立完整（不依赖上下文即可理解），原样返回
2. 如果问题包含代词（它、他、这个、那个等）或省略了主语/宾语，根据历史补全
3. 改写后的问题应与原问题语义一致，不添加多余信息
4. 只输出改写后的问题文本，不要添加解释、引号或前缀

对话历史：
{history}

当前问题：{query}

改写后的问题："""


def rewrite_query(
    query: str,
    history_text: str = "",
    model: str | None = None,
) -> str:
    """改写查询，使其在脱离对话上下文后仍然完整。

    若 history_text 为空，直接返回原 query（无 API 调用）。
    改写失败时返回原 query 作为 fallback。
    """
    if not history_text or not history_text.strip():
        return query

    if model is None:
        model = LLM_MODEL

    prompt = _REWRITE_PROMPT.format(history=history_text.strip(), query=query)

    try:
        provider = get_provider(model)
        result = provider.generate(prompt)
        rewritten = result.text.strip()

        # 过滤常见的 LLM "客气话"
        prefixes_to_strip = [
            "改写后的问题：", "改写后的问题:", "改写后：", "改写后:",
            "完整问题：", "完整问题:", "独立问题：", "独立问题:",
            "改写的问题：", "改写的问题:",
        ]
        for prefix in prefixes_to_strip:
            if rewritten.startswith(prefix):
                rewritten = rewritten[len(prefix):].strip()

        # 如果改写结果为空或过长（异常），fallback
        if not rewritten:
            return query
        if len(rewritten) > len(query) * 5:
            return query

        return rewritten
    except Exception:
        return query
