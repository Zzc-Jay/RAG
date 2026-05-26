# 模块详解

本文逐一讲解项目中 17 个 Python 模块的设计思路、关键代码和教学点。

---

## 1. `config.py` — 配置集中管理

**职责**：所有可配置参数集中定义，通过环境变量覆盖。

**关键设计**：

```python
# 环境变量覆盖默认值
DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
DATA_DIR: str = os.getenv("RAG_DATA_DIR", os.path.join(BASE_DIR, "data"))
LLM_MODEL: str = os.getenv("LLM_MODEL", "qwen-plus")
```

**教学点**：
- **配置与代码分离**：参数变更不需要改代码，只需设置环境变量。Docker 部署时通过 `env_file` 或 `environment` 注入
- **模块级常量 vs 函数**：频繁不变的配置用模块级常量（如 `CHUNK_SIZE=800`），需要动态计算的用函数（如 `get_user_data_dir(user_id)`）
- **MODEL_PRICING**：定义了每个模型的输入/输出 token 单价，用于成本估算

---

## 2. `loader.py` — 多格式文档加载

**职责**：统一接口加载 PDF/DOCX/TXT/MD/URL，输出标准化的页面字典列表。

**支持的格式和工具**：

| 格式 | 解析库 | 特殊处理 |
|------|--------|----------|
| PDF | PyMuPDF (fitz) | 表格提取、扫描件检测、多列布局 |
| DOCX | python-docx | 段落 + 表格分别提取 |
| TXT/MD | 标准库 | 编码检测（UTF-8/GBK 自动切换） |
| URL | requests + BeautifulSoup | 提取正文文本、去除导航/广告 |

**输出格式**（所有格式统一）：
```python
[
    {
        "page": 1,            # 页码
        "text": "页面文本...", # 纯文本内容
        "table_count": 2,     # 本页表格数
        "is_scanned": False,  # 是否为扫描件
    },
    ...
]
```

**教学点**：
- **接口统一**：外部使用方不关心是什么格式——`load_document(path, type)` 永远返回同一种数据结构
- **表格提取**：PDF 通过 `page.find_tables()` 提取，DOCX 通过 `doc.tables` 提取。表格文本嵌入到 chunk 中，同时标记 `has_table=True`
- **扫描件检测**：PyMuPDF 可以获取页面的图片数量。如果文本极少但图片很多，标记为扫描件（提示用户做 OCR 预处理）

---

## 3. `chunker.py` — 文本切分

**职责**：将长文档切分成适合嵌入的文本块。

**核心逻辑**：

```python
CHUNK_SIZE = 800    # 每个 chunk 最多 800 字符
CHUNK_OVERLAP = 150 # 相邻 chunk 重叠 150 字符
```

使用 LangChain 的 `RecursiveCharacterTextSplitter`：
- 按自然分隔符优先级切分：`\n\n` → `\n` → `。` → `，` → 单字符
- 保证 chunk 边界尽量在语义单元之间，而非硬截断在句子中间

**教学点**：
- **chunk_size 为什么是 800**：中文 800 字符 ≈ 400 tokens，Embedding 模型推荐输入范围是 ≤ 512 tokens
- **overlap 的作用**：假设关键信息恰好在第 800 个字符处，没有 overlap 它会被切成两个 chunk 的开头和结尾，语义完整性被破坏。150 字符重叠确保关键信息至少在一个完整 chunk 中出现
- **元数据透传**：切分后的 chunk 保留原始文档的 `has_table`、`is_scanned` 标记，供检索时展示

---

## 4. `embedder.py` — 向量嵌入与检索

**职责**：将文本转为向量、存入 ChromaDB、执行向量相似度检索。

**核心流程**：

```
文本列表 → 嵌入缓存查询 → 缓存未命中的调API → 写入缓存 → upsert到ChromaDB
```

**分批嵌入**（`_batch_embed`）：
```python
BATCH_SIZE = 10  # DashScope API 上限：最多 10 条/次

for bi in range(0, len(miss_texts), BATCH_SIZE):
    batch = miss_texts[bi:bi + BATCH_SIZE]
    embeddings, tokens = _call_embedding_api(batch)
    # 写入缓存
    store(batch, embeddings)
```

**教学点**：
- **为什么分批**：DashScope TextEmbedding API 一次最多接受 10 条文本，不是系统限制，是 API 限制。每条文本独立嵌入，批量调用只是为了减少网络往返
- **ChromaDB 路径隔离**：`_get_collection(kb_name)` 内部读取用户上下文，ChromaDB 数据落到 `data/users/{user_id}/chroma_db/{kb_name}/`，实现多租户物理隔离
- **upsert 而非 insert**：`collection.upsert()` 会覆盖相同 ID 的旧数据。这样重复上传同一文件时不会产生重复向量
- **删除 → 重建 BM25**：每次 `add_to_kb()` 或 `delete_doc_chunks()` 后重建 BM25 索引，保证关键词检索和向量检索的数据一致性

---

## 5. `embedding_cache.py` — 嵌入缓存

**职责**：基于文本内容的嵌入向量缓存，避免重复调用 Embedding API。

**核心思想**：**内容寻址存储（Content-Addressable Storage）**

```
文本 "Python是..."  →  SHA256 →  "a3f8c2..."  →  查 SQLite
                                    ↓
                          命中 → 直接返回 JSON 中的向量
                          未命中 → 调 API → 写入 SQLite
```

**缓存键设计**：
- 对文本计算 SHA256 → 相同文本 = 相同哈希 = 相同嵌入向量
- **跨知识库共享**：同一段文字上传到 A 库和 B 库，只调用一次 API
- **重启不丢失**：SQLite 持久化，不需要 Redis

**成本节约**：
- DashScope text-embedding-v3: ¥0.0005/1K tokens
- 以 800 字/块典型 chunk ≈ 400 tokens = ¥0.0002/块
- 缓存 1000 条节约 ¥0.2；10000 条节约 ¥2

**教学点**：
- **为什么缓存是全局共享的**：不同于知识库数据，嵌入向量是纯内容派生的（SHA256 确定相同文本），不包含任何用户信息，全局共享不会泄漏隐私
- **命中率监控**：`cache_hit_rate()` 返回 0.0~1.0，帮助判断缓存效果

---

## 6. `bm25_index.py` — 关键词检索

**职责**：基于 BM25 算法的关键词检索，与向量检索互补。

**实现细节**：

```python
def _tokenize(text: str) -> list[str]:
    return [w for w in jieba.cut(text) if w.strip()]

def build_index(chunks, kb_name):
    corpus = [_tokenize(c["text"]) for c in chunks]
    model = BM25Okapi(corpus)
    pickle.dump({"corpus": corpus, "model": model, "docs": chunks}, f)
```

**教学点**：
- **为什么用 jieba 分词**：BM25 需要词袋输入。英文天然空格分词，中文需要专门的分词工具。jieba 精确模式逐词切分："检索增强生成" → ["检索", "增强", "生成"]
- **为什么用 pickle 持久化**：BM25 基于统计（文档频率、平均长度），构建后不变。pickle 序列化整个模型对象，下次加载直接反序列化，无需重建
- **BM25 vs TF-IDF**：见 `docs/INTERVIEW.md` Q5
- **向量 + 关键词 = 互补**：向量擅长找"意思相近的"，BM25 擅长找"关键词精确匹配的"。比如搜"API_VERSION"，embedding 可能找不到，BM25 直接命中

---

## 7. `retriever.py` — 混合检索与 RRF 融合

**职责**：协调向量检索和 BM25 检索，用 RRF 算法融合排序。

**核心流程**：

```
query → ① (可选)查询改写 → ② 向量检索(Top-K×fetch_factor)
       → ③ BM25检索(Top-K×fetch_factor)
       → ④ RRF融合排序 → ⑤ (可选)LLM精排 → Top-K结果
```

**RRF 公式**：

```
score(d) = w_vector/(K + rank_vector) + w_bm25/(K + rank_bm25)
```

**可配置参数**：
- `top_k`：最终返回的文档片段数（默认 5）
- `rrf_k`：RRF 平滑参数（默认 60），控制排名衰减速度
- `vector_weight`：向量检索权重（默认 1.0）
- `bm25_weight`：BM25 检索权重（默认 1.0）

**检索偏好预设**：
| 偏好 | vector_weight | bm25_weight | 适用场景 |
|------|---------------|-------------|----------|
| 纯语义 | 1.0 | 0.0 | 自然语言问题 |
| 偏向语义 | 1.0 | 0.5 | 模糊查询 |
| 均衡 | 1.0 | 1.0 | 通用 |
| 偏向关键词 | 0.5 | 1.0 | 技术文档查询 |
| 纯关键词 | 0.0 | 1.0 | 代码/变量名搜索 |

**教学点**：
- **Rerank 时扩大 fetch_k**：如果启用了 LLM 精排，先从两路各取 top_k × 2 个候选，再送给 LLM 打分筛到 top_k。因为精排只能从候选池中选，候选池太小会漏掉好结果
- **RRF 详解**：见 `docs/RETRIEVAL.md`

---

## 8. `generator.py` — LLM 生成

**职责**：构建 Prompt、调用 LLM 生成回答。

**Prompt 模板**：

```
你是一个基于知识库的问答助手。请根据以下参考资料回答用户问题。
如果参考资料中没有相关信息，请明确说明"参考资料中未找到相关信息"。

## 参考资料
[1] [来源: xxx.pdf 第3页] (分数: 0.95)
文档片段内容...

[2] [来源: yyy.txt 第1页] (分数: 0.87)
文档片段内容...

## 用户问题
什么是 RAG？

## 回答要求
1. 基于参考资料回答，引用具体来源编号
2. 如果参考资料不足以回答问题，请明确说明
```

**流式生成**（`generate_stream`）：
```python
def generate_stream(query, docs, conversation=None, model=None):
    provider = get_provider(model or LLM_MODEL)
    prompt = build_prompt(query, docs, conversation)
    for chunk in provider.generate_stream(prompt):
        yield chunk
```

**教学点**：
- **引用溯源**：在 Prompt 中要求 LLM 引用来源编号 `[1]`，回答中就能对应到具体的文档片段
- **流式输出**：生成器模式逐 token yield，SSE 推给前端。用户看到打字机效果，体验更好
- **多 provider 抽象**：`get_provider()` 根据模型名返回对应的 provider 实例，DashScope/OpenAI/豆包都实现了相同的 `generate()` 和 `generate_stream()` 接口

---

## 9. `providers.py` — 多 LLM 后端

**职责**：统一多 LLM 提供者的注册和调用接口。

**支持的模型**：

| 提供者 | 模型 | 说明 |
|--------|------|------|
| DashScope | qwen-plus, qwen3-max | 阿里云，默认选择 |
| OpenAI 兼容 | deepseek-v4-flash, deepseek-v4-pro | DeepSeek |
| 豆包 | doubao-seed-2.0-* | 字节跳动 |

**注册机制**：
```python
PROVIDER_REGISTRY = {
    "qwen-plus": (DashScopeProvider, {"model": "qwen-plus"}),
    "deepseek-v4-flash": (OpenAICompatibleProvider, {
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com/v1",
    }),
    ...
}
```

**教学点**：
- **策略模式**：所有 provider 实现相同的 `generate()` 和 `generate_stream()` 接口，调用方不关心后端是谁
- **Key 检测**：UI 自动检测哪些 provider 有可用的 API Key，只显示已配置的选项

---

## 10. `kb_manager.py` — 知识库管理

**职责**：知识库的增删改查，JSON 注册表管理，数据目录路径管理。

**注册表结构**（`data/users/{user_id}/kb_registry.json`）：
```json
{
  "技术文档": {
    "created": "2026-05-26",
    "docs": [
      {"name": "架构设计.pdf", "pages": 15, "chunks": 42, "type": ".pdf"},
      {"name": "API手册.md", "pages": 1, "chunks": 18, "type": ".md"}
    ]
  },
  "产品手册": { ... }
}
```

**教学点**：
- **用户隔离**：`_get_user_data_dir()` 内部读取 contextvar，每个用户独立目录，知识库名在不同用户间可以重复
- **批量操作优化**：`add_docs_batch()` 和 `remove_docs_batch()` 一次读写注册表，而非逐个 add_doc 导致多次 IO

---

## 11. `security.py` — 输入安全

**职责**：输入校验 + XSS 防护 + 速率限制。

**三层防护**：

```
输入 → validate_question() → HTML实体转义 → 长度限制(2000字符)
     → validate_kb_name() → 字符白名单(中英文/数字/下划线)
     → validate_file_extension() → 后缀白名单(.pdf/.txt/.md/.docx)
```

**速率限制**（`RateLimiter`）：

滑动窗口算法，默认 60 秒内最多 20 次请求：
```python
class RateLimiter:
    def check(self, session_id) -> bool:
        # 清理窗口外的旧记录
        active = [t for t in entries if t > now - window]
        if len(active) >= max_requests:
            return False  # 限流
        active.append(now)
        return True
```

**教学点**：
- **为什么用 HTML 实体转义**：`html.escape("<script>")` → `&lt;script&gt;`，即使用户输入恶意脚本，也不会被浏览器执行
- **滑动窗口 vs 固定窗口**：滑动窗口更平滑，不会在窗口边界出现突发双倍流量
- **字符白名单**：知识库名只允许 `[一-龥a-zA-Z0-9_-\s]`，拒绝路径穿越字符 `../`

---

## 12. `retry.py` — API 重试

**职责**：指数退避重试，处理临时性 API 故障。

**重试策略**：
```python
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0   # 首次等 1 秒
RETRY_MAX_DELAY = 10.0   # 单次最多等 10 秒

# 第1次重试: 等 1.0秒
# 第2次重试: 等 2.0秒
# 第3次重试: 等 4.0秒 → 达到上限 10.0 秒，最终失败
```

**教学点**：
- **为什么是指数退避而不是固定间隔**：给服务器恢复时间。如果 1 万客户端同时失败同时重试（thundering herd），服务器会被打死。指数退避 + 随机抖动可以分散重试时间
- **哪些错误应该重试**：网络超时、服务端 5xx、限流 429 → 重试。输入参数错误 4xx → 不重试（重试也没用）

---

## 13. `conversation.py` — 多轮对话

**职责**：管理对话历史，提供上下文给 LLM。

**存储结构**：
```python
conversation = [
    {
        "question": "什么是RAG？",
        "answer": "RAG是检索增强生成...",
        "references": [...],
        "feedback": None,  # 0=踩, 1=赞, None=未评价
    },
    ...
]
```

**截断策略**：最多保留最近 5 轮对话，或总 token 数不超过 2000（取较严格的）。

**教学点**：
- **为什么截断**：LLM 有上下文窗口限制，历史太长会把检索结果挤出窗口。超过 5 轮时删最旧的
- **反馈闭环**：用户点 👍/👎 记录到对话中，将来可以用于评估检索质量

---

## 14. `token_tracker.py` — Token 用量

**职责**：追踪 Embedding 和 Generation 的 token 消耗，估算费用。

**成本计算方法**：
```python
cost = (embedding_tokens * 0.0005 + gen_input * 0.0008 + gen_output * 0.002) / 1000
```

**教学点**：
- **为什么估算而不是精确**：流式生成时实际 token 数要在生成后才能精确获取。实际做法是 `len(text) * 0.4` 做粗略估算（中文约 2.5 字符/token）
- **UI 展示**：侧边栏实时显示累计 tokens 和费用，帮助用户理解 API 调用成本

---

## 15. `audit.py` — 审计日志

**职责**：Append-only 操作记录，用于追溯。

**事件类型**：
| 事件 | 含义 |
|------|------|
| `kb.create` / `kb.delete` | 知识库生命周期 |
| `doc.upload` / `doc.url` / `doc.delete` | 文档操作 |
| `doc.upload.batch` / `doc.delete.batch` | 批量操作 |
| `query` / `query.stream` | 问答操作 |

**设计原则**：
- **Append-only**：只 INSERT 不 UPDATE/DELETE，防止篡改
- **结构化 JSON**：details 字段存 JSON，兼顾查询灵活性（索引字段）和扩展性（自由字段）
- **用户自动筛选**：每个用户只能看到自己的审计日志

---

## 16. `auth.py` — 认证与多租户

**职责**：用户注册/登录、JWT 认证、用户上下文管理、数据迁移。

**详见**：`docs/AUTH.md`

---

## 17. `api.py` — REST API

**职责**：14 个 API 端点，含认证、知识库 CRUD、文档管理、问答、审计。

**端点清单**：

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/health` | 无 | 健康检查 |
| GET | `/api/stats` | 无 | 系统统计 |
| POST | `/auth/register` | 无 | 用户注册 |
| POST | `/auth/login` | 无 | 用户登录 |
| GET | `/auth/me` | 需要 | 当前用户信息 |
| GET | `/api/kb` | 需要 | 列出知识库 |
| POST | `/api/kb` | 需要 | 创建知识库 |
| DELETE | `/api/kb/{name}` | 需要 | 删除知识库 |
| GET | `/api/kb/{name}/docs` | 需要 | 列出文档 |
| POST | `/api/kb/{name}/docs/upload` | 需要 | 上传单个文件 |
| POST | `/api/kb/{name}/docs/upload/batch` | 需要 | 批量上传 |
| POST | `/api/kb/{name}/docs/url` | 需要 | 导入网页 |
| POST | `/api/kb/{name}/docs/url/batch` | 需要 | 批量导入网页 |
| DELETE | `/api/kb/{name}/docs/{doc_name}` | 需要 | 删除文档 |
| POST | `/api/kb/{name}/docs/delete/batch` | 需要 | 批量删除 |
| POST | `/api/kb/{name}/query` | 需要 | 同步问答 |
| GET | `/api/kb/{name}/query/stream` | 需要 | 流式问答(SSE) |
| GET | `/api/audit` | 需要 | 审计日志 |

**教学点**：
- **RESTful 设计**：资源名是名词（`/api/kb` 而非 `/api/createKb`），HTTP 方法区分操作（GET/POST/DELETE）
- **Pydantic 校验**：请求体自动类型检查 + 范围校验 + 必填字段检查，FastAPI 自动生成 OpenAPI 文档（`/docs`）
- **SSE 端点用 GET**：`/query/stream` 用 GET 而非 POST，因为浏览器 `EventSource` 只支持 GET
- **错误响应统一**：4xx 返回 `{"error": "描述"}`，5xx 返回 FastAPI 默认格式

---

## 跨模块关注点

### 日志（`logging_config.py`）

- 统一格式：`时间 | 级别 | 模块名 | 消息`
- 双输出：控制台（实时）+ 文件（按天轮转，保留 30 天）
- 每个模块通过 `get_logger("模块名")` 获取独立 logger

### 用户上下文（贯穿 kb_manager/embedder/bm25/audit）

- `contextvars.ContextVar` 在请求入口设置
- 模块底层函数直接读取，无需参数传递
- 详见 `docs/AUTH.md`

### 测试架构

```
tests/
├── conftest.py           ← 根级：sys.path + CI mock + 用户上下文 + dependency override
├── test_rag.py           ← 103 tests：核心业务逻辑
├── test_api.py           ← 23 tests：API 端点 (TestClient)
├── test_embedding_cache.py ← 21 tests：嵌入缓存
├── test_audit.py         ← 20 tests：审计日志
├── test_query_rewriter.py ← 9 tests：查询改写
├── test_reranker.py      ← 15 tests：重排序
└── e2e/
    ├── conftest.py       ← E2E 共享：假 Embedding/LLM + KB 生命周期
    ├── test_api_e2e.py   ← 8 tests：API 全链路
    └── test_ui_e2e.py    ← 3 tests：Streamlit UI
```
