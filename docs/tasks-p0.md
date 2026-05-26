# Tasks: P0 加固阶段任务清单

> 按依赖拓扑排序，每个任务有明确的验收标准。

---

## 阶段 1: 基础设施

### Task 1.1 — requirements.txt 版本锁定

**依赖**: 无  
**涉及文件**: `requirements.txt`  
**预估**: 15 分钟

**步骤**:
1. 查询当前安装的版本: `pip freeze | grep -E "streamlit|chromadb|pymupdf|langchain|dashscope|jieba|rank-bm25"`
2. 将 `requirements.txt` 改为版本范围格式（如 `streamlit>=1.32,<2`）
3. 同时创建 `requirements-dev.txt` 包含 pytest 及开发工具

**验收**:
- [ ] `pip install -r requirements.txt` 在新环境中可成功安装
- [ ] 版本号锁定为语义化主版本范围

---

### Task 1.2 — 日志系统

**依赖**: 无  
**涉及文件**: 新增 `src/logging_config.py`，修改 `src/config.py`  
**预估**: 30 分钟

**步骤**:
1. 创建 `src/logging_config.py`
   - `setup_logging(level)` — 配置 StreamHandler + RotatingFileHandler
   - `get_logger(name)` — 返回 `logging.getLogger(f"rag.{name}")`
2. 在 `config.py` 中添加 `LOG_LEVEL` 和 `LOG_DIR` 配置
3. 在 `app.py` 启动时调用 `setup_logging()`

**验收**:
- [ ] 日志文件生成在 `data/logs/app.log`
- [ ] 格式: `2026-05-23 14:30:01 [INFO] rag.app: 应用启动`
- [ ] `LOG_LEVEL=DEBUG` 时输出 DEBUG 级别日志

---

### Task 1.3 — 安全校验模块

**依赖**: 无  
**涉及文件**: 新增 `src/security.py`  
**预估**: 45 分钟

**步骤**:
1. 创建 `src/security.py`
   - `validate_question(text)` — 长度检查 + HTML 转义
   - `validate_kb_name(name)` — 正则校验
   - `validate_file_extension(filename)` — 后缀白名单
   - `RateLimiter` 类 — 内存滑动窗口
2. 单元测试（跟着写，不等最后）

**验收**:
- [ ] `<script>alert(1)</script>` → `&lt;script&gt;alert(1)&lt;/script&gt;`
- [ ] 知识库名 `技术文档_v2` 合法，`知识库!!!` 不合法
- [ ] 文件名 `test.exe` 被拒绝，`test.pdf` 通过
- [ ] RateLimiter 第 21 次请求返回 False，61 秒后恢复

---

## 阶段 2: 核心防护

### Task 2.1 — API 重试机制

**依赖**: Task 1.2（日志）  
**涉及文件**: 新增 `src/retry.py`  
**预估**: 45 分钟

**步骤**:
1. 创建 `src/retry.py`
   - `is_retryable(exception)` — 判断异常是否可重试
   - `retry_call(func, *args, max_retries, base_delay, **kwargs)` — 核心重试逻辑
2. 编写 retry 模块的 mock 测试

**验收**:
- [ ] 模拟抛出 ConnectionError → 重试 3 次后抛出 RuntimeError
- [ ] 模拟抛出 ValueError → 立即抛出，不重试
- [ ] 重试日志: `WARNING: 重试 1/3，等待 2.0 秒后重试...`

---

### Task 2.2 — embedder 集成重试 + 日志

**依赖**: Task 2.1  
**涉及文件**: `src/embedder.py`  
**预估**: 30 分钟

**步骤**:
1. 将 `TextEmbedding.call()` 包装在 `retry_call()` 中
2. 添加日志: embedding 开始、每批完成、全部完成、错误
3. 将 `print` 替换为 `logger`

**验收**:
- [ ] embedding 处理日志: `开始生成向量，共 35 个文本块，分 2 批处理`
- [ ] 每批完成日志含批号、耗时
- [ ] API 临时故障时自动重试，重试恢复后正常完成

---

### Task 2.3 — generator 集成重试 + 日志

**依赖**: Task 2.1  
**涉及文件**: `src/generator.py`  
**预估**: 30 分钟

**步骤**:
1. 将 `Generation.call()` 包装在 `retry_call()` 中
2. 添加日志: 生成开始、耗时、token 数（如有）
3. 将 `print` 替换为 `logger`

**验收**:
- [ ] 生成日志: `开始生成回答，prompt 长度 1234 字符`
- [ ] 生成完成日志: `回答生成完成，耗时 2.3 秒`
- [ ] API 临时故障时自动重试

---

## 阶段 3: UI 防护

### Task 3.1 — app.py 集成安全 + 速率限制

**依赖**: Task 1.2, 1.3  
**涉及文件**: `src/app.py`  
**预估**: 45 分钟

**步骤**:
1. 提问输入框调用 `validate_question()` 校验
2. 知识库创建/重命名调用 `validate_kb_name()` 校验
3. 文件上传调用 `validate_file_extension()` 校验
4. 提问按钮绑定 `RateLimiter.check()` 
5. 违规时用 `st.warning/error` 提示用户
6. 回答渲染使用 `st.markdown` 确保不执行脚本

**验收**:
- [ ] 输入超 2000 字符时显示错误提示，不发起 API 调用
- [ ] 创建知识库名包含 `!!!` 时被拒绝，提示允许的字符
- [ ] 上传 `.exe` 文件被拒绝
- [ ] 高频提问时显示 "请求过于频繁，请稍后重试"
- [ ] 用户输入 `<b>test</b>` 显示为纯文本而非粗体

---

## 阶段 4: 部署

### Task 4.1 — Dockerfile

**依赖**: Task 2.2, 2.3（确保代码可运行）  
**涉及文件**: 新增 `Dockerfile`  
**预估**: 30 分钟

**步骤**:
1. 编写 Dockerfile（python:3.12-slim base）
2. 安装系统依赖、Python 依赖
3. 创建非 root 用户
4. 设置 ENTRYPOINT 启动 Streamlit

**验收**:
- [ ] `docker build -t rag-app .` 构建成功
- [ ] `docker run -p 8501:8501 -e DASHSCOPE_API_KEY=xxx rag-app` 可访问

---

### Task 4.2 — docker-compose.yml

**依赖**: Task 4.1  
**涉及文件**: 新增 `docker-compose.yml`  
**预估**: 15 分钟

**步骤**:
1. 定义 app 服务
2. 挂载 `data/` 目录（持久化 ChromaDB + BM25 + 日志）
3. 读取 `.env` 注入环境变量

**验收**:
- [ ] `docker-compose up` 一键启动
- [ ] 上传 PDF、创建知识库后 stop + start，数据保留

---

### Task 4.3 — .dockerignore

**依赖**: Task 4.1  
**涉及文件**: 新增 `.dockerignore`  
**预估**: 5 分钟

**验收**:
- [ ] `data/`、`env/`、`.git/`、`__pycache__/` 未打入镜像

---

## 阶段 5: 测试

### Task 5.1 — Mock 测试（embedder + generator + retriever）

**依赖**: Task 2.2, 2.3  
**涉及文件**: `tests/test_rag.py`  
**预估**: 60 分钟

**步骤**:
1. `test_embed_batch_success` — mock `TextEmbedding.call`，验证返回
2. `test_embed_batch_splits` — 35 条输入分 2 批
3. `test_search_returns_correct_structure` — mock ChromaDB query
4. `test_generate_stream_yields` — mock `Generation.call` stream response
5. `test_generate_empty_docs` — 空 docs 的行为
6. `test_retrieve_calls_both_sources` — mock embedder + BM25
7. `test_rrf_dedup` — 相同内容去重

**验收**:
- [ ] 断网后 `pytest tests/ -v -k "mock or Mock"` 全部通过
- [ ] mock 中验证了 API 调用参数（如 model 名称、input 格式）

---

### Task 5.2 — 安全模块测试

**依赖**: Task 1.3  
**涉及文件**: `tests/test_rag.py`  
**预估**: 30 分钟

**步骤**:
1. 4 个 validate 函数测试（正常 + 边界 + 异常）
2. RateLimiter 功能测试 + 窗口重置测试

**验收**:
- [ ] 安全模块测试覆盖所有校验规则

---

### Task 5.3 — 重试模块测试

**依赖**: Task 2.1  
**涉及文件**: `tests/test_rag.py`  
**预估**: 30 分钟

**步骤**:
1. 成功不重试
2. 一次失败后恢复
3. 全部失败抛出
4. 4xx 不重试
5. 最大重试次数可配置

**验收**:
- [ ] 重试模块测试全部通过

---

### Task 5.4 — 全量回归 + Docker 验证

**依赖**: Task 5.1, 5.2, 5.3  
**预估**: 20 分钟

**步骤**:
1. `pytest tests/ -v` 确保所有测试通过
2. `docker-compose up` 启动
3. 手动验证: 上传 PDF → 提问 → 查看引用 → 检查日志文件
4. 验证错误场景: 故意写错 API key → 观察重试日志

**验收**:
- [ ] `pytest tests/ -v` 全部通过（断网）
- [ ] docker 环境功能正常
- [ ] 错误场景展示友好提示而非 traceback

---

## 总预估

| 阶段 | 任务数 | 预估时间 |
|------|--------|----------|
| 阶段 1: 基础设施 | 3 | 1.5 小时 |
| 阶段 2: 核心防护 | 3 | 1.75 小时 |
| 阶段 3: UI 防护 | 1 | 0.75 小时 |
| 阶段 4: 部署 | 3 | 0.75 小时 |
| 阶段 5: 测试 | 4 | 2.5 小时 |
| **合计** | **14** | **约 7 小时** |

> 按每天 2 小时投入，约 3-4 天完成。
