from __future__ import annotations

import logging
import logging.handlers
import os
import sys


def setup_logging(level: str = "INFO") -> None:
    """初始化全项目统一的日志配置。

    两个输出目标:
    1. 控制台 (StreamHandler) — 开发时实时查看
    2. 文件 (TimedRotatingFileHandler) — 持久化留存，按天轮转，保留 7 天

    level: 日志级别字符串，可通过环境变量 LOG_LEVEL 覆盖，默认 INFO
    """
    log_level = os.getenv("LOG_LEVEL", level).upper()
    numeric_level = getattr(logging, log_level, logging.INFO)

    # 创建根 logger，所有 rag.* logger 向上传播
    root = logging.getLogger("rag")
    root.setLevel(numeric_level)
    root.propagate = False  # 不污染 root logger

    # 避免重复添加 handler（Streamlit 会多次 reload）
    if root.handlers:
        return

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(numeric_level)
    console.setFormatter(fmt)
    root.addHandler(console)

    # 文件（按天轮转，保留 7 天）
    log_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "logs",
    )
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "app.log")

    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_file,
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # 抑制第三方库的 DEBUG 日志，减少噪音
    for noisy in ("chromadb", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """获取带 rag 命名空间的 logger。

    name: 模块简称，如 "embedder"、"generator"
    返回: logging.getLogger("rag.embedder")
    """
    return logging.getLogger(f"rag.{name}")
