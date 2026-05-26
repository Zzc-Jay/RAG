# Plan: P1 打磨实施方案

## 设计原则

1. **会话级状态** — 对话历史存储在 st.session_state，不引入数据库
2. **渐进复杂度** — 先实现核心多轮对话，再补 token 统计和 UI 增强
3. **Prompt 工程优先** — 多轮对话的核心是 prompt 设计，代码改动不大
4. **可独立验证** — 每个子功能可单独测试和验收

---

## 模块设计

### 1. `src/conversation.py` — 对话历史管理

```
职责：管理多轮对话的上下文窗口

数据结构:
  Turn = {"question": str, "answer": str, "references": list[dict], "timestamp": str}
  Conversation = list[Turn]

API:
  create_conversation() -> Conversation
    创建空对话

  add_turn(conv: Conversation, question: str, answer: str, refs: list[dict]) -> Conversation
    添加一轮问答，返回更新后的对话

  get_history(conv: Conversation, max_turns: int = 5) -> list[Turn]
    返回最近 N 轮对话（用于注入 prompt）

  format_for_prompt(turns: list[Turn]) -> str
    将历史格式化为 prompt 可用的文本:
    """
    对话历史：
    用户：什么是 RAG？
    助手：RAG 是检索增强生成技术...
    用户：它有什么优点？
    助手：[这轮正在生成，不包含]
    """

  estimate_tokens(text: str) -> int
    粗略估算 token 数（中文: 字符数/2, 英文: 字符数/4）
    用于判断是否需要截断历史
```

### 2. `src/token_tracker.py` — Token 统计

```
职责：累计和展示 API 调用用量

API:
  TokenUsage = namedtuple("TokenUsage", ["input_tokens", "output_tokens"])

  TokenTracker 类:
    __init__()
      self.embedding_input = 0
      self.generation_input = 0
      self.generation_output = 0
      self.history: list[dict] = []

    record_embedding(input_tokens: int) -> None
    record_generation(input_tokens: int, output_tokens: int) -> None

    @property
    def total_tokens(self) -> int
    @property
    def estimated_cost(self) -> float
    @property
    def summary(self) -> dict
      返回 {embedding_tokens, generation_input, generation_output, total, cost}

    费用计算:
      embedding: 0.0005 元/1K tokens
      generation_input: 0.0008 元/1K tokens
      generation_output: 0.002 元/1K tokens
```

### 3. `src/generator.py` 改造

```
生成 prompt 时注入对话历史:

build_prompt_with_history(query, docs, history_turns) -> str:
  """
  对话历史：
  用户：xxx
  助手：xxx

  参考资料：
  [1] ...

  当前问题：xxx
  回答：
  """

generate_stream 改动:
  - 接收可选的 history 参数
  - 调用后从 resp 提取 usage 信息（如有），传给 token_tracker
  - DashScope Generation 响应中 output.usage 含 input_tokens, output_tokens
```

### 4. `src/app.py` 改造

```
对话 UI 重构:
  旧：单个输入框 + 单个回答区
  新：对话线程视图

布局:
┌─ 主区域 ────────────────────────────────────┐
│  [对话历史线程]                               │
│  ┌─ Q: 什么是 RAG？ ───────────────────────┐ │
│  │  A: RAG 是检索增强生成...                │ │
│  │  📊 输入 234 tokens · 输出 156 tokens    │ │
│  │  💰 约 ¥0.0004                           │ │
│  │  [👍] [👎] [📋 复制]                     │ │
│  │  ▸ 参考来源 (3条)                        │ │
│  └──────────────────────────────────────────┘ │
│  ┌─ Q: 它有什么优点？ ──────────────────────┐ │
│  │  A: RAG 的主要优点包括...                 │ │
│  │  ...                                      │ │
│  └──────────────────────────────────────────┘ │
│                                                │
│  [问题输入框________________] [提问]           │
│  [🗑 清空对话] [📥 导出对话]                   │
└──────────────────────────────────────────────┘

侧边栏新增:
  Session 统计:
    嵌入: 12,345 tokens
    生成输入: 5,678 tokens
    生成输出: 3,210 tokens
    合计: 21,233 tokens · ¥0.012
```

### 5. 回答操作与反馈

```
复制按钮:
  - 使用 st.code 组件天然带复制按钮，或
  - 自定义按钮 + JavaScript clipboard API（通过 st.markdown 注入）

导出对话:
  - "导出对话"按钮 → 生成 Markdown 文本
  - st.download_button 触发下载

用户反馈:
  - 每轮回答下方 👍/👎 按钮
  - 存储在 session_state 中跟踪点击状态
  - 记录到 data/feedback.jsonl（追加模式，每行一条 JSON）
```

---

## 实现顺序

```
阶段 1（对话核心）
  [1.1] conversation.py — 对话数据结构 + prompt 格式化
  [1.2] generator.py 改造 — 集成对话历史
  [1.3] app.py 改造 — 对话线程 UI

阶段 2（成本可见）
  [2.1] token_tracker.py — Token 统计模块
  [2.2] embedder.py 改造 — embedding token 提取
  [2.3] generator.py 改造 — generation token 提取
  [2.4] app.py 改造 — 统计面板 + 单次消耗展示

阶段 3（操作与反馈）
  [3.1] 复制按钮
  [3.2] 导出对话
  [3.3] 👍/👎 反馈按钮 + JSON 日志

阶段 4（测试）
  [4.1] conversation 模块测试
  [4.2] token_tracker 模块测试
  [4.3] 集成测试 + 回归验证
```

---

## 关键设计决策

### 为什么对话历史存 session_state 而非数据库？

- Streamlit 本身是有状态服务，session_state 的生命周期匹配用户会话
- 引入数据库（SQLite/Redis）需要额外依赖、初始化逻辑、迁移管理
- 面试时讲清楚「当前场景用 session_state，生产环境换 Redis」的选型依据

### 为什么不用 LangChain 的 ConversationBufferMemory？

- LangChain 的 ConversationBufferMemory 是通用方案，带来了不必要的抽象
- 自己实现对话历史管理代码量不超过 60 行，更容易理解和定制
- 面试时可以展示对 prompt 工程的理解：怎么把历史对话注入 prompt

### Token 统计算法

DashScope API 响应中 `output.usage` 字段包含 token 信息：
```python
# TextEmbedding 响应
resp.usage.input_tokens

# Generation 响应
resp.usage.input_tokens
resp.usage.output_tokens
```

如果 API 未返回 usage（旧版本 SDK），降级为估算：
- 中文: len(text) / 2 ≈ token 数
- 英文: len(text) / 4 ≈ token 数
```

### 为什么不做 Query 改写（P1 范围外）

Query 改写需要用 LLM 在检索前改写用户问题（如「它有什么优点」→「RAG 有什么优点」）。这引入了额外的 LLM 调用（增加的延迟和成本），且需要验证改写质量。

P1 先通过对话历史解决上下文问题，Query 改写作为 P2 的检索优化项。

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| DashScope 不返回 usage 信息 | 优雅降级，估算 token 数或显示「未知」 |
| 对话历史过长超过 LLM 上下文 | 限制最近 5 轮，超出截断 |
| Streamlit session_state 在 rerun 时丢失 | conversation 对象需在 session_state 中初始化并持久化 |
| 复制按钮在 Streamlit 中交互受限 | 使用 st.code 自带复制或 HTML + JS 方案 |
