from __future__ import annotations

from datetime import datetime
from typing import TypedDict


class Turn(TypedDict):
    """单轮对话。"""
    question: str
    answer: str
    references: list[dict]
    feedback: str | None  # "up", "down", or None
    timestamp: str


Conversation = list[Turn]


def create_conversation() -> Conversation:
    """创建一个空对话。"""
    return []


def add_turn(
    conv: Conversation,
    question: str,
    answer: str,
    references: list[dict],
) -> Conversation:
    """追加一轮问答到对话末尾。"""
    turn: Turn = {
        "question": question,
        "answer": answer,
        "references": references,
        "feedback": None,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    conv.append(turn)
    return conv


def get_history(conv: Conversation, max_turns: int = 5) -> list[Turn]:
    """返回最近 N 轮对话，用于注入 prompt 上下文。"""
    if max_turns <= 0:
        return []
    return conv[-max_turns:]


def set_feedback(conv: Conversation, turn_index: int, feedback: str | None) -> None:
    """设置某轮对话的反馈状态。feedback: 'up', 'down', None。"""
    if 0 <= turn_index < len(conv):
        conv[turn_index]["feedback"] = feedback


def format_for_prompt(turns: list[Turn]) -> str:
    """将历史轮次格式化为 LLM prompt 可用的对话历史文本。

    输出格式:
    对话历史：
    用户：什么是 RAG？
    助手：RAG 是检索增强生成技术...

    用户：它有什么优点？
    助手：RAG 的主要优点包括...

    注意：不包含当前轮（最后一轮），因为当前问题会单独放在 prompt 中。
    """
    if not turns:
        return ""

    lines = ["对话历史："]
    for turn in turns:
        lines.append(f"用户：{turn['question']}")
        lines.append(f"助手：{turn['answer']}")
        lines.append("")  # 空行分隔

    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数。

    中文字符约 1 char = 0.5 token，英文字符约 1 char = 0.25 token。
    这里取折中值 0.4 token/char，用于在无法从 API 获取精确值时估算。
    """
    if not text:
        return 0
    return max(1, int(len(text) * 0.4))
