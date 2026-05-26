from __future__ import annotations
import os

# --- API keys ---
DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
DOUBAO_API_KEY: str = os.getenv("DOUBAO_API_KEY", "")


def get_api_keys() -> dict[str, str]:
    """返回所有 provider API Key 的字典，供 get_provider() 工厂使用。"""
    return {
        "DASHSCOPE_API_KEY": DASHSCOPE_API_KEY,
        "DEEPSEEK_API_KEY": DEEPSEEK_API_KEY,
        "DOUBAO_API_KEY": DOUBAO_API_KEY,
    }

# --- Paths ---
BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR: str = os.getenv("RAG_DATA_DIR", os.path.join(BASE_DIR, "data"))
CHROMA_DIR: str = os.path.join(DATA_DIR, "chroma_db")
BM25_DIR: str = os.path.join(DATA_DIR, "bm25")
REGISTRY_PATH: str = os.path.join(DATA_DIR, "kb_registry.json")

# --- Chunking ---
CHUNK_SIZE: int = 800
CHUNK_OVERLAP: int = 150

# --- Embedding ---
BATCH_SIZE: int = 10  # DashScope TextEmbedding 上限为 10 条/批
EMBEDDING_MODEL: str = "text-embedding-v3"

# --- Retrieval ---
TOP_K: int = 5
RRF_K: int = 60

# --- URL loading ---
URL_TIMEOUT: int = 15
USER_AGENT: str = "RAG-KB-Bot/2.0"

# --- Generation ---
LLM_MODEL: str = os.getenv("LLM_MODEL", "qwen-plus")

# --- Model pricing (per 1K tokens) ---
# 格式: {"gen_input": float, "gen_output": float}
# embedding 价格固定为 DashScope text-embedding-v3: ¥0.0005/1K
MODEL_PRICING: dict[str, dict[str, float]] = {
    # DashScope (Alibaba Qwen) — ¥ / 1K tokens
    "qwen-plus":   {"gen_input": 0.0008, "gen_output": 0.002},
    "qwen3-max":   {"gen_input": 0.002,  "gen_output": 0.006},
    # DeepSeek V4 (2026.04) — ¥ / 1K tokens
    # 官方: Flash ¥1/M in, ¥2/M out; Pro ¥3/M in, ¥6/M out
    "deepseek-v4-flash": {"gen_input": 0.001, "gen_output": 0.002},
    "deepseek-v4-pro":   {"gen_input": 0.003, "gen_output": 0.006},
    # 豆包 Seed 2.0 (2026.02) — ¥ / 1K tokens
    # USD→RMB 按 1:7.25 换算
    "doubao-seed-2.0-pro":  {"gen_input": 0.0037, "gen_output": 0.0186},
    "doubao-seed-2.0-lite": {"gen_input": 0.00064,"gen_output": 0.0038},
    "doubao-seed-2.0-mini": {"gen_input": 0.00021,"gen_output": 0.0021},
}

# --- Model display names (for UI) ---
MODEL_LABELS: dict[str, str] = {
    "qwen-plus":    "Qwen Plus (通义千问)",
    "qwen3-max":    "Qwen3 Max (通义千问)",
    "deepseek-v4-flash": "DeepSeek V4 Flash",
    "deepseek-v4-pro":   "DeepSeek V4 Pro",
    "doubao-seed-2.0-pro":  "豆包 Seed 2.0 Pro (字节)",
    "doubao-seed-2.0-lite": "豆包 Seed 2.0 Lite (字节)",
    "doubao-seed-2.0-mini": "豆包 Seed 2.0 Mini (字节)",
}

# --- Retry (API 容错) ---
MAX_RETRIES: int = 3
RETRY_BASE_DELAY: float = 1.0  # 首次重试等待秒数，后续指数翻倍
RETRY_MAX_DELAY: float = 10.0  # 单次等待上限

# --- Security ---
MAX_QUERY_LENGTH: int = 2000
MAX_KB_NAME_LENGTH: int = 50
MAX_FILE_SIZE_MB: int = 50
RATE_LIMIT_REQUESTS: int = 20
RATE_LIMIT_WINDOW: int = 60  # 秒

# --- Conversation ---
MAX_HISTORY_TURNS: int = 5
MAX_HISTORY_TOKENS: int = 2000  # 对话历史最大 token 数，超出则截断

# --- Token tracking ---
PRICE_EMBEDDING: float = 0.0005     # per 1K tokens
PRICE_GEN_INPUT: float = 0.0008     # per 1K tokens
PRICE_GEN_OUTPUT: float = 0.002     # per 1K tokens
