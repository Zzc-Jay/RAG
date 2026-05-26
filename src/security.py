from __future__ import annotations

import html
import re
import time

# --- 输入校验 ---------------------------


def validate_question(text: str, max_length: int = 2000) -> str:
    """校验并净化用户提问。

    - 长度限制: 不超过 max_length 字符
    - XSS 防护: HTML 实体转义

    返回净化后的文本；不合法时抛出 ValueError。
    """
    if not text or not text.strip():
        raise ValueError("问题不能为空")

    text = text.strip()

    if len(text) > max_length:
        raise ValueError(f"问题长度不能超过 {max_length} 字符，当前 {len(text)} 字符")

    return html.escape(text)


def validate_kb_name(name: str, max_length: int = 50) -> str:
    """校验知识库名称。

    规则:
    - 长度 1-50 字符
    - 仅允许: 中英文字母、数字、下划线、短横线、空格

    返回 trim 后的名称；不合法时抛出 ValueError。
    """
    if not name or not name.strip():
        raise ValueError("知识库名称不能为空")

    name = name.strip()

    if len(name) > max_length:
        raise ValueError(f"知识库名称不能超过 {max_length} 字符")

    if not re.match(r'^[一-龥a-zA-Z0-9_\-\s]+$', name):
        raise ValueError("知识库名称仅支持中英文、数字、下划线、短横线")

    return name


def validate_file_extension(filename: str) -> str:
    """校验文件后缀是否在白名单内。

    白名单: .pdf, .txt, .md, .docx
    返回小写后缀；不合法时抛出 ValueError。
    """
    import os
    ext = os.path.splitext(filename)[1].lower()
    if not ext:
        raise ValueError("无法识别文件类型，请确保文件名包含后缀")

    allowed = {".pdf", ".txt", ".md", ".docx"}
    if ext not in allowed:
        raise ValueError(f"不支持的文件类型 '{ext}'，仅支持: {', '.join(sorted(allowed))}")

    return ext


# --- 速率限制 ---------------------------


class RateLimiter:
    """基于内存的滑动窗口速率限制器。

    用于限制单 session 的 API 调用频率，防止滥用。
    存储结构: dict[session_id, list[timestamp]]

    >>> rl = RateLimiter(max_requests=3, window_seconds=60)
    >>> rl.check("user_A")  # True
    """

    def __init__(self, max_requests: int = 20, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._store: dict[str, list[float]] = {}

    def check(self, session_id: str) -> bool:
        """检查是否允许本次请求，允许则记录时间戳并返回 True。"""
        now = time.monotonic()
        entries = self._store.get(session_id, [])

        # 清理窗口外的旧记录
        cutoff = now - self.window_seconds
        active = [t for t in entries if t > cutoff]

        if len(active) >= self.max_requests:
            self._store[session_id] = active
            return False

        active.append(now)
        self._store[session_id] = active
        return True

    def remaining(self, session_id: str) -> int:
        """返回当前窗口内剩余可用次数。"""
        now = time.monotonic()
        entries = self._store.get(session_id, [])
        cutoff = now - self.window_seconds
        active = [t for t in entries if t > cutoff]
        return max(0, self.max_requests - len(active))
