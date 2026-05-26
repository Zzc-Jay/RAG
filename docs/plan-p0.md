# Plan: P0 加固实施方案

## 设计原则

1. **无新依赖** — 能用标准库就不用第三方库，降低学习和维护成本
2. **最小侵入** — 不重构现有模块，只在调用链上增加防护层
3. **可讲原理** — 每个设计决策都有 WHY，面试时能讲清楚
4. **测试先行** — mock 测试先写，明确行为预期后再改代码

---

## 模块设计

### 1. `src/retry.py` — 通用重试函数

```
职责：对可恢复的 API 错误自动重试（指数退避）

API:
  retry_call(func, *args, max_retries=3, base_delay=1.0, **kwargs) → 返回值
    成功 → 返回 func 的返回值
    不可恢复错误（4xx） → 立即抛异常
    可恢复错误（429/5xx/网络超时） → 等待 base_delay * 2^attempt 后重试
    重试耗尽 → 抛出 RuntimeError("DashScope API 调用失败，已重试 {n} 次")

可恢复异常类型:
  - dashscope.api_base.APIError (5xx, 429)
  - ConnectionError, TimeoutError
  - requests.exceptions.Timeout, requests.exceptions.ConnectionError

不可恢复异常:
  - 4xx 状态码（参数错误、认证失败——重试无意义）
  - ValueError / TypeError（代码 bug，重试不能修复）

日志:
  - WARNING: 每次重试（含当前 attempt 和等待时间）
  - ERROR: 重试耗尽
```

**为什么不用 tenacity 库**：
- 面试时可以展示自己实现指数退避的能力
- 减少外部依赖
- 代码量不超过 40 行，维护成本低

### 2. `src/security.py` — 安全校验

```
职责：输入校验 + 速率限制

API:
  validate_question(text: str) -> str
    1. 检查长度 <= 2000，超限抛出 ValueError("问题长度不能超过 2000 字符")
    2. HTML 实体转义（内置 html.escape）
    3. 返回净化后的文本

  validate_kb_name(name: str) -> str
    1. 检查长度 1-50
    2. 正则匹配：^[a-zA-Z0-9一-龥_-]+$
    3. 不合法抛出 ValueError，附带说明允许的字符

  validate_file(file_obj) -> None
    1. 检查文件名后缀白名单：.pdf/.txt/.docx
    2. 检查文件大小 <= 50MB（通过 Streamlit 的 file_uploader 限制）
    3. MIME type magic number 校验（should 级别，用 filetype 库或内置）

  RateLimiter 类:
    __init__(max_requests=20, window_seconds=60)
    check(session_id: str) -> bool
      1. 在内存字典中记录 session_id 的请求时间戳
      2. 窗口外的旧记录自动清理
      3. 超过限制返回 False，否则返回 True
    remaining(session_id: str) -> int
      返回剩余可用次数
```

**为什么不用 Redis 做速率限制**：
- Streamlit 本身是有状态服务，内存字典足够
- 面试时讲清楚"单机内存 vs 分布式 Redis"的选型依据更有价值

### 3. `src/logging_config.py` — 日志配置

```
职责：全项目统一日志格式

API:
  setup_logging(level="INFO") -> None
    1. 配置 root logger 或 "rag" namespace
    2. 格式: "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    3. Handler:
       - StreamHandler → sys.stdout
       - RotatingFileHandler → data/logs/app.log（按天轮转，保留 7 天）
    4. 抑制第三方库的 DEBUG 日志（chromadb, urllib3 等设为 WARNING）

  get_logger(name: str) -> logging.Logger
    便捷函数，返回 logging.getLogger(f"rag.{name}")
```

**为什么不用 loguru**：
- 标准库 logging 足够用
- 面试时展示对 Python 标准库的熟悉程度
- 减少依赖

### 4. Dockerfile

```
基于 python:3.12-slim 多阶段构建（可选，slim 已足够小）

阶段:
  1. 安装系统依赖（仅 PyMuPDF 需要的 libmupdf-dev）
  2. pip install -r requirements.txt
  3. 创建非 root 用户
  4. 暴露 8501

优化点:
  - .dockerignore 排除不必要文件
  - pip --no-cache-dir 减小镜像体积
  - USER 非 root
```

### 5. 测试策略

```
新增测试（tests/test_rag.py 补充）:

embedder mock 测试:
  test_embed_batch_calls_api  — mock TextEmbedding.call，验证返回值结构
  test_embed_batch_splits_large_input  — 25+ 条输入是否正确分批
  test_search_returns_list_of_dict  — mock collection.query，验证返回格式

generator mock 测试:
  test_generate_stream_yields_tokens  — mock Generation.call，模拟 SSE chunk
  test_generate_stream_handles_empty  — 空 docs 时的行为

retriever mock 测试:
  test_retrieve_fuses_both_sources  — mock embedder + bm25，验证融合调用
  test_retrieve_dedup_same_content  — 两路返回相同文档时去重

security 测试:
  test_validate_question_too_long  — 超长输入被拒绝
  test_validate_question_xss_escaped  — <script> 被转义
  test_validate_kb_name_rejects_special  — 特殊字符知识库名被拒绝
  test_rate_limiter_blocks_after_limit  — 超过限制后返回 False
  test_rate_limiter_resets_after_window  — 窗口过后恢复

retry 测试:
  test_retry_success_first_attempt  — 第一次成功不重试
  test_retry_recovers_on_second  — 第一次失败第二次成功
  test_retry_exhausted_raises  — 全部失败后抛出异常
  test_retry_no_retry_on_4xx  — 4xx 错误不重试
```

---

## 实现顺序（依赖拓扑）

```
阶段 1（基础设施，无依赖）
  [1.1] requirements.txt 版本锁定
  [1.2] src/logging_config.py — 日志系统
  [1.3] src/security.py      — 安全校验

阶段 2（核心防护，依赖阶段 1）
  [2.1] src/retry.py         — 重试机制（依赖 logging_config）
  [2.2] src/embedder.py 改造 — 集成重试 + 日志
  [2.3] src/generator.py 改造 — 集成重试 + 日志

阶段 3（UI 防护，依赖阶段 1）
  [3.1] src/app.py 改造      — 集成安全校验 + 速率限制 + 日志

阶段 4（部署，依赖阶段 2+3）
  [4.1] Dockerfile
  [4.2] docker-compose.yml
  [4.3] .dockerignore

阶段 5（验证，依赖阶段 2+3）
  [5.1] 补充 mock 测试
  [5.2] 补充安全模块测试
  [5.3] 补充重试模块测试
  [5.4] 全量测试 + Docker 验证
```

---

## 关键设计决策

### 为什么重试只做指数退避，不做 jitter？

标准做法是加随机抖动避免惊群效应（thundering herd）。但本项目是单用户本地部署，不存在大量并发请求同时重试的场景，简单指数退避足够。

面试时说："当前场景是单用户本地部署，不需要 jitter。生产环境高并发下会加 ±25% 随机抖动。"

### 为什么速率限制不做 IP 级别？

Streamlit 应用通常单机运行，session_id 级别的限制已经能防止单用户滥用。IP 级别需要引入反向代理（nginx）或中间件，对本地 Demo 过度设计。

### 为什么安全校验只在 app.py 做，不在每个模块做？

安全边界在系统入口（用户输入 → 系统），不在模块间调用。在入口做一次校验即可，内部模块信任调用方传入的数据。这是"纵深防御"和"避免重复校验"的平衡。

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| DashScope SDK 不暴露明确的异常类型 | 用 try-except Exception 兜底，根据异常消息字符串判断是否为可恢复错误 |
| ChromaDB 在 Docker 中文件权限问题 | docker-compose 挂载 volume，USER 指令与宿主机 UID 对齐 |
| Streamlit 在 Docker 中 WebSocket 连接问题 | 映射 8501 端口，streamlit 启动命令指定 `--server.address 0.0.0.0` |
| mock 测试覆盖不全导致 CI 误通过 | mock 时验证 call args，确保调用参数符合预期 |
```

---

## 验证检查点

- [1.x] `python -c "from logging_config import setup_logging; setup_logging()"` 日志文件生成
- [1.x] `python -c "from security import validate_question; validate_question('<script>')"` 返回转义后文本
- [2.x] 断网后运行 app.py，提问时显示友好错误信息而非 traceback
- [2.x] 故意传错 API key，观察重试日志输出（WARNING 级别 3 次 + 最终 ERROR）
- [4.x] `docker-compose up` 浏览器能访问
- [5.x] `pytest tests/ -v` 断网全部通过
