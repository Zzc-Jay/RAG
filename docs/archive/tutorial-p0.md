# P0 加固教学文档 — 从 Demo 到可部署项目

> 本文档解释 P0 加固阶段每个模块的**设计原理**和**面试要点**。
> 读完你会理解：为什么 Demo 不能上线、每个加固手段解决了什么问题、以及怎么在面试中讲清楚。

---

## 目录

1. [为什么 V2 还不够](#1-为什么-v2-还不够)
2. [API 重试机制 (retry.py)](#2-api-重试机制-retrypy)
3. [安全校验 (security.py)](#3-安全校验-securitypy)
4. [日志系统 (logging_config.py)](#4-日志系统-logging_configpy)
5. [Docker 容器化](#5-docker-容器化)
6. [Mock 测试](#6-mock-测试)
7. [面试速查：每个模块你该怎么讲](#7-面试速查)

---

## 1. 为什么 V2 还不够

V2 是一个**功能完整**的系统，但它不是**工程完整**的系统。两者的区别：

| 维度 | Demo (V2) | 可部署项目 (V2+P0) |
|------|----------|-------------------|
| **API 调用失败** | 直接崩溃，traceback 甩用户脸上 | 自动重试 3 次，失败后友好提示 |
| **恶意输入** | `<script>` 直接执行 | HTML 实体转义，安全渲染 |
| **高频调用** | 无限制，API 配额几分钟烧光 | 每分钟 20 次上限 |
| **问题排查** | print 输出，重启后丢失 | 日志文件持久化，按天轮转 |
| **环境复现** | 手配 Python 环境 | `docker-compose up` 一键启动 |
| **测试** | 需要真实 API key 和网络 | Mock 后断网也能全过 |

**核心认知**：V2 向面试官证明你「能做出来」，P0 加固向面试官证明你「知道怎么做稳」。

---

## 2. API 重试机制 (retry.py)

### 2.1 问题

网络是不稳定的。DashScope API 可能因为以下原因临时不可用：

- **网络抖动**：你的机器到阿里云之间的网络偶尔丢包
- **API 限流 (429)**：短时间发太多请求，被暂时限流
- **服务端故障 (5xx)**：阿里云那边临时过载，过几秒就好

如果一次失败就崩溃，用户体验极差。用户不知道是「网络问题过几秒就好」还是「代码写错了」，只看到一个 traceback。

### 2.2 解决方案：指数退避重试

```
第 1 次尝试: 调用 API → 失败 (ConnectionError)
  等待 1 秒
第 2 次尝试: 调用 API → 失败 (502 Bad Gateway)
  等待 2 秒
第 3 次尝试: 调用 API → 失败 (503 Service Unavailable)
  等待 4 秒
第 4 次尝试: 调用 API → 成功 ✓
```

每次失败后等待时间翻倍：1s → 2s → 4s，上限 10s。

**为什么要指数退避而不是等固定时间？**
- 如果服务端正在恢复中，等待越久恢复概率越高
- 指数增长避免在前几次快速消耗重试配额
- 是行业标准做法（Google、AWS SDK 都用这个策略）

### 2.3 可恢复 vs 不可恢复错误

```
可恢复（重试有意义）              不可恢复（重试没意义）
─────────────────────          ────────────────────────
ConnectionError (网络断开)       ValueError (参数类型错误)
TimeoutError (超时)              4xx (你的请求本身有问题)
429 Rate Limit (被限流)          401 Unauthorized (API key 错了)
5xx Server Error (服务端故障)    403 Forbidden (无权限)
```

面试中常被追问："你怎么区分 4xx 和 5xx？"
→ 4xx = 客户端的问题，重试也不会变好。5xx = 服务端的问题，可能下一次就好了。

### 2.4 代码关键点

```python
# retry.py 核心逻辑
def retry_call(func, *args, max_retries=3, base_delay=1.0, **kwargs):
    for attempt in range(max_retries + 1):
        try:
            result = func(*args, **kwargs)
            # 即使不抛异常，也检查返回值的状态码
            if hasattr(result, "status_code") and result.status_code >= 500:
                raise RuntimeError(f"API 返回 {result.status_code}")
            return result
        except Exception as e:
            if attempt >= max_retries:
                raise RuntimeError(f"已重试 {max_retries} 次，最后错误: {e}")
            if not _is_retryable(e):
                raise  # 不可恢复，直接抛
            delay = min(base_delay * (2 ** attempt), 10.0)
            time.sleep(delay)
```

### 2.5 面试常见追问

**Q: 为什么不加随机抖动 (jitter)？**
A: Jitter 是为了防止「惊群效应」——当大量客户端同时重试时，如果没有随机抖动，它们会在同一时刻再次冲击服务器。本项目是单用户本地部署，不存在这个问题。但我会在代码注释里说明生产环境需要加 ±25% 随机抖动。

**Q: DashScope SDK 本身有重试吗？**
A: DashScope SDK 没有内置重试。就算有，自己实现一层仍然有价值：可以对不同的 API 调用（embedding 和 generation）设置不同的重试策略，而且面试时能讲清楚原理。

---

## 3. 安全校验 (security.py)

### 3.1 XSS 防护

**问题**：用户输入 `<script>alert(1)</script>`，如果 Streamlit 直接渲染 HTML，这个脚本会执行。

**解决**：用 Python 内置的 `html.escape()` 将特殊字符转为 HTML 实体：

```
< → &lt;
> → &gt;
" → &quot;
& → &amp;
```

用户看到的就是纯文本 `<script>alert(1)</script>`，浏览器不会执行它。

**为什么不用 bleach 或其它第三方库？**
- Streamlit 本身对 `st.markdown` 有保护，但多一层防护更安全（纵深防御原则）
- `html.escape` 是标准库，零依赖
- 面试时展示「知道何时用标准库而非引入依赖」的判断力

### 3.2 速率限制 (Rate Limiting)

**问题**：没有速率限制的话，有人（或脚本）可以 1 分钟发 1000 个请求，把你的 API 配额烧光。

**解决方案**：基于内存的滑动窗口算法

```python
class RateLimiter:
    def __init__(self, max_requests=20, window_seconds=60):
        self._store = {}  # session_id → [timestamp, timestamp, ...]

    def check(self, session_id):
        now = time.monotonic()
        # 清理 60 秒前的旧记录
        active = [t for t in self._store.get(session_id, []) if t > now - 60]
        if len(active) >= 20:
            return False
        active.append(now)
        self._store[session_id] = active
        return True
```

**时间复杂度**：O(n) 其中 n = 窗口内请求数（最多 20），实际可忽略。

**面试追问**：
- Q: 为什么不用 Redis？
- A: Streamlit 是有状态单机服务，内存足够。分布式部署时才需要 Redis 做跨实例共享。当前阶段适合「最小可行方案」，过度设计会降低代码的可维护性。

### 3.3 文件校验

- **后缀白名单**：只允许 .pdf/.txt/.md/.docx，拒绝 .exe/.py/.sh 等
- **为什么不做 magic number 校验**：当前 Streamlit file_uploader 已经限制了类型，加上后缀过滤已足够。magic number 校验留到有实际需求时再做（YAGNI 原则）

---

## 4. 日志系统 (logging_config.py)

### 4.1 为什么不用 print()

| print() | logging |
|---------|---------|
| 只能输出到控制台 | 同时输出到控制台 + 文件 |
| 无时间戳 | 每行自动带时间戳 |
| 无级别区分 | DEBUG/INFO/WARNING/ERROR 可开关 |
| 重启后消失 | 文件持久化，可回溯 |
| 第三方库的日志无法控制 | 可以设不同 logger 的级别 |

### 4.2 设计要点

**两个 Handler**：
1. `StreamHandler → sys.stdout`：开发时实时看
2. `TimedRotatingFileHandler → data/logs/app.log`：按天轮转，保留 7 天

**日志格式**：`2026-05-23 14:30:01 [INFO ] rag.embedder: 嵌入批次 1/3 完成 (20 条，耗时 2.34s)`

**第三库噪音控制**：`chromadb`、`urllib3` 等库的 DEBUG 日志设为 WARNING，避免刷屏。

**命名空间**：所有日志用 `rag.` 前缀，方便以后加 ELK/Splunk 等日志收集时过滤。

### 4.3 面试追问

**Q: 为什么不用 loguru？**
A: loguru 确实更好用，但 logging 是 Python 标准库。选标准库的原因：零依赖、面试官一定熟悉（不会因为不熟 loguru 而产生隔阂）、而且 logging 的复杂度（handler、formatter、logger 层级）恰好是值得展示的知识点。

---

## 5. Docker 容器化

### 5.1 Dockerfile 设计

```
FROM python:3.12-slim          # 选 slim 而不是 alpine（PyMuPDF 需要 glibc）
RUN apt-get install libmupdf-dev  # PyMuPDF 的系统依赖
RUN useradd appuser             # 创建非 root 用户
COPY requirements.txt .         # 先复制依赖文件（利用 Docker 缓存层）
RUN pip install -r requirements.txt
COPY src/ ./src/                # 最后复制代码（代码常变，依赖不常变）
USER appuser                    # 非 root 运行
```

**关键优化——利用 Docker 层缓存**：
- 如果只改了代码没改依赖，重新 build 时 `pip install` 那层不会重新执行
- 这就是为什么先 COPY requirements.txt 再 COPY src/

**为什么用非 root 用户**：
- 如果容器被打穿了（虽然概率很低），攻击者拿到的不是 root 权限

### 5.2 docker-compose.yml 设计

```yaml
services:
  app:
    ports:
      - "8501:8501"
    volumes:
      - ./data:/app/data   # 数据持久化到宿主机
    env_file:
      - .env               # API key 不进镜像
```

**数据持久化**：ChromaDB 和 BM25 索引存在 `./data/` 目录，通过 volume 挂载。删除容器后数据不丢失。

**API key 安全**：通过 `.env` 文件注入，不进镜像层。即使镜像被分享，API key 也不会泄露。

---

## 6. Mock 测试

### 6.1 为什么需要 Mock

V2 的测试有一个致命问题：**依赖真实的 DashScope API**。这意味着：

- 没有网络就跑不了测试
- CI/CD 环境没有 API key 就跑不了测试
- API 调用慢（每个 embedding 请求 1-2 秒），测试跑得慢

Mock 的核心思想：**用假的代替真的**。用 `unittest.mock` 替换 DashScope 的网络调用，返回预设的数据。

### 6.2 Mock 的三种用法

**用法 1：patch 装饰器替换函数返回值**

```python
with patch("embedder.TextEmbedding.call") as mock_call:
    mock_call.return_value = FakeResponse(...)  # 预设返回值
    result = my_function()  # 函数内部调用 TextEmbedding.call 时使用 mock
```

**用法 2：side_effect 模拟多次调用**

```python
mock_call.side_effect = [ConnectionError, "success"]
# 第一次调用抛出异常，第二次返回 "success"
# 用于测试重试逻辑
```

**用法 3：验证调用参数**

```python
mock_vec.assert_called_once()  # 验证恰好被调用一次
mock_vec.assert_called_with(model="text-embedding-v3", ...)  # 验证参数
```

### 6.3 Fake 对象模式

```python
class FakeEmbeddingResponse:
    """模拟 DashScope API 的返回对象。"""
    def __init__(self, embeddings, status_code=200, message=""):
        self.status_code = status_code
        self.message = message
        self.output = FakeEmbeddingOutput(embeddings)

class FakeEmbeddingOutput:
    """模拟 resp.output['embeddings'] 的嵌套结构。"""
    def __init__(self, embeddings_list):
        self._embeddings = {"embeddings": [{"embedding": e} for e in embeddings_list]}
    def __getitem__(self, key):
        return self._embeddings[key]
```

这种模式比 MagicMock 更可控：你明确知道每个属性返回什么，类型检查也能通过。

### 6.4 测试覆盖统计

| 模块 | V2 测试数 | P0 新增 | 合计 |
|------|---------|--------|------|
| loader | 7 | 0 | 7 |
| chunker | 2 | 0 | 2 |
| BM25 | 2 | 0 | 2 |
| RRF | 2 | 2 | 4 |
| kb_manager | 3 | 0 | 3 |
| generator | 1 | 3 | 4 |
| security | 0 | 14 | 14 |
| retry | 0 | 11 | 11 |
| embedder (mock) | 0 | 3 | 3 |
| retriever (mock) | 0 | 3 | 3 |
| **合计** | **18** | **36** | **54** |

---

## 7. 面试速查

面试时被问到每个模块，按这个结构回答：

### retry.py
> "我对所有 DashScope API 调用做了指数退避重试。区分了可恢复错误（网络、5xx、429，会自动重试）和不可恢复错误（4xx 参数错误，立即返回）。没有加 jitter 是因为当前单用户场景不需要，但我知道生产环境需要 ±25% 随机抖动防惊群效应。"

### security.py
> "做了三层防护：输入校验（长度限制 + HTML 实体转义防 XSS）、速率限制（滑动窗口算法，60 秒最多 20 次）、文件类型白名单。速率限制用内存字典实现，因为 Streamlit 是单机服务，不需要 Redis。如果上分布式，替换成 Redis sorted set 也很简单。"

### logging_config.py
> "替换了项目中的 print，用 Python 标准 logging 模块。配置了两个 handler：控制台实时看 + 文件按天轮转保留 7 天。第三方库的 DEBUG 日志被抑制到 WARNING。不用 loguru 是因为零依赖原则，而且 logging 的复杂度恰好是值得展示的知识点。"

### Docker
> "Dockerfile 基于 python:3.12-slim，关键优化是先 COPY requirements.txt 后 COPY src，利用 Docker 层缓存加速构建。非 root 用户运行，数据通过 docker-compose volume 持久化到宿主机，API key 通过 .env 注入不进镜像。"

### Mock 测试
> "54 个测试在断网环境下全部可跑。API 调用用 unittest.mock 做了 fake 对象替换，重试逻辑用 side_effect 模拟失败和恢复。测试设计遵循了 '测试应该快、独立、可重复' 的原则。"
