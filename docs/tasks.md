# Tasks: RAG V2 实现任务清单

> 每个阶段完成后验证再进入下一阶段。每个模块实现时需同步写测试。

## 阶段 A：基础设施

- [ ] **Task A1: 新建 config.py**
  - 描述：创建配置模块，集中管理路径、模型参数、常量
  - 触动文件：`src/config.py`
  - 验证：`python -c "from src.config import CHROMA_DIR, BATCH_SIZE; print('ok')"`

- [ ] **Task A2: 新建 kb_manager.py**
  - 描述：知识库生命周期管理，JSON 注册表持久化
  - 触动文件：`src/kb_manager.py`, `data/kb_registry.json`
  - 验证：`python -c "from src.kb_manager import create_kb, list_kbs; create_kb('test'); assert 'test' in list_kbs()"`

- [ ] **Task A3: 增强 chunker.py**
  - 描述：返回值改为 `list[dict]`（含 source/page/chunk_idx），按句子边界切分
  - 触动文件：`src/chunker.py`, `src/loader.py`（传入页码元数据）
  - 验证：切一个测试文本，确认每块含完整字段，不在句子中间截断

## 阶段 B：检索链路

- [ ] **Task B1: 安装新依赖**
  - 描述：安装 jieba + rank-bm25，更新 requirements.txt
  - 触动文件：`requirements.txt`
  - 验证：`pip list | grep -E "jieba|rank-bm25"`

- [ ] **Task B2: 新建 bm25_index.py**
  - 描述：jieba 分词 → BM25Okapi 索引 → pickle 持久化到 `data/bm25/{kb_name}/`
  - 触动文件：`src/bm25_index.py`
  - 验证：构建索引 → 搜索 → 确认返回结果与查询相关

- [ ] **Task B3: 增强 embedder.py**
  - 描述：分批嵌入（20条/批），collection 命名改为 `kb_{name}`，支持增量添加，search 返回 `list[dict]`
  - 触动文件：`src/embedder.py`
  - 验证：上传 100+ chunks → 确认分多批调用、无报错、search 返回含 source 字段的结果

## 阶段 C：融合与生成

- [ ] **Task C1: 重写 retriever.py**
  - 描述：混合检索（向量 + BM25）+ RRF 融合 + 去重
  - 触动文件：`src/retriever.py`
  - 验证：同一查询分别跑纯向量/纯BM25/混合，确认混合结果包含两路的互补内容

- [ ] **Task C2: 新建 generator.py**
  - 描述：构建带引用标注的 prompt，流式调用 DashScope，yield token
  - 触动文件：`src/generator.py`
  - 验证：传入 query + docs → 流式打印 → 确认输出含 [n] 引用标记

## 阶段 D：UI 集成

- [ ] **Task D1: 重写 app.py**
  - 描述：全功能 Streamlit UI — 知识库选择/新建/删除、多 PDF 上传、异步处理、流式回答、引用面板
  - 触动文件：`src/app.py`
  - 验证：`streamlit run` → 创建库 → 上传 2 个 PDF → 提问 → 流式回答 + 引用来源可见

## 阶段 E：验证与文档

- [ ] **Task E1: 更新测试**
  - 描述：适配新模块接口，覆盖核心逻辑（embedder 分批、BM25 分词、RRF 融合、kb_manager CRUD）
  - 触动文件：`tests/` 目录下各文件
  - 验证：`pytest tests/ -v` 全部通过

- [ ] **Task E2: 端到端验收**
  - 描述：准备 2 个不同主题的 PDF → 分别建库 → 各提 5 个问题 → 检查流式效果、引用准确性、检索召回率
  - 验证：所有问题回答正确引用来源，流式首 token < 3s，大 PDF 上传不卡死

- [ ] **Task E3: 编写项目教学文档**
  - 描述：交付一份面向"想深入理解的开发者"的系统化教学文档，覆盖：
    - 项目概述与目标
    - 整体架构设计（附数据流图）
    - 各模块职责与接口说明
    - 关键技术原理详解（Embedding、BM25、RRF 融合、流式生成、ChromaDB）
    - 设计决策与取舍（为什么选 RRF 而非加权求和、为什么分模块而非全放 app.py）
    - 运行指南与开发指引
  - 触动文件：`docs/tutorial.md`
  - 验证：文档覆盖所有 6 个主题，代码引用准确，原理讲解清晰

## 依赖拓扑

```
A1 ──→ A2 ──→ A3
              │
  B1 ──→ B2 ─┼──→ B3
              │     │
              └──→ C1 ──→ C2 ──→ D1 ──→ E1 ──→ E2 ──→ E3
```

A 阶段内顺序执行（A3 依赖 A2 的 kb_name 概念）。
B1 → B2 顺序，B2/B3 可并行（但都依赖 A 完成）。
C1 依赖 B2+B3 都完成。
C2 依赖 C1（输出格式）。
D1 依赖所有模块。
E1-E3 在 D1 完成后顺序执行。
