# Plan: RAG V2 技术实施方案

## 架构升级对比

```
V1 (当前)                          V2 (目标)
───────────────────────       ───────────────────────────
单 PDF、上传即覆盖            多库多文件，独立管理
纯向量检索                   向量 + BM25 混合检索 + RRF 融合
同步处理、UI 卡死             异步上传 + 分批嵌入 + 进度反馈
结果一次性返回               流式输出 (st.write_stream)
回答无来源标注               引用溯源 [1] [2]
所有代码混在 app.py           模块拆分：7 个独立模块
```

## V2 数据流架构

```
用户上传 PDFs 到知识库 "技术文档"
    │
    ▼
┌─────────────┐
│  loader.py  │  PDF → 纯文本（不变）
└─────────────┘
    │
    ▼
┌─────────────┐
│ chunker.py  │  切分 + 元数据（source 文件名、chunk 序号、页码）
└─────────────┘
    │  chunks: list[dict]  每项含 {text, source, page, chunk_idx}
    ▼
┌────────────────┐
│ kb_manager.py  │  注册 PDF 到知识库，更新 kb_registry.json
└────────────────┘
    │
    ├──→ embedder.py  分批(20条/批) → ChromaDB collection "kb_技术文档"
    │
    └──→ bm25_index.py  jieba分词 → rank_bm25 → pickle持久化 → data/bm25/技术文档/

用户提问
    │
    ▼
┌──────────────────┐
│  retriever.py    │
│  并行执行：       │
│  ├─ ChromaDB向量 │ ──→ top_k × 2 条
│  └─ BM25关键词   │ ──→ top_k × 2 条
│       │
│       ▼
│  RRF 融合排序    │ ──→ 取 top_k 条（带分数 + source）
└──────────────────┘
    │  docs: list[dict]  {text, source, score}
    ▼
┌─────────────────┐
│  generator.py   │
│  构建带引用的    │
│  prompt          │
│  stream=True     │ ──→ 逐 token 产出
│  解析 [n] 标记   │
└─────────────────┘
    │  generator
    ▼
┌──────────┐
│  app.py  │  st.write_stream(generator) → 流式显示 + 来源折叠面板
└──────────┘
```

## 模块设计

### 1. config.py（新增）
```
职责：集中管理路径和常量
├── DASHSCOPE_API_KEY  (从环境变量读取)
├── BASE_DATA_DIR      → data/
├── CHROMA_DIR         → data/chroma_db/
├── BM25_DIR           → data/bm25/
├── REGISTRY_PATH      → data/kb_registry.json
├── CHUNK_SIZE = 800
├── CHUNK_OVERLAP = 150
├── BATCH_SIZE = 20
├── TOP_K = 5
└── RRF_K = 60
```

### 2. kb_manager.py（新增）
```
职责：知识库生命周期管理
├── create_kb(name)         → 创建知识库目录 + 注册
├── delete_kb(name)         → 删库（二次确认在 UI 层）
├── list_kbs()              → 返回所有知识库列表
├── rename_kb(old, new)     → 重命名
├── add_pdf(kb_name, chunks)→ 注册 PDF 元数据
├── remove_pdf(kb_name, pdf)→ 从知识库删除指定 PDF
├── get_kb_docs(kb_name)    → 获取知识库内文档列表
└── 存储：JSON 注册表 data/kb_registry.json
    {
      "技术文档": {
        "created": "2026-05-21",
        "pdfs": [
          {"name": "架构设计.pdf", "pages": 42, "chunks": 58},
          {"name": "API文档.pdf",   "pages": 30, "chunks": 41}
        ]
      }
    }
```

### 3. chunker.py（增强）
```
改动：
- 返回值从 list[str] 改为 list[dict]
  每项: {text, source, page, chunk_idx}
- 页码信息从 PyMuPDF 传入
- chunk_size 和 overlap 可配置（从 config 读取）
- 按句子边界切分（用中文标点 。！？\n 作为分隔符）
```

### 4. embedder.py（增强）
```
改动：
- create_index(chunks, kb_name)  参数从 persist_dir 改为 kb_name
- 分批调用 DashScope，每批 BATCH_SIZE=20 条
- 使用 ChromaDB collection 命名: "kb_{kb_name}"
- 支持增量添加（不再 delete_collection，用 upsert）
- search(query, kb_name, top_k)  参数同步改为 kb_name
- 返回 list[dict] 而不是 list[str]: {text, source, score}
```

### 5. bm25_index.py（新增）
```
职责：BM25 关键词检索
依赖：jieba + rank_bm25

├── build_index(chunks, kb_name)  → 分词 → BM25Okapi → pickle 持久化
├── load_index(kb_name)           → 从 pickle 加载
├── search_bm25(query, kb_name, top_k) → list[dict] {text, source, score}
└── 索引路径: data/bm25/{kb_name}/bm25.pkl

分词策略：
- jieba.cut() 精确模式
- 同时保留原始词和单字（对数字/编号友好）
- 停用词暂不过滤（保留"的"、"是"等，BM25 自身会降权）
```

### 6. retriever.py（重写）
```
职责：混合检索 + RRF 融合

入口函数：
  retrieve(query, kb_name, top_k) → list[dict]

流程：
  1. 并行调用 embedder.search() + bm25.search_bm25()
     各取 top_k * 2 条（增大候选池）
  2. RRF 融合：
     score(d) = Σ 1/(RRF_K + rank_i(d))
     其中 RRF_K = 60
  3. 去重：不同源但内容相似的文档保留高分那条
  4. 按融合分数排序，返回 top_k 条
  5. 每条返回: {text, source, page, score}

旧 ask() 函数删除，LLM 调用移到 generator.py
```

### 7. generator.py（新增，从 retriever 拆分）
```
职责：构建 prompt + 流式调用 LLM + 引用解析

├── generate_stream(query, docs) → Generator[str]
│   构建引用 prompt → DashScope Generation(stream=True) → yield tokens
│
└── build_prompt(query, docs) → str
    在 prompt 中要求模型标注引用来源 [1], [2] 等

Prompt 模板：
"""
根据以下参考资料回答问题。每个参考资料以 [序号] 标记来源。
如果某个资料与问题无关，不要强行使用它。
回答中用 [n] 标注引用来源。

参考资料：
[1] (来源: {source}, 第{page}页) {text}
[2] (来源: {source}, 第{page}页) {text}
...

问题：{query}
回答：
"""

流式输出：
- resp = Generation.call(model="qwen-plus", prompt=prompt, stream=True)
- for chunk in resp: yield chunk.output.text
```

### 8. app.py（重写）
```
职责：Streamlit UI + 异步调度

UI 布局：
┌─ 侧边栏 ─────────────────────┐
│ [知识库选择] 下拉框            │
│ [新建知识库] 按钮+弹窗          │
│ [删除知识库] 按钮（二次确认）    │
│ ─────────────────────        │
│ [PDF 上传] 多文件上传           │
│ [已上传列表] 显示文件+删除按钮   │
│ [处理进度] 分批嵌入进度条        │
└──────────────────────────────┘

主区域：
├── 对话输入框
├── 流式回答区（st.write_stream）
└── 引用来源展开面板（st.expander）

异步处理：
- 上传 PDF 后，在后台线程执行 loader → chunker → embedder → bm25
- 用 st.status 包裹处理流程，显示每步状态
- 分批嵌入时更新进度条
```

## 实现顺序（依赖拓扑）

```
阶段 A（基础设施，无依赖）
  [A1] config.py         ── 新建配置模块
  [A2] kb_manager.py     ── 知识库 CRUD（依赖 config）
  [A3] chunker.py 增强   ── 返回值改为 list[dict] + 句子边界

阶段 B（检索链路，依赖 A）
  [B1] bm25_index.py     ── BM25 索引（依赖 config + chunker 格式）
  [B2] embedder.py 增强  ── 分批嵌入 + kb_name 参数（依赖 config + kb_manager）

阶段 C（融合与生成，依赖 B）
  [C1] retriever.py 重写 ── 混合检索 + RRF（依赖 embedder + bm25）
  [C2] generator.py      ── 流式生成 + 引用溯源（依赖 retriever 输出格式）

阶段 D（UI 集成，依赖 C）
  [D1] app.py 重写       ── 全功能 UI（依赖所有模块）

阶段 E（验证）
  [E1] 更新测试           ── 适配新接口，补充新模块测试
  [E2] 安装新依赖         ── jieba, rank-bm25
  [E3] 端到端验收         ── 实际运行，上传 PDF 提问
```

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| DashScope embedding API 单次调用有 token 上限 | 分批 20 条/批，失败时自动减半重试 |
| ChromaDB 多 collection 管理复杂度 | 统一命名规范 `kb_{name}`，启动时校验 |
| BM25 检索长文本时内存占用 | 索引时截断过长文本（max 2000 字符） |
| RRF 融合参数 (k=60) 对效果敏感 | 先固定 k=60，后续可按需调参 |
| Streamlit 异步支持不完善 | 用 `asyncio.to_thread` 包装 CPU/IO 密集操作 |

## 验证检查点

- [A] 阶段完成 → `python -c "import config; print(config.CHROMA_DIR)"` 正常
- [A] 阶段完成 → `python -c "from kb_manager import create_kb; create_kb('test')"` 能创建
- [B] 阶段完成 → BM25 索引能持久化、embedder 分批输出
- [C] 阶段完成 → `retriever.retrieve("测试", "test_kb")` 返回混合结果
- [C] 阶段完成 → `generator.generate_stream("你好", docs)` 逐 token 产出
- [D] 阶段完成 → `streamlit run app.py` 全流程可用
- [E] 阶段完成 → 测试全部通过，端到端验证 OK
