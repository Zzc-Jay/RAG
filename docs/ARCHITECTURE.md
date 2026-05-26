# RAG 知识库问答系统 — 架构总览

## 一句话描述

一个**企业级知识库检索问答系统**：上传文档（PDF/Word/网页等）→ 向量化存储 → 用户提问时混合检索（语义 + 关键词）→ LLM 生成带引用的回答。

## 系统架构图

```
┌──────────────────────────────────────────────────────────────┐
│                      用户接口层                               │
│  ┌──────────────────┐    ┌──────────────────────────────┐   │
│  │  Streamlit UI    │    │  FastAPI REST API (14 端点)   │   │
│  │  (localhost:8501) │    │  (localhost:8502)             │   │
│  └────────┬─────────┘    └──────────────┬───────────────┘   │
│           │          用户认证层 (JWT)    │                    │
│           └───────────┬────────────────┘                    │
├───────────────────────┼──────────────────────────────────────┤
│                       ▼                      业务逻辑层       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ 知识库   │ │ 文档处理  │ │ 检索管线  │ │ 对话管理      │   │
│  │ 管理     │ │ 加载→切分 │ │ 向量+BM25 │ │ 历史+反馈     │   │
│  │ CRUD     │ │ →嵌入    │ │ →RRF融合  │ │              │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘   │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│                      基础设施层                               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ 嵌入缓存  │ │ 审计日志  │ │ 速率限制  │ │ API 重试      │   │
│  │ SHA256→  │ │ append-  │ │ 滑动窗口  │ │ 指数退避      │   │
│  │ SQLite   │ │ only DB  │ │          │ │              │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘   │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│                      外部依赖                                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ DashScope │ │ ChromaDB │ │ BM25     │ │ SQLite        │   │
│  │ 嵌入+LLM  │ │ 向量存储  │ │ 关键词   │ │ 缓存+审计     │   │
│  │          │ │          │ │ 检索     │ │ +用户DB       │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

## 数据流（一次完整的问答请求）

```
用户输入: "什么是 RAG？"
  │
  ▼
① API/UI 接收 → 输入校验（长度/注入检测）
  │
  ▼
② 查询嵌入: DashScope TextEmbedding → 1024维向量
  │
  ├──→ ③a 向量检索: ChromaDB 余弦相似度 Top-K
  │
  └──→ ③b 关键词检索: jieba分词 → BM25 打分 Top-K
  │
  ▼
④ RRF 融合: score = w_vec/(K+rank_vec) + w_bm25/(K+rank_bm25)
  │
  ▼
⑤ (可选) 查询改写: LLM 消解代词/省略
  │
  ▼
⑥ (可选) LLM 精排: 对候选文档批量打分重排序
  │
  ▼
⑦ 构建 Prompt: 系统指令 + 检索结果(带编号) + 对话历史 + 用户问题
  │
  ▼
⑧ LLM 生成: DashScope qwen-plus → 流式逐 token 返回 (SSE)
  │
  ▼
⑨ 返回: 回答 + 引用来源(文件名/页码/片段/分数) + 表格/扫描标记
  │
  ▼
⑩ 记录: 审计日志 + Token 用量统计 + 对话历史追加
```

## 技术栈

| 层级 | 技术 | 选型原因 |
|------|------|----------|
| 前端 | Streamlit | Python 原生，无需写 JS，快速构建数据应用 |
| API | FastAPI | 高性能异步，自动 OpenAPI 文档，依赖注入 |
| 向量数据库 | ChromaDB | 轻量嵌入式，无需单独部署服务 |
| 嵌入模型 | DashScope text-embedding-v3 | 1024 维，中文优化，¥0.0005/1K tokens |
| LLM | DashScope qwen-plus | ¥0.0008 in / ¥0.002 out，中文能力强 |
| 关键词检索 | rank-bm25 + jieba | BM25Okapi 算法，jieba 中文分词 |
| 缓存 | SQLite | SHA256 内容寻址，跨知识库共享 |
| 认证 | JWT (HS256) + bcrypt | 无状态，API/UI 复用 |
| 容器化 | Docker + docker-compose | 双服务（Streamlit + FastAPI） |
| CI/CD | GitHub Actions → ghcr.io | 自动测试 + 构建推送 |

## 项目规模

| 指标 | 数据 |
|------|------|
| Python 模块 | 17 个 src/*.py |
| 总代码量 | ~4000 行 |
| API 端点 | 14 个（含 3 个认证端点） |
| 测试用例 | 220 个 |
| 测试覆盖 | 核心逻辑 + API E2E + UI E2E |
| 支持文档格式 | PDF / DOCX / TXT / MD / URL |

## 核心模块依赖关系

```
app.py ──────────┐
api.py ──────────┤
                 ├─→ kb_manager ──→ audit
                 │       │
                 ├─→ loader ──→ chunker
                 │
                 ├─→ embedder ──→ embedding_cache
                 │       │              │
                 │       ▼              ▼
                 │    ChromaDB       SQLite
                 │
                 ├─→ bm25_index (jieba + rank-bm25)
                 │
                 ├─→ retriever ──→ embedder.search + bm25_index.search
                 │       │
                 │       ├─→ query_rewriter (可选)
                 │       └─→ reranker (可选)
                 │
                 ├─→ generator ──→ providers (多LLM)
                 │
                 ├─→ conversation (对话历史)
                 ├─→ token_tracker (用量统计)
                 ├─→ security (校验 + 限流)
                 ├─→ retry (API容错)
                 └─→ auth (JWT + 用户隔离)
```

## 核心设计决策

### 1. 为什么用 ChromaDB 而不是 FAISS/Milvus？

| 对比维度 | ChromaDB | FAISS | Milvus |
|---------|----------|-------|--------|
| 部署复杂度 | `pip install` 即用 | `pip install` 即用 | 需要 Docker/K8s |
| 持久化 | 内置 SQLite | 需手动 pickle | 内置 |
| 元数据过滤 | 原生支持 | 不支持 | 原生支持 |
| 适用场景 | 中小规模(< 百万条) | 大规模(亿级) | 大规模+集群 |
| 本项目选择 | **ChromaDB** — 轻量、嵌入式、够用 | — | — |

### 2. 为什么 RRF 融合而不是简单的分数加权？

分数加权的问题：向量相似度是 0~1 的余弦距离，BM25 是开区间分数。量纲不同直接加法毫无意义。RRF 只关心**排名**，与分值尺度无关。详见 `docs/RETRIEVAL.md`。

### 3. 为什么 contextvars 而不是函数参数做用户隔离？

需要隔离的模块有 4 个（kb_manager/embedder/bm25_index/audit），涉及 40+ 函数。给每个函数加 `user_id` 参数会像病毒一样传播。contextvars 实现"一次设置、处处可用"，零函数签名变更。

详见 `docs/AUTH.md`。

## 快速启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 设置 API Key
export DASHSCOPE_API_KEY=sk-xxx

# 3. 启动 Streamlit (终端1)
PYTHONPATH=src streamlit run src/app.py --server.port 8501

# 4. 启动 FastAPI (终端2)
PYTHONPATH=src uvicorn api:app --host 0.0.0.0 --port 8502

# 5. 访问
# UI:     http://localhost:8501
# API文档: http://localhost:8502/docs
```

Docker 一键部署：

```bash
docker compose up -d
```

## 下一步阅读

- 想理解每个模块 → `docs/MODULES.md`
- 想理解检索原理 → `docs/RETRIEVAL.md`
- 想理解认证设计 → `docs/AUTH.md`
- 想理解部署运维 → `docs/DEPLOYMENT.md`
- 准备面试 → `docs/INTERVIEW.md`
