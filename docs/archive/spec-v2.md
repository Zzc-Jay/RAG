# Spec: RAG 知识库问答系统 V2

## Objective

将当前 demo 级 RAG 系统升级为生产可用的多知识库问答平台。用户在 Web 界面中管理多个独立知识库、上传多个 PDF，获得带引用溯源的流式回答。

### 用户故事

1. **多库管理** — 我可以创建"技术文档"、"合同"等知识库，每个库独立上传 PDF，切换库提问不互相干扰
2. **多文件上传** — 一个知识库内上传多个 PDF，统一检索
3. **流式问答** — 提交问题后回答逐字流式显示，不用等完整结果
4. **引用溯源** — 回答后能看到每个结论出自哪个文档的哪个段落
5. **可靠检索** — 专有名词、编号、数字等也能准确检索到
6. **稳定上传** — 大 PDF（100 页+）上传不卡顿、不超时

### Success Criteria

- [ ] 大 PDF（200 页 / 10 万字符）上传处理不超时，UI 不卡死
- [ ] 1000 个 chunk 嵌入完成时间 < 60 秒（分批 20 条/批）
- [ ] 回答流式显示，首 token 延迟 < 3 秒
- [ ] 回答引用不少于 1 条来源片段，且引用内容确实在原文中
- [ ] 混合检索对专有名词的召回率明显优于纯向量检索（人工抽样 20 题对比）
- [ ] 知识库切换/增删操作即时生效

## Tech Stack

| 组件 | 选型 | 版本 |
|------|------|------|
| 对话模型 | DashScope qwen-plus | - |
| 嵌入模型 | DashScope text-embedding-v3 | - |
| 向量库 | ChromaDB | latest |
| 关键词检索 | rank-bm25 + jieba 分词 | latest |
| PDF 解析 | PyMuPDF (fitz) | latest |
| 文本切分 | LangChain RecursiveCharacterTextSplitter | latest |
| UI 框架 | Streamlit | latest |
| 环境变量 | python-dotenv | latest |
| 语言 | Python | 3.12 |

## Commands

```bash
# 安装依赖
D:/ai/rag/env/Scripts/pip install -r requirements.txt

# 启动开发服务器
D:/ai/rag/env/Scripts/python.exe -m streamlit run D:/ai/rag/src/app.py

# 运行测试
D:/ai/rag/env/Scripts/python.exe -m pytest tests/ -v

# 运行单个测试文件
D:/ai/rag/env/Scripts/python.exe -m pytest tests/test_rag.py -v
```

## Project Structure

```
rag/
├── src/                    # 应用源码
│   ├── app.py              # Streamlit UI（异步处理 + 流式显示）
│   ├── loader.py           # PDF 文本提取（PyMuPDF）
│   ├── chunker.py          # 文本切分（语义感知切分 + overlap）
│   ├── embedder.py         # 分批嵌入 + ChromaDB 向量存储
│   ├── bm25_index.py       # BM25 关键词索引（jieba 分词）
│   ├── retriever.py        # 混合检索 + RRF 融合
│   ├── generator.py        # LLM 生成（流式 + 引用溯源）
│   ├── kb_manager.py       # 知识库 CRUD 管理
│   └── config.py           # 配置管理
├── data/                   # 持久化数据
│   ├── chroma_db/          # ChromaDB 向量存储
│   ├── bm25/               # BM25 索引文件
│   └── kb_registry.json    # 知识库注册表
├── tests/                  # 测试
│   ├── test_loader.py
│   ├── test_chunker.py
│   ├── test_embedder.py
│   ├── test_retriever.py
│   ├── test_kb_manager.py
│   └── test_generator.py
├── docs/                   # 文档与规格
│   ├── spec.md             # V1 spec（原始）
│   ├── spec-v2.md          # V2 spec（本文件）
│   ├── plan.md             # 实施计划
│   └── tasks.md            # 任务清单
├── requirements.txt
├── .env                    # 环境变量（不提交）
└── .gitignore
```

## Code Style

```python
# 函数命名：动词_名词，类型注解必写
def search_hybrid(query: str, kb_name: str, top_k: int = 10) -> list[dict]:
    """返回带分数的文档列表，每条含 content / source / score 字段"""
    ...

# 类命名：大写驼峰
class KnowledgeBaseManager:
    ...

# TODO 标记待完成工作，不写冗余注释
# 错误处理：显式抛出，附带可读的错误消息
raise RuntimeError("知识库不存在: {}".format(kb_name))
```

- Python 文件头部加 `from __future__ import annotations`
- 用 f-string，不用 % 或 .format()
- 导入顺序：标准库 → 第三方 → 本地模块
- 函数超过 30 行需要拆分

## Testing Strategy

- 框架：pytest
- 测试位置：`tests/` 目录，每个源文件对应一个 test 文件
- 覆盖要求：核心逻辑（embedder / retriever / chunker / kb_manager）必须覆盖
- 测试级别：
  - **单元测试** — chunker 切分逻辑、BM25 分词、RRF 融合算法（mock 外部调用）
  - **集成测试** — embedder + ChromaDB 写入/读取、retriever + generator 端到端（需 API key）

## Boundaries

- **Always do:**
  - 运行现有测试确认不回归再提交
  - 函数写类型注解
  - 用户输入做 XSS 过滤
  - API key 只从环境变量读取，不硬编码

- **Ask first:**
  - 添加新依赖（pip install）
  - 修改数据存储目录结构（影响已有数据）
  - 更换模型或 API 提供商

- **Never do:**
  - 提交 .env 文件
  - 在测试中硬编码真实 API key
  - 删除用户数据而不提示确认
  - 跳过错误处理（静默吞异常）

## Resolved Decisions

1. **BM25 索引持久化** — 保存到 `data/bm25/{kb_name}/`，启动时按需加载，避免重复重建
2. **流式输出** — 使用 Streamlit 内置 `st.write_stream` 实现
3. **删除确认** — 知识库删除前需用户二次确认
4. **新增依赖** — jieba（分词）、rank-bm25（关键词检索）
