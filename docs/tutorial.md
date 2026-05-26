# RAG 知识库问答系统 — 教学文档

> 本文档面向想深入理解 RAG 系统原理和工程实现的开发者。
> 读完你会理解：RAG 是什么、每个模块做什么、关键算法为什么这么选、以及代码是怎么串起来的。

---

## 目录

1. [RAG 是什么](#1-rag-是什么)
2. [整体架构](#2-整体架构)
3. [模块详解](#3-模块详解)
4. [关键技术原理](#4-关键技术原理)
5. [设计决策与取舍](#5-设计决策与取舍)
6. [运行指南](#6-运行指南)

---

## 1. RAG 是什么

### 1.1 问题的起点

大语言模型（LLM）有两个根本限制：

1. **知识截止日期** — 模型训练完后，世界继续变化，它不知道新发生的事情
2. **幻觉** — 模型被问到它不知道的事情时，可能会"编造"听起来合理但完全错误的内容

RAG（Retrieval-Augmented Generation，检索增强生成）解决这个问题的方式很简单：**先检索，再生成**。

### 1.2 工作流程

```
用户问题 → [检索] → 从知识库找到相关文档片段 → [生成] → LLM阅读片段后回答问题
```

类比：你问一个专家一个问题，但他不直接回答。他先去书架（你的文档库）上翻几本书，找到相关段落，读一遍，然后基于这些段落组织答案。答案里还能告诉你是从哪本书哪一页找到的。

### 1.3 本项目做了什么

- **上传 PDF** → 提取文字 → 切成小块
- **向量化** → 每块文字变成一串数字（embedding），存到向量数据库
- **关键词索引** → 同时建立 BM25 关键词索引，存到本地文件
- **提问时** → 同时用向量搜索 + 关键词搜索 → 融合排序 → 取最相关的给 LLM
- **LLM 回答** → 流式逐字输出，标注引用来源

---

## 2. 整体架构

### 2.1 模块划分

```
src/
├── config.py        # 所有配置项集中管理
├── loader.py        # PDF → 按页提取文本
├── chunker.py       # 长文本 → 小片段（保留页码等元数据）
├── embedder.py      # 文本 → 向量，存入 ChromaDB（分批）
├── bm25_index.py    # 文本 → 分词 → BM25 索引（持久化）
├── retriever.py     # 混合检索（向量 + BM25）→ RRF 融合排序
├── generator.py     # 构建 prompt → 调用 LLM → 流式输出
├── kb_manager.py    # 知识库的增删改查（JSON 注册表）
└── app.py           # Streamlit UI，串联所有模块
```

### 2.2 数据流（上传 PDF）

```
PDF 文件
  │
  ▼
loader.py: 逐页提取 → [{page:1, text:"..."}, {page:2, text:"..."}]
  │
  ▼
chunker.py: 按页切分 → [{text, source, page, chunk_idx}, ...]
  │
  ┌─────────────┬─────────────┐
  ▼             ▼             ▼
embedder.py  bm25_index.py  kb_manager.py
DashScope     jieba分词      JSON注册表
  │             │
  ▼             ▼
ChromaDB     data/bm25/xx/
向量存储      pickle 文件
```

### 2.3 数据流（提问）

```
用户问题: "这个系统的架构是什么？"
  │
  ├──→ embedder.search()    → 向量相似度检索 → top_k × 2 条
  │
  └──→ bm25.search_bm25()   → 关键词匹配检索 → top_k × 2 条
            │
            ▼
  retriever._rrf_fuse()    → RRF 融合 → top_k 条
            │
            ▼
  generator.build_prompt() → 构造含引用的 prompt
            │
            ▼
  generator.generate_stream() → DashScope → 逐 token 产出
            │
            ▼
  app.py: st.write_stream() → 用户看到流式回答
```

---

## 3. 模块详解

### 3.1 config.py — 配置中心

**为什么需要它**：V1 中路径和参数散落在各个文件里（`persist_dir = "data/chroma_db"` 出现在 3 个地方）。改了就得全局搜索。集中到一个文件后，所有模块 `from config import XXX`，单点修改。

关键配置：

```python
CHUNK_SIZE = 800      # 每个片段最大 800 字符
CHUNK_OVERLAP = 150   # 相邻片段重叠 150 字符，避免信息在边界断裂
BATCH_SIZE = 20       # 每批嵌入 20 条，避免超 DashScope token 限制
RRF_K = 60            # RRF 融合常数（越大越平滑，越不容易被单一高排名主导）
TOP_K = 5             # 最终返回给 LLM 的文档数
```

### 3.2 loader.py — PDF 文本提取

**V1 → V2 关键变化**：返回类型从 `str` 变成 `list[dict]`。

```python
# V1: 把所有页拼成一个大字符串
def load_pdf(path) -> str:
    return "\n".join([page.get_text() for page in doc])

# V2: 按页返回，保留页码
def load_pdf(path) -> list[dict]:
    return [{"page": i+1, "text": page.get_text()} for i, page in enumerate(doc)]
```

**为什么**：引用溯源需要知道"这段话来自第几页"。如果 V1 那样全部拼在一起再切分，页码信息就丢了。

### 3.3 chunker.py — 文本切分

**核心问题**：PDF 一页可能有几千字，不能直接喂给 embedding 模型（token 限制），也不能直接喂给 LLM（上下文窗口有限）。需要切成小块。

**切分策略**：使用 LangChain 的 `RecursiveCharacterTextSplitter`，按优先级递归切分：

```
分隔符优先级: "\n\n" > "\n" > "。" > "！" > "？" > "；" > "." > " " > ""
               段落      句子      中文标点                英文标点    兜底
```

先尝试在段落边界切，切不动再在句子边界切，切不动再在词边界切… 实在不行按字符硬切。

**overlap 的作用**：相邻 chunk 重叠 150 字符。假设一句话正好在 chunk 边界被截断：
- 没有 overlap：前一半在 chunk A，后一半在 chunk B，两个 chunk 都丢了完整信息
- 有 overlap：chunk A 的前半句 + 后半句都在 chunk B 的开头区域，B 保存了完整信息

**V2 增强**：保留 source（PDF 文件名）、page（页码）、chunk_idx（序号），一路传递到检索结果。

### 3.4 embedder.py — 向量嵌入与存储

**什么是 Embedding**：把一段文字变成一串数字（向量），语义相似的文字，向量之间距离就近。

```
"Python 是一种编程语言" → [0.12, -0.34, 0.56, ..., 0.78]  (1024 维向量)
"Java 也是一种编程语言"  → [0.11, -0.32, 0.58, ..., 0.79]  (距离很近)
"今天天气真好"           → [-0.45, 0.67, -0.23, ..., 0.01] (距离很远)
```

**V2 三个关键改进**：

1. **分批嵌入** — V1 把所有 chunks 一次推给 API，长 PDF 直接超限。V2 每批 20 条，循环调用。

```python
def _batch_embed(texts):
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        resp = TextEmbedding.call(input=batch, ...)
        # 收集本批结果
```

2. **增量 upsert** — V1 先 `delete_collection` 再 `create_collection`，每次上传清空全部。V2 用 `upsert`：相同 id 的更新，新 id 的插入，不影响其他文档。

3. **元数据存储** — 每个向量附带 source、page、chunk_idx 到 ChromaDB 的 metadata，检索时可以返回。

**ChromaDB 存储结构**：

```
data/chroma_db/
├── 技术文档/           ← 一个知识库 = 一个 ChromaDB 目录
│   └── ...             ← ChromaDB 内部文件
├── 合同库/
│   └── ...
```

### 3.5 bm25_index.py — 关键词检索

**为什么需要 BM25**：向量检索擅长"语义相似"但不擅长"精确匹配"。

- 向量检索：问 "API 接口规范" → 可能找到 "RESTful 设计指南"（语义相关）
- BM25 检索：问 "RFC 7231" → 精确找到含 "RFC 7231" 的文档（关键词匹配）

**BM25 原理**：

```
BM25(d, q) = Σ IDF(q_i) × (tf(q_i, d) × (k1 + 1)) / (tf(q_i, d) + k1 × (1 - b + b × |d|/avgdl))

其中:
- tf(q_i, d)  = 词 q_i 在文档 d 中出现的频率
- IDF(q_i)    = log((N - n + 0.5) / (n + 0.5))  — 逆文档频率，词在多少文档中出现过
- |d|/avgdl   = 文档长度 / 平均文档长度  — 长度归一化
- k1 = 1.5, b = 0.75  — 经典参数
```

通俗理解：一个词在**这个文档**中出现得越频繁（TF↑），且在**其他文档**中越罕见（IDF↑），它对得分的贡献就越大。

**中文分词**：BM25 需要把中文句子切分成词。英文天然有空格分隔，中文没有。本项目用 jieba 做分词：

```python
jieba.cut("今天天气真好")  → ["今天", "天气", "真好"]
```

**持久化**：索引构建后 pickle 保存到 `data/bm25/{kb_name}/bm25.pkl`。再次启动时直接加载，不需要重建。

### 3.6 retriever.py — 混合检索

**核心思想**：向量检索和 BM25 是互补的。把两路结果融合可以取长补短。

**RRF（Reciprocal Rank Fusion）融合算法**：

```
score(d) = Σ 1 / (k + rank_i(d))

其中:
- k = 60 (平滑常数)
- rank_i(d) = 文档 d 在第 i 路检索结果中的排名（从 1 开始）
```

**为什么用 RRF 而不是加权求和**：

| 方法 | 问题 |
|------|------|
| 加权求和 | 向量相似度范围 [0, 1]，BM25 分数范围 [0, 50+]，量纲不同。需要调试权重 α 和 β。 |
| RRF | 只看排名，不看绝对分值。天然消除了量纲差异，无需调权。业界标准做法。 |

**示例计算**：

```
文档 "Java 编程" 在向量检索排第 2，在 BM25 排第 1：
  score = 1/(60+2) + 1/(60+1) = 0.01613 + 0.01639 = 0.03252

文档只在向量检索排第 5，BM25 没出现：
  score = 1/(60+5) = 0.01538
```

### 3.7 generator.py — 流式生成

**Prompt 工程**：

```
请根据以下参考资料回答问题。每个参考资料以 [序号] 标记来源。
如果某个资料与问题无关，不要强行使用它。
回答中用 [n] 标注引用来源。

参考资料：
[1] (来源: 架构设计.pdf, 第5页)
系统的核心采用了微服务架构...

[2] (来源: API文档.pdf, 第12页)
接口使用 RESTful 风格...

问题：这个系统的架构是什么？
回答：
```

几个关键设计：
- **"如果与问题无关，不要强行使用"** — 防止模型为凑引用而引用无关内容
- **[n] 标记** — 让模型学会标注来源，用户可以追溯
- **来源 + 页码** — 在 prompt 中给足元数据，模型才能在回答中引用

**流式输出**：设置 `stream=True`，DashScope 返回一个迭代器：

```python
resp = Generation.call(model="qwen-plus", prompt=prompt, stream=True)
for chunk in resp:
    yield chunk.output.text  # 逐 token 产出
```

Streamlit 的 `st.write_stream(generator)` 接收这个迭代器，边产出边显示。

### 3.8 kb_manager.py — 知识库管理

**设计思路**：用一个 JSON 文件作为"注册表"，记录所有知识库的元数据：

```json
{
  "技术文档": {
    "created": "2026-05-21",
    "pdfs": [
      {"name": "架构设计.pdf", "pages": 42, "chunks": 58},
      {"name": "API文档.pdf", "pages": 30, "chunks": 41}
    ]
  },
  "合同库": {
    "created": "2026-05-21",
    "pdfs": []
  }
}
```

删除知识库时，同时清理三类数据：
1. 注册表中的条目
2. ChromaDB 的向量数据（`data/chroma_db/{name}/`）
3. BM25 索引文件（`data/bm25/{name}/`）

### 3.9 app.py — Streamlit UI

**布局设计**：

```
┌─ 侧边栏 ───────────────────┐  ┌─ 主区域 ──────────────────┐
│ 知识库选择 [下拉框]         │  │                           │
│ [新建] [删除(需确认)]       │  │  RAG 知识库问答            │
│ ──────────────────          │  │                           │
│ 上传 PDF [文件选择器]       │  │  [问题输入框______________] │
│ [处理上传的文件]            │  │                           │
│ 已上传:                     │  │  ### 回答                 │
│  - 架构设计.pdf (42页) [×]  │  │  流式输出的回答内容...    │
│  - API文档.pdf (30页)  [×]  │  │                           │
│                             │  │  ▸ 参考来源 (5条)         │
└─────────────────────────────┘  └───────────────────────────┘
```

**状态管理**：用 `st.session_state` 跟踪当前选中的知识库和已上传文件列表，切换知识库时自动刷新文档列表。

---

## 4. 关键技术原理

### 4.1 Embedding（向量嵌入）

**本质**：把离散的文字映射到连续的向量空间。在这个空间里，语义越接近的东西距离越近。

```
文本 → Tokenizer → 词元序列 → Embedding 模型 → [0.12, -0.34, ...]
```

本项目使用 DashScope `text-embedding-v3`，输出 1024 维向量。

**相似度计算**：ChromaDB 默认使用余弦相似度。

```
cos(A, B) = (A · B) / (||A|| × ||B||)

值域 [-1, 1]，1 表示方向完全一致（语义相同），0 表示正交（无关），-1 表示相反。
```

实际存储的是余弦**距离** = 1 - cos(A, B)，值域 [0, 2]，越小越相似。

### 4.2 ChromaDB

ChromaDB 是一个轻量级向量数据库，核心功能：

- **存储**：每条数据 = id + embedding（向量）+ document（原文）+ metadata（元数据）
- **索引**：对向量建 HNSW 索引（一种近似最近邻搜索算法），O(log n) 查找
- **查询**：给定一个 query vector，返回最相似的 k 条数据

```python
collection.query(query_embeddings=[[0.12, -0.34, ...]], n_results=5)
# 返回: {ids, embeddings, documents, metadatas, distances}
```

### 4.3 BM25

BM25 是概率检索模型（Best Matching 25），属于 TF-IDF 体系的改进版。核心改进：

- **TF 饱和**：词出现 1 次和 100 次不应该差距 100 倍。BM25 用 `tf/(tf+k1)` 的形式让高频词的效果逐渐饱和。
- **长度归一化**：长文档天然包含更多词，不应因长度而获得不合理的优势。用 `|d|/avgdl` 做文档长度惩罚。

### 4.4 RRF 融合

RRF 来自 2009 年的一篇论文，最初用于元搜索（合并多个搜索引擎的结果）。核心洞察：不同搜索引擎的分数不可直接比较，但排名顺序可以。

```
score(d) = 1/(60 + rank_vector(d)) + 1/(60 + rank_bm25(d))
```

k=60 的选择：足够大来平滑排名差异，又足够小来区分相邻排名。k 越大，排名差异的影响越小（趋向于平均）；k 越小，排名差异的影响越显著。

### 4.5 流式生成

LLM 生成文本是逐个 token 产出的（auto-regressive）。非流式模式下，调用方要等所有 token 生成完才返回。流式模式下，每产出一个 token 就立刻返回。

DashScope 的流式 API 基于 SSE（Server-Sent Events），服务端持续推送数据，客户端用迭代器消费。

---

## 5. 设计决策与取舍

### 5.1 为什么分模块而不是全写 app.py？

V1 把逻辑混在 app.py 里，100 行以内还行。但 V2 新增了知识库管理、BM25、RRF 融合等逻辑，全放一个文件会有 500+ 行。

模块化的收益：
- **可测试** — 每个模块可以独立写测试，不需要启动 Streamlit
- **可替换** — 如果以后换 OpenAI 的 embedding，只改 embedder.py，不动其他文件
- **可理解** — 一个文件一个职责，看文件名就知道它干什么

### 5.2 为什么用 ChromaDB 而不是 FAISS？

| | ChromaDB | FAISS |
|------|------|------|
| 定位 | 向量数据库（自带存储） | 向量索引库（只做索引） |
| 持久化 | 内置 | 需要自己实现 |
| 元数据 | 内置 metadata 过滤 | 需要外部存储 |
| 上手难度 | 低，pip install 就能用 | 中，数据管理要自己写 |

对于本项目（轻量、本地、需要持久化），ChromaDB 更合适。

### 5.3 为什么用 jieba 做分词？

中文 NLP 分词选择：
- **jieba**：经典、稳定、轻量、纯 Python。适合本地小规模使用。
- **HanLP**：更准确但更重，需要 Java 运行时或额外的模型文件。
- **pkuseg**：更准确但更慢，需要下载模型。

本项目文本量不大，jieba 的精确度足够，不需要引入重型依赖。

### 5.4 为什么先删旧数据再 upsert？

上传同一个 PDF 的新版本时，旧的 chunks 还在 ChromaDB 里。如果只 upsert 新 chunks，旧的那些不在新 chunks 里的 id 不会被清理。

解决办法：在 `add_to_kb` 之前，先通过 metadata filter 查出该 PDF 的所有旧 id，删除后再 upsert。这保证了"同名 PDF 替换"的语义正确。

### 5.5 为什么要分批嵌入？

DashScope text-embedding-v3 限制单次请求最多 25 条文本。V1 一次性把所有 chunks 传入，超过 25 条必然报错。

V2 每批 20 条（留 5 条的 buffer），循环调用，并在每次 API 调用间自然形成对 UI 线程的让出（虽然是同步的，但 Streamlit 可以在 `st.status` 中显示状态更新）。

---

## 6. 运行指南

### 6.1 环境准备

```bash
# 1. 确保 Python 3.12 已安装
python --version

# 2. 激活虚拟环境（或直接使用项目自带的 venv）
# 项目自带: D:/ai/rag/env/Scripts/python.exe

# 3. 安装依赖
D:/ai/rag/env/Scripts/pip install -r requirements.txt

# 4. 配置 API Key
# 在系统环境变量中设置 DASHSCOPE_API_KEY
# Windows: setx DASHSCOPE_API_KEY "your-key-here"
```

### 6.2 启动

```bash
D:/ai/rag/env/Scripts/python.exe -m streamlit run D:/ai/rag/src/app.py
```

访问 http://localhost:8501

### 6.3 运行测试

```bash
D:/ai/rag/env/Scripts/python.exe -m pytest tests/ -v
```

### 6.4 目录结构

```
rag/
├── src/           # 源代码（8 个模块）
├── tests/         # 测试（11 个测试用例）
├── data/          # 运行时数据（ChromaDB + BM25 + 注册表）
│   ├── chroma_db/ # 向量存储（每个知识库一个子目录）
│   ├── bm25/      # BM25 索引（每个知识库一个 pickle 文件）
│   └── kb_registry.json  # 知识库注册表
├── docs/          # 文档
│   ├── spec.md    # V1 规格
│   ├── spec-v2.md # V2 规格
│   ├── plan.md    # V1 计划
│   ├── plan-v2.md # V2 计划
│   ├── tasks.md   # 任务清单
│   └── tutorial.md# 本文档
└── requirements.txt
```

---

## 附录：V1 → V2 变化速览

| 维度 | V1 | V2 |
|------|------|------|
| PDF 数量 | 单 PDF | 多库多文件 |
| 检索方式 | 纯向量 | 向量 + BM25 + RRF |
| 嵌入方式 | 一次性 | 分批 20 条 |
| 输出方式 | 完整返回 | 流式逐字显示 |
| 来源标注 | 无 | [n] 标注 + 来源面板 |
| 模块数 | 4 个 | 8 个 |
| UI 状态 | 卡顿 | 进度反馈 + 非阻塞 |
| 测试 | 3 个集成测试 | 11 个单元+集成测试 |
