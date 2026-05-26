# 学习路径指南

## 这个项目教你什么

这是一个**刻意设计的学习项目**，不是简单的 API Demo。每个模块、每个设计决策都有教学目的。

---

## 建议学习顺序（3 天 到 2 周，视深度）

### 第一轮：跑起来，建立直觉（半天）

**目标**：能把系统跑起来，上传一个文档，问几个问题，了解整体数据流。

1. 安装依赖，配好 `DASHSCOPE_API_KEY`
2. 启动 Streamlit → 创建一个知识库 → 上传 PDF → 提问
3. 看看 API 文档 → `localhost:8502/docs` → 试试 `/health`、`/api/kb`
4. 读 `docs/ARCHITECTURE.md`，画出自己的理解（白纸手绘即可）

**检查点**：能解释"从用户提问到回答返回经历了哪些步骤"。

---

### 第二轮：逐模块吃透（2~3 天）

**目标**：理解每个模块的输入、输出、设计决策。

按依赖关系从内到外阅读：

```
Day 1 上午: config.py → loader.py → chunker.py
          理解：配置管理、多格式文档解析、文本切分策略

Day 1 下午: embedder.py → embedding_cache.py → bm25_index.py
          理解：向量嵌入流程、分批 API 调用、内容寻址缓存、BM25 原理

Day 2 上午: retriever.py → RRF 融合算法
          重点：这是核心亮点，需要彻底理解 RRF 公式和为什么用它
          读 docs/RETRIEVAL.md，理解分数归一化 vs RRF

Day 2 下午: generator.py → providers.py → conversation.py
          理解：Prompt 构建、多 LLM 后端抽象、对话历史管理

Day 3 上午: kb_manager.py → audit.py → security.py
          理解：知识库 CRUD、JSON 注册表、审计日志设计、输入安全

Day 3 下午: auth.py → 多租户设计
          读 docs/AUTH.md，理解：contextvars、JWT、bcrypt、数据隔离
```

**每读完一个模块问自己三个问题**：
1. 这个模块解决什么问题？
2. 它的输入和输出是什么？
3. 如果让我重新设计，我会怎么做？

---

### 第三轮：跨模块追踪（1 天）

**目标**：理解模块之间的协作关系。

选一条完整调用链，用代码跳转工具（VS Code F12）一路追踪：

```
用户提问 "什么是RAG？"
  → app.py: st.chat_input()
  → validate_question() → 安全校验
  → retrieve() → retriever.py
      → embedder.search() → 向量检索
      → bm25_index.search_bm25() → 关键词检索
      → _rrf_fuse() → 融合排序
      → (可选) query_rewriter.rewrite() → 查询改写
      → (可选) reranker.rerank() → 精排
  → generate_stream() → generator.py
      → build_prompt() → 拼装 Prompt
      → provider.generate_stream() → LLM 流式生成
  → audit.log_event() → 审计记录
  → token_tracker.record_generation() → 用量统计
  → conversation.add_turn() → 保存对话历史
```

**画图**：用箭头画出这个调用链，标注每步做了什么。

---

### 第四轮：读测试（半天）

**目标**：通过测试理解预期行为。

```bash
# 按文件顺序读
tests/test_rag.py        # 核心逻辑的单测，涵盖各种边界情况
tests/test_api.py        # API 端点测试，看请求/响应格式
tests/test_audit.py      # 审计日志的 CRUD 测试
tests/e2e/test_api_e2e.py # 全链路端到端测试
tests/conftest.py        # 测试基础设施：mock 策略、路径配置
```

测试是最精确的"这个函数应该怎么用"的文档。

---

### 第五轮：面试准备（1 天）

**目标**：能自信地讲清楚你的项目。

1. 通读 `docs/INTERVIEW.md`
2. 用自己的话把 12 道预测题都回答一遍
3. 重点练习 Q4（RRF 融合），这是你的王牌
4. 准备一个 3 分钟的"项目介绍"，结构如下：
   - 一句话说清楚这项目是什么（30 秒）
   - 技术架构和数据流（1 分钟）
   - 你做的核心决策和亮点（1 分钟）
   - 如果再做一次会改什么（30 秒）

---

## 核心学习点（面试高频）

| 知识点 | 对应模块 | 为什么重要 |
|--------|---------|-----------|
| RRF 融合算法 | retriever.py | 检索系统的核心创新，区分于"调 API 套壳" |
| 嵌入缓存设计 | embedding_cache.py | 理解内容寻址存储（Content-Addressable） |
| BM25 检索原理 | bm25_index.py | 经典信息检索算法，面 NLP 岗位必问 |
| JWT 认证 | auth.py | 面后端岗位的标配知识 |
| contextvars 用户隔离 | auth.py + kb_manager | 展示架构设计能力 |
| ChromaDB 选型 | embedder.py | 展示技术选型能力 |
| SSE 流式输出 | api.py + generator.py | 实时交互的工程实现 |
| 测试策略 | tests/ | 测试金字塔：单元→集成→E2E |

---

## 相关文档

| 文档 | 内容 |
|------|------|
| `docs/ARCHITECTURE.md` | 系统架构总览、数据流、技术栈 |
| `docs/MODULES.md` | 17 个模块逐一详解 |
| `docs/RETRIEVAL.md` | 混合检索与 RRF 融合深度讲解 |
| `docs/AUTH.md` | 用户认证与多租户设计 |
| `docs/DEPLOYMENT.md` | Docker 容器化与 CI/CD |
| `docs/INTERVIEW.md` | 面试预测题 + 回答框架 + 岗位策略 |
