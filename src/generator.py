"""生成模块 — 构建 prompt 并委托给 LLM Provider 生成回答。

教学点 — 委托模式（Delegation Pattern）：
1. generator.py 只负责「构建 prompt」——拼接对话历史 + 参考资料 + 问题，
   不关心具体调用哪个 LLM 的 API。
2. 实际生成由 provider 完成，通过 get_provider() 工厂动态选择实例。
   新增厂商不需要改这里的代码。
3. build_prompt 使用单段文本格式（兼容 DashScope 的 prompt 参数），
   chat 风格 API（OpenAI/Anthropic）的 provider 内部自行转换为 messages。
"""

from __future__ import annotations

import time
from collections.abc import Generator

from config import LLM_MODEL, MAX_HISTORY_TURNS, get_api_keys
from conversation import get_history, format_for_prompt, Conversation
from retry import retry_call
from logging_config import get_logger
from providers import get_provider, GenerationResult  # re-exported

logger = get_logger("generator")

# Re-export for backward compatibility
__all__ = ["GenerationResult", "build_prompt", "generate", "generate_stream"]


def build_prompt(
    query: str,
    docs: list[dict],
    history_text: str = "",
) -> str:
    """构建带引用标注和对话历史的 prompt。

    docs: [{"text": "...", "source": "...", "page": int}, ...]
    history_text: 格式化后的对话历史文本（可为空）
    """
    parts: list[str] = []

    # 对话历史
    if history_text:
        parts.append(history_text)
        parts.append("---")

    # 参考资料
    ref_parts: list[str] = []
    for i, d in enumerate(docs, start=1):
        src = d.get("source", "未知")
        pg = d.get("page", "?")
        ref_parts.append(f"[{i}] (来源: {src}, 第{pg}页)\n{d['text']}")
    references = "\n\n".join(ref_parts)

    parts.append(
        "请根据以下参考资料回答问题。每个参考资料以 [序号] 标记来源。\n"
        "如果某个资料与问题无关，不要强行使用它。\n"
        "回答中用 [n] 标注引用来源。\n"
    )

    # 如果有对话历史，提示模型可以引用上文
    if history_text:
        parts.append("请结合对话历史中的上下文理解当前问题。\n")

    parts.append(f"参考资料：\n{references}")
    parts.append(f"\n当前问题：{query}\n回答：")

    return "\n".join(parts)


def _get_provider(model: str | None = None):
    """根据当前配置（或指定 model）创建 provider 实例。"""
    return get_provider(model or LLM_MODEL, get_api_keys())


def generate_stream(
    query: str,
    docs: list[dict],
    conversation: Conversation | None = None,
    max_history_turns: int = MAX_HISTORY_TURNS,
    model: str | None = None,
) -> Generator[str, None, None]:
    """流式生成回答（含对话历史上下文）。

    query: 用户问题
    docs: 检索到的参考文档列表
    conversation: 对话历史（不含当前轮）
    max_history_turns: 注入 prompt 的历史轮数
    model: 模型名称，None 则使用 config.LLM_MODEL

    Yields: 回答文本片段（token 级）
    """
    provider = _get_provider(model)

    # 构建对话历史文本
    history_text = ""
    if conversation:
        history_turns = get_history(conversation, max_history_turns)
        if history_turns:
            history_text = format_for_prompt(history_turns)

    prompt = build_prompt(query, docs, history_text)
    logger.info(
        f"开始流式生成 [{provider.model}]: prompt 长度 {len(prompt)} 字符，"
        f"参考 {len(docs)} 个文档"
    )

    t0 = time.perf_counter()
    token_count = 0
    try:
        for chunk in provider.generate_stream(prompt):
            token_count += 1
            yield chunk
    except (ConnectionError, OSError) as e:
        if token_count > 0:
            logger.warning(f"流式生成连接中断（已产出 {token_count} tokens）: {e}")
            yield "\n\n[生成中断，以上为已返回的部分内容]"
        else:
            raise RuntimeError(f"流式生成连接失败: {e}") from e
    except Exception as e:
        raise RuntimeError(f"模型 [{provider.model}] 调用失败: {e}") from e

    elapsed = time.perf_counter() - t0
    logger.info(
        f"流式生成完成 [{provider.model}]: "
        f"约 {token_count} tokens，耗时 {elapsed:.2f}s"
    )


def generate(
    query: str,
    docs: list[dict],
    conversation: Conversation | None = None,
    model: str | None = None,
) -> GenerationResult:
    """非流式生成回答，返回完整结果含 token 用量。

    用于测试或需要 token 统计的场景。
    """
    provider = _get_provider(model)

    history_text = ""
    if conversation:
        history_turns = get_history(conversation, MAX_HISTORY_TURNS)
        if history_turns:
            history_text = format_for_prompt(history_turns)

    prompt = build_prompt(query, docs, history_text)
    logger.info(f"开始生成 [{provider.model}]: prompt 长度 {len(prompt)} 字符")

    t0 = time.perf_counter()
    try:
        result = provider.generate(prompt)
    except Exception as e:
        raise RuntimeError(f"模型 [{provider.model}] 调用失败: {e}") from e

    elapsed = time.perf_counter() - t0
    logger.info(
        f"生成完成 [{provider.model}]: 输入 {result.usage.input_tokens}t · "
        f"输出 {result.usage.output_tokens}t · 耗时 {elapsed:.2f}s"
    )
    return result
