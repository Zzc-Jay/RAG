# RAG 知识库检索问答系统

企业级多格式知识库检索增强生成（RAG）问答系统。上传 PDF/Word/网页等文档 → 自动向量化 → 混合检索（语义 + 关键词 + RRF 融合）→ LLM 生成带引用来源的回答。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key
# Windows: set DASHSCOPE_API_KEY=sk-xxx
# Linux/Mac: export DASHSCOPE_API_KEY=sk-xxx

# 3. 启动 Streamlit (终端1)
PYTHONPATH=src streamlit run src/app.py --server.port 8501

# 4. 启动 FastAPI (终端2)
PYTHONPATH=src uvicorn api:app --host 0.0.0.0 --port 8502

# 5. 访问
# UI: http://localhost:8501
# API 文档: http://localhost:8502/docs
# 健康检查: http://localhost:8502/health
```

**Docker 一键部署：**

```bash
docker compose up -d
```

**拉取预构建镜像：**

```bash
docker pull ghcr.io/zzc-jay/rag:latest
docker compose -f docker-compose.prod.yml up -d
```

## 核心特性

- **多格式文档解析**：PDF / DOCX / TXT / MD / URL，含表格提取和扫描件检测
- **混合检索**：向量语义检索（ChromaDB）+ 关键词检索（BM25 + jieba）+ RRF 加权融合，检索偏好 5 档可调
- **查询优化**：LLM 驱动多轮对话查询改写 + 批量精排（Rerank）
- **流式生成**：SSE 协议逐 token 推送，带引用来源（文件名/页码/得分）
- **多 LLM 后端**：DashScope（通义千问）、DeepSeek、豆包 Seed 2.0、OpenAI 兼容接口
- **嵌入缓存**：SHA256 内容寻址缓存 → SQLite，跨知识库共享
- **用户认证**：JWT 注册/登录 + bcrypt 密码哈希 + 多租户数据物理隔离
- **REST API**：FastAPI 14 端点，含流式 SSE、批量操作、审计日志查询
- **审计日志**：Append-only SQLite，7 种事件类型，用户级自动筛选
- **Docker 双服务**：Streamlit（8501）+ FastAPI（8502），compose 一键部署
- **CI/CD**：GitHub Actions 自动测试 + 构建推送到 ghcr.io
- **测试**：220 个测试用例，覆盖单元/API 集成/E2E

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Streamlit |
| API | FastAPI + Pydantic + SSE |
| 向量数据库 | ChromaDB (HNSW) |
| 嵌入模型 | DashScope text-embedding-v3 (1024维) |
| LLM | DashScope qwen-plus / DeepSeek V4 / 豆包 Seed 2.0 |
| 关键词检索 | rank-bm25 + jieba |
| 融合算法 | RRF (Reciprocal Rank Fusion) |
| 缓存 | SQLite (SHA256 内容寻址) |
| 认证 | JWT (HS256) + bcrypt |
| 容器化 | Docker + docker-compose |
| CI/CD | GitHub Actions → ghcr.io |
| 测试 | pytest (220 tests) |

## 架构

```
用户 → Streamlit UI / FastAPI API → JWT 认证
  → 查询改写 → 向量检索 + BM25 检索 → RRF 融合 → (可选)LLM 精排
  → Prompt 构建 → LLM 流式生成 → 引用溯源 + 审计日志
```

详细架构图：`docs/ARCHITECTURE.md`

## 项目规模

- 17 个 Python 模块，~4000 行代码
- 14 个 API 端点（含 3 个认证）
- 220 测试（本地全通过）
- 完整 Docker 双服务 + CI/CD

## 文档导航

| 文档 | 内容 | 适合 |
|------|------|------|
| `docs/ARCHITECTURE.md` | 系统架构、数据流、技术栈 | 了解全貌 |
| `docs/MODULES.md` | 17 个模块逐一详解 | 深入源码 |
| `docs/RETRIEVAL.md` | 混合检索与 RRF 融合原理 | 理解核心技术 |
| `docs/AUTH.md` | 用户认证与多租户设计 | 理解认证设计 |
| `docs/DEPLOYMENT.md` | Docker 容器化 + CI/CD | 部署运维 |
| `docs/LEARNING-PATH.md` | 学习路径指南 | 按顺序学习 |
| `docs/INTERVIEW.md` | 面试预测 + 回答框架 | 求职准备 |

## 运行测试

```bash
# 全量测试
python -m pytest tests/ -v

# E2E 测试
python -m pytest tests/e2e/ -v --tb=short

# 单文件
python -m pytest tests/test_rag.py -v
```

## 配置

通过环境变量覆盖默认值：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DASHSCOPE_API_KEY` | — | 阿里云 DashScope API Key |
| `DEEPSEEK_API_KEY` | — | DeepSeek API Key |
| `DOUBAO_API_KEY` | — | 豆包 API Key |
| `LLM_MODEL` | `qwen-plus` | 默认 LLM 模型 |
| `JWT_SECRET_KEY` | (内置开发默认值) | JWT 签名密钥，生产需修改 |
| `RAG_DATA_DIR` | `data/` | 数据存储目录 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

## License

MIT
