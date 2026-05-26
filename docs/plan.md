# Plan: 简易 RAG 知识库检索系统

## 架构概览

```
用户上传 PDF
    │
    ▼
┌─────────────┐
│  loader.py  │  PDF → 纯文本（PyMuPDF）
└─────────────┘
    │  text: str
    ▼
┌─────────────┐
│  chunker.py │  长文本 → 小块列表（LangChain splitter）
└─────────────┘
    │  chunks: list[str]
    ▼
┌─────────────┐
│ embedder.py │  每块 → 向量 → 存入 ChromaDB（DashScope embedding）
└─────────────┘
    │  collection 就绪
    ▼
┌──────────────┐
│ retriever.py │  用户问题 → 向量 → 检索 top-K → qwen-plus 生成回答
└──────────────┘
    │  answer: str
    ▼
┌─────────────┐
│   app.py    │  Streamlit UI 串联全部流程
└─────────────┘
```

## 模块职责与接口

### loader.py
- **职责**：接收 PDF 文件路径，返回全部文本
- **入口函数**：`load_pdf(file_path: str) -> str`
- **依赖**：PyMuPDF (`fitz`)
- **风险**：扫描版 PDF（图片型）无法提取文字 → 后续再说

### chunker.py
- **职责**：接收长文本，返回切分后的文本块列表
- **入口函数**：`split_text(text: str, chunk_size=800, overlap=150) -> list[str]`
- **依赖**：LangChain `RecursiveCharacterTextSplitter`
- **说明**：参数写死在函数签名里，不搞配置对象

### embedder.py
- **职责**：接收文本块列表，调用 DashScope 做向量化，存入 ChromaDB
- **入口函数**：
  - `create_index(chunks: list[str], persist_dir: str) -> None`
  - `search(query: str, persist_dir: str, top_k: int = 5) -> list[str]`
- **依赖**：dashscope SDK、ChromaDB
- **说明**：每次上传新 PDF 会清空旧数据（单 PDF 模式）

### retriever.py
- **职责**：接收用户问题，先检索相关片段，再调用 qwen-plus 生成回答
- **入口函数**：`ask(query: str, persist_dir: str) -> str`
- **依赖**：embedder.search()、dashscope SDK（chat）
- **说明**：拼接 prompt → 调用模型 → 返回回答

### app.py（Streamlit 入口）
- **职责**：UI 布局 + 串联上述模块
- **界面元素**：
  1. 侧边栏：PDF 上传按钮 + 处理进度条
  2. 主区域：对话输入框 + 回答展示
  3. 状态提示：上传状态、处理完成提示
- **状态管理**：用 `st.session_state` 跟踪是否已处理过 PDF

## 实现顺序

```
[1] 项目骨架 ──→ [2] loader ──→ [3] chunker ──→ [4] embedder ──→ [5] retriever ──→ [6] app
```

全顺序执行，每个步骤完成后验证再进入下一步。

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| DashScope API 欠费/限流 | 在 UI 显示明确错误信息，不静默失败 |
| PDF 过大导致 token 超限 | 切分时控制 chunk_size，先不做分段聚合 |
| ChromaDB 持久化路径问题 | 固定使用 `data/chroma_db`，不搞配置 |

## 验证检查点

- [2] loader 完成 → 拿一个测试 PDF，print 前 500 字
- [3] chunker 完成 → print 切分后的块数和每块长度
- [4] embedder 完成 → 写入后 print collection 中的文档数
- [5] retriever 完成 → 传一个固定问题，print 回答
- [6] app 完成 → streamlit run，实际上传 PDF 提问
