"""LLM Provider 注册与工厂。

教学点 — 注册表模式（Registry Pattern）：
1. PROVIDER_REGISTRY 是一个 model_name → (ProviderClass, extra_kwargs) 的映射。
   新增模型只需加一行，get_provider() 自动处理实例化和 API Key 匹配。
2. 国产大模型大多兼容 OpenAI API 协议，通过 CompatibleProvider + base_url 统一接入。
3. API Key 解析逻辑:
   - DashScope → DASHSCOPE_API_KEY
   - 兼容端点 → 专用 Key（DOUBAO_API_KEY / DEEPSEEK_API_KEY / ...），fallback 通用 Key
4. 工厂模式的好处：调用方只需知道模型名称（字符串），不需要知道 provider 类。
"""

from __future__ import annotations

from .base import BaseProvider, GenerationResult
from .dashscope_provider import DashScopeProvider
from .compatible_provider import CompatibleProvider


def _build_registry() -> dict[str, tuple[type[BaseProvider], dict]]:
    """构建 provider 注册表。"""
    registry: dict[str, tuple[type[BaseProvider], dict]] = {}

    # DashScope — Alibaba Qwen（原生 API）
    registry.update({
        "qwen-plus": (DashScopeProvider, {}),
        "qwen3-max": (DashScopeProvider, {}),
    })

    # OpenAI-Compatible — DeepSeek / 豆包
    C = CompatibleProvider
    registry.update({
        # DeepSeek V4 (2026.04)
        "deepseek-v4-flash": (C, {"base_url": "https://api.deepseek.com/v1"}),
        "deepseek-v4-pro":   (C, {"base_url": "https://api.deepseek.com/v1"}),
        # 豆包 Seed 2.0 (2026.02) — 火山引擎 Ark
        "doubao-seed-2.0-pro":  (C, {"base_url": "https://ark.cn-beijing.volces.com/api/v3"}),
        "doubao-seed-2.0-lite": (C, {"base_url": "https://ark.cn-beijing.volces.com/api/v3"}),
        "doubao-seed-2.0-mini": (C, {"base_url": "https://ark.cn-beijing.volces.com/api/v3"}),
    })

    return registry


# 模块加载时构建注册表
PROVIDER_REGISTRY: dict[str, tuple[type[BaseProvider], dict]] = _build_registry()

# 各 provider 类需要的 API Key 环境变量名
_PROVIDER_KEY_MAP: dict[type[BaseProvider], str] = {
    DashScopeProvider: "DASHSCOPE_API_KEY",
}

# Compatible 端点 → 专用 Key 环境变量名
_COMPATIBLE_KEY_MAP: dict[str, str] = {
    "api.deepseek.com":          "DEEPSEEK_API_KEY",
    "ark.cn-beijing.volces.com": "DOUBAO_API_KEY",
}


def _get_required_key_name(
    provider_cls: type[BaseProvider],
    extra_kwargs: dict,
) -> str:
    """确定 provider 需要的 API Key 环境变量名。

    对于 Compatible 端点（有 base_url），优先使用专用 Key。
    例如 deepseek-chat → DEEPSEEK_API_KEY，fallback 通用 Key。
    """
    base_url = extra_kwargs.get("base_url", "")
    if base_url:
        from urllib.parse import urlparse
        host = urlparse(base_url).hostname or ""
        return _COMPATIBLE_KEY_MAP.get(host, "OPENAI_API_KEY")

    return _PROVIDER_KEY_MAP.get(provider_cls, "")


def get_provider(
    model_name: str,
    api_keys: dict[str, str],
) -> BaseProvider:
    """工厂函数：根据模型名称和 API Key 字典创建对应的 Provider 实例。

    Args:
        model_name: 模型名称，如 "qwen-plus", "deepseek-chat", "doubao-1.5-pro-32k"
        api_keys: {"DASHSCOPE_API_KEY": "...", "DEEPSEEK_API_KEY": "...", ...}

    Returns:
        BaseProvider 子类实例

    Raises:
        ValueError: 模型名不在注册表中
        RuntimeError: 缺少必需 API Key
    """
    entry = PROVIDER_REGISTRY.get(model_name)
    if entry is None:
        available = list(PROVIDER_REGISTRY.keys())
        raise ValueError(
            f"未知模型: {model_name}。可用模型: {available}"
        )

    provider_cls, extra_kwargs = entry
    key_name = _get_required_key_name(provider_cls, extra_kwargs)

    api_key = api_keys.get(key_name, "")

    # Compatible 端点：专用 Key 没配时，尝试 OPENAI_API_KEY 作为通用 fallback
    # 注意：DASHSCOPE_API_KEY 仅适用于阿里云 DashScope，不参与 Compatible 端点的 fallback
    if not api_key and key_name not in ("DASHSCOPE_API_KEY", ""):
        api_key = api_keys.get("OPENAI_API_KEY", "")

    if not api_key:
        key_hint = f"请设置 {key_name} 环境变量"
        if key_name not in ("DASHSCOPE_API_KEY", ""):
            key_hint += f"（或设置 OPENAI_API_KEY 作为通用 Key）"
        raise RuntimeError(
            f"模型 {model_name} 缺少 API Key。{key_hint}"
        )

    return provider_cls(model=model_name, api_key=api_key, **extra_kwargs)
