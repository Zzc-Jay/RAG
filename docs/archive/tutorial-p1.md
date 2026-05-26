# P1 打磨教学文档 — 多轮对话、Token 统计、用户反馈

> 本文档解释 P1 打磨阶段每个子系统的设计原理和面试要点。

---

## 目录

1. [多轮对话：为什么不是简单的「记住上一句」](#1-多轮对话)
2. [Token 统计：成本可观测性](#2-token-统计)
3. [用户反馈：检索质量的隐形基础设施](#3-用户反馈)
4. [面试速查](#4-面试速查)

---

## 1. 多轮对话

### 1.1 问题

单轮 RAG 的典型失败场景：

```
用户: "什么是 RAG？"
系统: [检索 + 生成] RAG 是检索增强生成技术...

用户: "它有什么优点？"    ← 「它」指 RAG
系统: [检索] 关键词「它」「优点」... → 找不到相关内容 ❌
```

原因：系统不知道「它」指什么。每次提问独立处理，没有记忆。

### 1.2 解决方案：Prompt 注入历史

思路很简单：**把前面的对话作为上下文，和当前问题一起发给 LLM**。

```
旧 Prompt:
  参考资料 + 当前问题 + 回答

新 Prompt:
  对话历史（最近 N 轮）+ 参考资料 + 当前问题 + 回答
```

具体实现：

```python
# conversation.py
def format_for_prompt(turns):
    """对话历史 → prompt 文本"""
    lines = ["对话历史："]
    for turn in turns[:-1]:  # 不包含当前轮
        lines.append(f"用户：{turn['question']}")
        lines.append(f"助手：{turn['answer']}")
    return "\n".join(lines)
```

注入后的 prompt：

```
对话历史：
用户：什么是 RAG？
助手：RAG 是检索增强生成技术，它结合了信息检索和文本生成。

---
请根据以下参考资料回答问题...
参考资料：
[1] (来源: ai.pdf, 第3页) RAG has several advantages...

当前问题：它有什么优点？
回答：
```

LLM 看到「对话历史」+「它有什么优点」，自然能推断出「它」=「RAG」。

### 1.3 窗口管理

不能把所有历史都注入，有两个限制：

1. **LLM 上下文窗口** — qwen-plus 有 131K 的窗口，但 prompt 越大越贵越慢
2. **相关性衰减** — 10 轮前的对话对当前问题帮助不大

所以限制最近 N 轮（默认 5 轮）：

```python
def get_history(conv, max_turns=5):
    """只返回最近 N 轮。"""
    return conv[-max_turns:]
```

### 1.4 为什么不用 LangChain 的 Memory？

LangChain 提供了 `ConversationBufferMemory`、`ConversationSummaryMemory` 等，但：

- 引入了不必要的抽象层，对 50 行代码的功能来说太重
- 面试时如果你说「我用 LangChain 的 Memory」，面试官就不知道该问你什么了
- 如果你说「我自己实现了滑动窗口对话管理」，面试官可以追问「窗口满了怎么办」「怎么处理 token 超限」

**自己实现的价值在于你有话可讲。**

### 1.5 面试追问

**Q: 对话历史太长怎么办？**

三种策略：
1. **截断**（当前实现）：只保留最近 N 轮，简单有效
2. **摘要**：用 LLM 把旧对话压缩成摘要（增加一次 API 调用，但保留更多信息）
3. **滑动窗口 + 向量检索**：把历史对话也存入向量库，检索时召回相关的历史片段（适合长对话场景）

**Q: 多轮对话下，检索策略要改吗？**

是的。理想情况下应该把当前问题 + 历史上下文一起用于检索。比如用户问「它有什么优点」，应该用「RAG 有什么优点」去检索。

这是 Query 改写（Query Rewriting）的范畴，属于 P2 的检索优化。

---

## 2. Token 统计

### 2.1 为什么重要

- **成本控制**：知道每个用户/每次对话花了多少钱
- **性能调优**：prompt 太长 → input tokens 多 → 优化 prompt 长度
- **面试亮点**：展示你对 LLM 应用成本模型的认知

### 2.2 实现方式

```python
class TokenTracker:
    def record_embedding(self, tokens: int): ...
    def record_generation(self, input_tokens: int, output_tokens: int): ...

    @property
    def estimated_cost(self):
        return (
            self.embedding_tokens / 1000 * 0.0005 +     # embedding
            self.generation_input / 1000 * 0.0008 +      # generation 输入
            self.generation_output / 1000 * 0.002        # generation 输出
        )
```

### 2.3 从 API 获取真实 token 数

DashScope API 响应中包含 usage 信息：

```python
# TextEmbedding
resp.usage.input_tokens  # → 1234

# Generation
resp.usage.input_tokens   # → 500
resp.usage.output_tokens  # → 300
```

**流式生成的坑**：DashScope 流式响应中，usage 信息可能在**最后一个 chunk**中才返回。当前实现对流式生成用字符数估算（`len(text) * 0.4`），非流式可以用精确值。

面试时诚实说明："当前流式生成用估算值，精确值需要解析最后一个 chunk 的 usage 字段。这是已知的 trade-off，生产环境中应该在流式结束后回填精确值。"

### 2.4 面试追问

**Q: 用户问「这个月花了多少钱」，你怎么回答？**

当前版本是 session 级别的统计，不跨 session。要做月度统计需要：
- 把每次调用的 token 记录写入数据库（SQLite/PostgreSQL）
- 按时间范围聚合查询

这是从「单机统计」到「持久化统计」的升级，架构上不难，但需要引入数据库。

---

## 3. 用户反馈

### 3.1 为什么重要

用户反馈是 RAG 系统**检索质量评估**的第一步。没有反馈数据，你永远不知道检索结果好不好。

常见的使用方式：
- 👍/👎 → 积累标注数据
- 用标注数据评估检索质量（Hit Rate、MRR）
- 用标注数据训练 reranker 或调检索参数

### 3.2 当前实现

```python
# 每轮回答存储反馈状态
{"question": "...", "answer": "...", "references": [...], "feedback": "up" | "down" | None}
```

### 3.3 面试追问

**Q: 有了反馈数据后，你怎么改进检索？**

1. **短期**：导出反馈数据，人工分析踩了的回答，找检索盲区
2. **中期**：用 👍/👎 作为 ground truth，评估不同检索策略（调整 chunk_size、top_k、RRF_K）
3. **长期**：用标注数据 fine-tune reranker 模型，或训练一个「答案质量预测器」

---

## 4. 面试速查

### 多轮对话
> "我用的是滑动窗口策略：保留最近 5 轮对话，通过 prompt 注入历史上下文。这比 LangChain 的 Memory 更轻量，且每个设计决策我都能讲清楚。窗口满了就截断最旧的，如果以后需要更长的记忆，可以加摘要压缩或向量检索历史。"

### Token 统计
> "实现了 session 级的 TokenTracker，分别统计 embedding 和 generation 的 token 用量，按 DashScope 官方定价估算费用。流式生成用字符估算，非流式从 API response 提取精确值。如果要跨 session 统计，加一个 SQLite 表存每次调用的用量就行。"

### 用户反馈
> "每轮回答下面有 👍/👎 按钮，反馈数据可以用于检索质量评估。这是 RAG 系统持续优化的基础设施——没有反馈数据，检索质量改进就是盲人摸象。"
