# RAG 项目模块分析

## 项目概述

这是一个**基于 PDF 文档的 RAG（检索增强生成）知识库问答系统**。核心流程：用户上传 PDF → 提取文本 → 切分成块 → 向量化存入 ChromaDB → 用户提问 → 检索相关片段 → LLM 生成回答。

**技术栈：** Python 3.11+ / Streamlit / DashScope API（嵌入 + 对话）/ ChromaDB / PyMuPDF / LangChain

---

## 模块清单

### 1. `src/loader.py` — PDF 文本提取

- **入口函数：** `load_pdf(file_path: str) -> str`
- **依赖：** PyMuPDF (`fitz`)
- **功能：** 打开 PDF 文件，逐页提取纯文本，用换行符拼接所有页面后返回。
- **局限：** 只能提取文字型 PDF，扫描版（图片型）PDF 无法提取。

### 2. `src/chunker.py` — 文本切分

- **入口函数：** `split_text(text: str, chunk_size=800, overlap=150) -> list[str]`
- **依赖：** LangChain `RecursiveCharacterTextSplitter`
- **功能：** 将长文本按语义边界递归切分成小块。分隔符优先级：段落 → 换行 → 中英文标点 → 空格 → 字符。chunk_size=800 字符，相邻块之间 overlap=150 字符，确保上下文连贯。
- **作用：** 解决两个问题——(1) LLM 上下文窗口有限，不能一次塞入整本书；(2) 小块精确匹配，提高检索准确率。

### 3. `src/embedder.py` — 向量化与检索引擎

- **入口函数：**
  - `create_index(chunks: list[str], persist_dir: str) -> None` — 批量向量化并写入数据库
  - `search(query: str, persist_dir: str, top_k=5) -> list[str]` — 语义检索
- **依赖：** DashScope SDK（`text-embedding-v3` 模型）、ChromaDB
- **功能：**
  - **写入（create_index）：** 接收文本块列表 → 调用 DashScope `text-embedding-v3` 批量生成向量 → 清空旧 collection → 存入 ChromaDB（持久化到 `data/chroma_db/`）。每次上传新 PDF 会覆盖旧数据（单 PDF 模式）。
  - **检索（search）：** 接收用户问题 → 调用 DashScope 将问题转为向量 → 在 ChromaDB 中做余弦相似度搜索 → 返回 top_k 个最相关的文本块。

### 4. `src/retriever.py` — 检索 + LLM 回答生成

- **入口函数：** `ask(query: str, persist_dir: str) -> str`
- **依赖：** `embedder.search()`、DashScope SDK（`qwen-plus` 模型）
- **功能：** 先调用 `embedder.search()` 检索相关文档片段 → 拼接 prompt（参考资料 + 用户问题） → 调用 DashScope `qwen-plus` 生成回答 → 返回回答文本。如果没有检索到任何片段，返回提示信息。

### 5. `src/app.py` — Streamlit 用户界面

- **入口：** `streamlit run src/app.py`
- **依赖：** 以上所有模块 + Streamlit
- **功能：**
  - **侧边栏：** PDF 文件上传组件（仅接受 `.pdf`），上传后自动执行全流程（loader → chunker → embedder），显示处理进度和状态。
  - **主区域：** 文本输入框用于提问，点击后展示 LLM 生成的回答。
  - **状态管理：** 使用 `st.session_state` 跟踪 PDF 是否已处理和当前文件名，避免重复处理同一文件。
  - **关键变量：** `PERSIST_DIR` 指向 `data/chroma_db/`，存放 ChromaDB 持久化数据。

### 6. `tests/test_rag.py` — 集成测试

- **框架：** pytest
- **测试用例：**
  - `test_load_pdf` — 用代码生成一个迷你 PDF，验证文本提取正确。
  - `test_split_text` — 生成一段长文本，验证切分后块数 ≥ 2 且每块长度不超过 `chunk_size + overlap`。
  - `test_pipeline_without_api` — 验证 loader + chunker 完整链路（不调 API）。
- **辅助函数：** `create_test_pdf(text)` 用 PyMuPDF 动态生成测试 PDF，免去测试依赖外部文件。

---

## 数据流图

```
PDF 文件
  │
  ▼
loader.py ── text: str ──▶ chunker.py ── chunks: list[str] ──▶ embedder.py
                                                                  │
                                                          ChromaDB (data/chroma_db/)
                                                                  │
用户问题 ──────────────────────────────────────────────────────▶ search()
                                                                  │
                                                          top_k chunks
                                                                  │
                                                                  ▼
                                                            retriever.py
                                                              │
                                                        qwen-plus 生成
                                                              │
                                                              ▼
                                                          回答文本
```

## 配置与依赖

| 文件 | 说明 |
|------|------|
| `requirements.txt` | Python 依赖：streamlit, chromadb, pymupdf, langchain, langchain-text-splitters, dashscope, python-dotenv, pytest |
| `.env.example` | 环境变量模板，仅需配置 `DASHSCOPE_API_KEY`（阿里云百炼 API 密钥） |
| `.gitignore` | 排除 `.env`、`data/`、`__pycache__/`、`.venv/` |

## 关键设计决策

1. **单 PDF 模式：** 每次上传新 PDF 会清空 ChromaDB 旧 collection，不支持多 PDF 并存。这是有意为之，保持简单。
2. **单轮对话：** 不做多轮对话，每次提问独立处理。
3. **参数内嵌：** chunk_size=800、overlap=150、top_k=5 直接写在函数签名默认值中，不抽配置对象。
4. **无抽象层：** 每个模块一个文件一个核心函数，不引入工厂模式、接口、依赖注入等。
5. **仅集成测试：** 不写单元测试和 mock，直接跑真实流程。
