from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from config import MAX_RETRIES, RETRY_BASE_DELAY, RETRY_MAX_DELAY
from logging_config import get_logger

logger = get_logger("retry")

# 尝试导入 DashScope 和 requests 异常类型，失败时不阻塞
try:
    import dashscope
    _DASHSCOPE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _DASHSCOPE_AVAILABLE = False

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _REQUESTS_AVAILABLE = False

try:
    import openai
    _OPENAI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _OPENAI_AVAILABLE = False


def _is_retryable(error: Exception) -> bool:
    """判断异常是否属于可恢复类型（值得重试）。

    可恢复:
    - HTTP 429 (限流) — 等一等配额恢复
    - HTTP 5xx (服务端临时故障) — 下次可能正常
    - 网络连接错误 — 网络抖动
    - 超时 — 临时延迟

    不可恢复:
    - HTTP 4xx (客户端错误，如 400/401/403/404) — 重试不会改变结果
    - ValueError / TypeError — 代码 bug
    """
    # 网络/超时类异常: 可恢复
    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return True

    # requests 库的异常
    if _REQUESTS_AVAILABLE:
        if isinstance(error, (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        )):
            return True
        if isinstance(error, requests.exceptions.HTTPError):
            resp = getattr(error, "response", None)
            if resp is not None:
                code = resp.status_code
                return code == 429 or code >= 500
            return False

    # OpenAI SDK 异常 (Compatible 端点通用): RateLimitError / APIConnectionError 可恢复
    if _OPENAI_AVAILABLE:
        if isinstance(error, (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.InternalServerError,
            openai.APITimeoutError,
        )):
            return True

    # 如果异常消息包含 HTTP 状态码，按状态码判断
    # 用 \b 边界匹配，适应 "500", "500:", "500 " 等各种格式
    import re
    msg = str(error).lower()
    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return True
    if re.search(r"\b5(?:0[0-3]|0[5-9]|1[0-9]|2[0-9]|3[0-9])\b", msg):
        return True

    return False


def retry_call(
    func: Callable[..., Any],
    *args: Any,
    max_retries: int = MAX_RETRIES,
    base_delay: float = RETRY_BASE_DELAY,
    max_delay: float = RETRY_MAX_DELAY,
    **kwargs: Any,
) -> Any:
    """用指数退避重试调用 func。

    每次失败后等待 base_delay * 2^attempt 秒，最多重试 max_retries 次。
    重试耗尽后抛出 RuntimeError，包含最后一次的异常信息。

    对可恢复异常（网络、5xx、429）自动重试；
    不可恢复异常（4xx 参数错误等）立即抛出，不浪费重试次数。
    """
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            result = func(*args, **kwargs)

            # DashScope 接口即使不抛异常，也可能返回非 200 状态码
            if hasattr(result, "status_code") and result.status_code and result.status_code >= 500:
                msg = getattr(result, "message", "") or getattr(result, "text", "") or ""
                raise RuntimeError(f"API 返回 {result.status_code}: {msg}")

            return result
        except Exception as e:
            last_error = e

            # 最后一次尝试失败 → 不再重试
            if attempt >= max_retries:
                logger.error(f"重试 {max_retries} 次后仍失败: {e}")
                raise RuntimeError(
                    f"API 调用失败，已重试 {max_retries} 次。"
                    f"最后错误: {e}"
                ) from e

            # 不可恢复的错误 → 立即抛出
            if not _is_retryable(e):
                logger.error(f"不可恢复的错误，不重试: {e}")
                raise

            # 可恢复 → 等待后重试
            delay = min(base_delay * (2 ** attempt), max_delay)
            logger.warning(f"API 调用失败 (尝试 {attempt + 1}/{max_retries + 1})，"
                           f"{delay:.1f} 秒后重试: {e}")
            time.sleep(delay)

    # 理论上不会走到这里（上面已 raise），但让类型检查满意
    raise RuntimeError(f"重试耗尽: {last_error}")
