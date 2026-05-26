# Spec: 简易 RAG 知识库检索系统

## Objective

构建一个基于 PDF 文档的知识库检索问答系统。用户上传 PDF 文件后，系统自动切分、向量化存入 ChromaDB，然后用户可以通过自然语言提问，系统检索相关文档片段并调用 LLM 生成回答。

- **用户**：需要基于私有 PDF 文档进行问答的个人
- **成功标准**：上传 PDF → 提问 → 得到基于文档内容的回答

## Tech Stack

| 组件 | 技术 | 说明 |
|------|------|------|
| 语言 | Python 3.11+ | |
| 界面 | Streamlit | 最简单的 Python Web UI |
| 嵌入模型 | DashScope `text-embedding-v3` | 阿里云百炼云端 API |
| 对话模型 | DashScope `qwen-plus` | 阿里云百炼云端 API |
| 向量数据库 | ChromaDB | 本地运行，零配置 |
| PDF 解析 | PyMuPDF (fitz) | 轻量快速 |
| 文档切分 | LangChain `RecursiveCharacterTextSplitter` | 语义切分 |
| API SDK | `dashscope` | 阿里云百炼 Python SDK |

## Commands

```bash
# 安装依赖
pip install -r requirements.txt

# 启动应用
streamlit run src/app.py

# 运行测试
python -m pytest tests/ -v
```

## Project Structure

```
rag/
├── docs/
│   └── spec.md            ← 本文件
├── src/
│   ├── app.py             ← Streamlit 主入口 + UI
│   ├── loader.py          ← PDF 加载与文本提取
│   ├── chunker.py         ← 文本切分
│   ├── embedder.py        ← 向量化 + ChromaDB 存取
│   └── retriever.py       ← 检索 + LLM 生成回答
├── tests/
│   └── test_rag.py        ← 集成测试
├── data/                   ← 运行时生成的 ChromaDB 数据，gitignore
├── requirements.txt
├── .env.example            ← DASHSCOPE_API_KEY 示例
└── .gitignore
```

## Code Style

```python
# 简单直接的函数式风格，一个函数只做一件事
# 类型注解保持可读但不强制完整
# 变量名用中文拼音也可以，只要清晰

def split_text(text: str, chunk_size: int = 1000) -> list[str]:
    """把长文本切成小块"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=200,
    )
    return splitter.split_text(text)
```

- 命名：`snake_case` 函数/变量，`PascalCase` 类
- 每行不超过 120 字符（大致）
- 不写 docstring，函数名即文档
- 不写类型体操，`list[str]` 就够，不搞 `List[Union[str, ...]]`

## Testing Strategy

- 框架：pytest
- 范围：仅集成测试，验证核心链路（加载→切分→嵌入→检索→生成）
- 覆盖率：不追求覆盖率，只测核心流程
- 不写单元测试，不 mock，直接跑真实流程

## Boundaries

- **Always**：保持代码简单直白，一个文件一个职责，函数不超过 30 行
- **Ask first**：添加新依赖、切换模型、引入新数据类型（Word/URL）
- **Never**：搞抽象层/工厂模式/插件系统、过度优化、加缓存层、处理安全边界

## Success Criteria

1. [ ] 启动 Streamlit 后能看到上传 PDF 的按钮
2. [ ] 上传 PDF 后自动解析、切分、向量化存入 ChromaDB
3. [ ] 能输入问题并得到基于 PDF 内容的回答
4. [ ] 回答带有来源页码或片段引用
5. [ ] 完整流程（不含 API 调用）在本地 10 秒内完成

## Open Questions

- [x] ~~LLM 选型？~~ → DashScope qwen-plus
- [x] ~~嵌入模型选型？~~ → DashScope text-embedding-v3
- [x] ~~需要多轮对话吗？~~ → 暂不需要，先做单轮
- [ ] 一个 PDF 上传后是否支持追加第二个 PDF？（建议先做单 PDF，后续再说）
