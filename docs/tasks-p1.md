# Tasks: P1 打磨阶段任务清单

> 4 个阶段，12 个任务，预估 ~8 小时。

---

## 阶段 1: 对话核心

### Task 1.1 — conversation.py 对话历史模块

**依赖**: 无  
**涉及文件**: 新增 `src/conversation.py`  
**预估**: 45 分钟

**步骤**:
1. 定义 `Turn` 和 `Conversation` 数据结构
2. 实现 `add_turn()`、`get_history()`、`format_for_prompt()`
3. 实现 `estimate_tokens()` 粗略估算函数
4. 实现 `should_truncate()` 判断是否需要截断

**验收**:
- [ ] `format_for_prompt()` 输出格式正确
- [ ] `get_history(conv, 3)` 在 5 轮对话中只返回最近 3 轮
- [ ] `estimate_tokens("你好世界")` 返回合理值 (~2)

---

### Task 1.2 — generator.py 集成对话历史

**依赖**: Task 1.1  
**涉及文件**: `src/generator.py`  
**预估**: 30 分钟

**步骤**:
1. `build_prompt()` 改为 `build_prompt(query, docs, history_text=None)`
2. 新增 `build_multi_turn_prompt()` 拼接历史 + 参考资料 + 当前问题
3. `generate_stream()` 接收 `history` 可选参数

**验收**:
- [ ] 无历史时 prompt 格式与原来一致（不回归）
- [ ] 有历史时 prompt 包含「对话历史」区块
- [ ] 现有测试全部通过

---

### Task 1.3 — app.py 对话线程 UI

**依赖**: Task 1.2  
**涉及文件**: `src/app.py`  
**预估**: 60 分钟

**步骤**:
1. 用 `st.session_state.conversation` 替代单个 query_submitted
2. UI 改为对话线程：渲染历史 Q&A + 当前回答
3. 新提问时追加到 conversation，非覆盖
4. 切换知识库时清空对话
5. 添加「清空对话」按钮

**验收**:
- [ ] 连续 3 轮提问，UI 显示完整对话线程
- [ ] 「它有什么优点」能正确理解「它」指代前文
- [ ] 切换知识库后对话历史清空
- [ ] 清空按钮可重置对话

---

## 阶段 2: 成本可见

### Task 2.1 — token_tracker.py Token 统计模块

**依赖**: 无  
**涉及文件**: 新增 `src/token_tracker.py`  
**预估**: 30 分钟

**步骤**:
1. 定义 `TokenUsage` 数据结构
2. 实现 `TokenTracker` 类（累计 + 费用计算）
3. 实现 `format_cost()` 费用格式化
4. 实现 `summary` 属性

**验收**:
- [ ] `record_embedding(1000)` 后 total_tokens == 1000
- [ ] `record_generation(500, 300)` 后 total_tokens == 1800
- [ ] estimated_cost 计算公式正确

---

### Task 2.2 — embedder.py 提取 embedding token

**依赖**: Task 2.1  
**涉及文件**: `src/embedder.py`  
**预估**: 20 分钟

**步骤**:
1. `_call_embedding_api()` 返回 (embeddings, token_count)
2. 从 DashScope TextEmbedding 响应中提取 `usage.input_tokens`
3. 无 usage 时返回 token_count=0（优雅降级）

**验收**:
- [ ] embedding 响应含 usage 时正确提取 token 数
- [ ] embedding 响应无 usage 时不崩溃

---

### Task 2.3 — generator.py 提取 generation token

**依赖**: Task 2.1  
**涉及文件**: `src/generator.py`  
**预估**: 20 分钟

**步骤**:
1. `generate_stream()` 收集 usage 信息
2. 从 DashScope Generation 流式响应的最后一个 chunk 提取 usage
3. 流式生成完成后返回 token 统计

**验收**:
- [ ] generation 完成后能获取 input_tokens 和 output_tokens
- [ ] 无 usage 时不崩溃

---

### Task 2.4 — app.py 统计面板 + 单次消耗

**依赖**: Task 2.2, 2.3  
**涉及文件**: `src/app.py`  
**预估**: 30 分钟

**步骤**:
1. 每次回答下方显示 token 消耗行
2. 侧边栏底部显示 session 累计统计
3. 初始化 `st.session_state.token_tracker`

**验收**:
- [ ] 回答下方显示本次 token 消耗和费用
- [ ] 侧边栏累计统计随提问递增
- [ ] UI 上消耗显示格式: "📊 输入 500 · 输出 300 tokens · 💰 ≈¥0.0012"

---

## 阶段 3: 操作与反馈

### Task 3.1 — 复制按钮

**依赖**: Task 1.3  
**涉及文件**: `src/app.py`  
**预估**: 20 分钟

**步骤**:
1. 每轮回答旁添加复制按钮
2. 使用 `st.code(answer_text)` 自带的复制功能，或
3. 自定义 HTML + JS 实现 clipboard 复制

**验收**:
- [ ] 点击复制后剪贴板包含完整回答文本
- [ ] 复制成功有用户可感知的反馈

---

### Task 3.2 — 导出对话

**依赖**: Task 1.3  
**涉及文件**: `src/app.py`  
**预估**: 20 分钟

**步骤**:
1. 生成 Markdown 格式的对话记录
2. 使用 `st.download_button` 触发下载

**验收**:
- [ ] 导出的 .md 文件包含所有轮次的问题/回答/引用/时间
- [ ] 可在任意 Markdown 阅读器中正常查看

---

### Task 3.3 — 👍/👎 反馈按钮

**依赖**: Task 1.3  
**涉及文件**: `src/app.py`, `data/feedback.jsonl`  
**预估**: 25 分钟

**步骤**:
1. 每轮回答下方添加 👍/👎 按钮
2. 点击后记录到 session_state（防重复点击）
3. 追加写入 `data/feedback.jsonl`

**验收**:
- [ ] 点击 👍 后按钮变色，视觉确认
- [ ] 再次点击可取消
- [ ] feedback.jsonl 中记录包含问题、回答、引用、时间戳
- [ ] JSONL 每行一条有效 JSON

---

## 阶段 4: 测试

### Task 4.1 — conversation 模块测试

**依赖**: Task 1.1  
**涉及文件**: `tests/test_rag.py`  
**预估**: 20 分钟

**验收**:
- [ ] `add_turn` 正确追加
- [ ] `get_history` 限制轮数
- [ ] `format_for_prompt` 格式正确
- [ ] `estimate_tokens` 边界情况

---

### Task 4.2 — token_tracker 模块测试

**依赖**: Task 2.1  
**涉及文件**: `tests/test_rag.py`  
**预估**: 15 分钟

**验收**:
- [ ] 累计统计正确
- [ ] 费用计算精度
- [ ] 零 token 时的边界行为

---

### Task 4.3 — 全量回归

**依赖**: 所有任务  
**预估**: 15 分钟

**验收**:
- [ ] `pytest tests/ -v` 全部通过
- [ ] 手动测试多轮对话功能
- [ ] 手动测试 token 统计面板

---

## 总预估

| 阶段 | 任务数 | 预估时间 |
|------|--------|----------|
| 阶段 1: 对话核心 | 3 | 2.25 小时 |
| 阶段 2: 成本可见 | 4 | 1.75 小时 |
| 阶段 3: 操作与反馈 | 3 | 1 小时 |
| 阶段 4: 测试 | 3 | 0.75 小时 |
| **合计** | **13** | **约 6 小时** |
