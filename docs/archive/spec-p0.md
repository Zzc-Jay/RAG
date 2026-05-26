# Spec: RAG V2 → 加固阶段 (P0)

> 目标：将当前 Demo 级系统加固为**生产可部署、面试可展示**的工程化项目。

---

## Objective

补齐安全、稳定性、可部署三大维度的基础设施。不改动现有功能逻辑，只在关键节点增加防护层。

### 用户故事

1. **依赖可复现** — 新人 clone 代码后 `pip install -r requirements.txt` 能精确复现环境，不会因依赖升级导致不可用
2. **一键部署** — `docker-compose up` 就能跑起来，不需要手动配置 Python 环境
3. **API 容错** — DashScope 临时故障时自动重试，不会一次失败就崩溃
4. **安全防护** — 恶意输入被过滤，大文件被拒绝，高频请求被限制
5. **测试可信** — 不依赖外部 API 也能跑测试，CI 环境可验证

### Success Criteria

- [ ] `docker-compose up` 一键启动，浏览器访问 `http://localhost:8501` 功能完整
- [ ] DashScope API 调用失败后自动重试（最多 3 次，指数退避），重试耗尽后显示友好错误
- [ ] 用户输入含 `<script>alert(1)</script>` 时不执行，以纯文本显示
- [ ] 单文件上传超过 50MB 时被拒绝
- [ ] 同一 session 1 分钟内提问超过 20 次时触发速率限制提示
- [ ] `pytest tests/ -v` 在无网络环境下也能全部通过（API 调用已 mock）
- [ ] 所有日志输出包含时间戳、级别、模块名，可追踪问题

---

## P0-1: API 重试机制

### 现状问题

`embedder.py` 和 `generator.py` 直接调用 DashScope API，无任何容错处理。网络抖动、API 限流、临时故障都会直接抛异常并展示给用户。

### 需求

| ID | 需求 | 优先级 |
|----|------|--------|
| R1 | 所有 DashScope API 调用支持自动重试 | must |
| R2 | 重试策略：指数退避（1s → 2s → 4s），最多 3 次 | must |
| R3 | 仅对可恢复错误重试（429 限流、5xx 服务端错误、网络超时），4xx 客户端错误不重试 | must |
| R4 | 重试耗尽后抛出明确错误信息（含原始错误原因） | must |
| R5 | 重试过程记录 warning 日志 | should |
| R6 | 最大重试次数和退避系数可配置 | should |

### 涉及模块

- `src/embedder.py` — `TextEmbedding.call()`
- `src/generator.py` — `Generation.call()`
- 新增 `src/retry.py` — 通用重试装饰器/函数

---

## P0-2: 输入校验与安全防护

### 现状问题

用户输入直接传给后端处理，无任何校验。攻击者可上传恶意文件、注入脚本、高频调用耗尽 API 配额。

### 需求

| ID | 需求 | 优先级 |
|----|------|--------|
| S1 | 问题输入长度限制：最大 2000 字符 | must |
| S2 | 知识库名称限制：最大 50 字符，仅允许中英文、数字、下划线、短横线 | must |
| S3 | 文件上传类型白名单：仅 .pdf / .txt / .docx | must |
| S4 | 文件上传大小限制：单文件最大 50MB | must |
| S5 | 文件内容 MIME type 校验（magic number），防止改后缀绕过 | should |
| S6 | XSS 防护：用户输入在渲染前做 HTML 实体转义 | must |
| S7 | 速率限制：同一 session 每分钟最多 20 次提问 | should |
| S8 | 违规操作返回明确提示，不静默忽略 | must |

### 涉及模块

- `src/app.py` — Streamlit 侧输入校验 + 渲染安全
- 新增 `src/security.py` — 校验函数 + 速率限制器

---

## P0-3: 日志系统

### 现状问题

项目使用 `st.info/warning/error` 和 `print()` 输出信息，无持久化日志，问题发生后无法回溯排查。

### 需求

| ID | 需求 | 优先级 |
|----|------|--------|
| L1 | 使用 Python 标准 `logging` 模块替代 `print` | must |
| L2 | 日志格式：`时间戳 - 模块名 - 级别 - 消息`，包含完整 traceback | must |
| L3 | 同时输出到控制台和 `data/logs/app.log` 文件 | must |
| L4 | 日志文件按天轮转，保留最近 7 天 | should |
| L5 | API 调用记录请求耗时和结果状态（成功/失败/重试次数） | should |
| L6 | 日志级别通过环境变量 `LOG_LEVEL` 控制，默认 INFO | should |

### 涉及模块

- 新增 `src/logging_config.py` — 日志初始化，全局复用
- 所有 `src/*.py` — 将 `print` 替换为 `logger.info/warning/error`

---

## P0-4: Docker 容器化

### 现状问题

部署依赖手动配置 Python 虚拟环境、安装依赖、设置环境变量。无法在另一台机器上快速复现。

### 需求

| ID | 需求 | 优先级 |
|----|------|--------|
| D1 | `Dockerfile` 基于 `python:3.12-slim`，安装依赖后暴露 8501 端口 | must |
| D2 | `docker-compose.yml` 定义 app 服务，挂载 `data/` 目录持久化 | must |
| D3 | `.dockerignore` 排除 `__pycache__/`、`.env`、`data/`、`.git/` | must |
| D4 | 环境变量 `DASHSCOPE_API_KEY` 通过 `.env` 文件注入，不写入镜像 | must |
| D5 | 容器内以非 root 用户运行 | should |

### 涉及文件

- `Dockerfile`（项目根目录）
- `docker-compose.yml`（项目根目录）
- `.dockerignore`（项目根目录）

---

## P0-5: Mock 测试覆盖

### 现状问题

18 个测试中，与 DashScope API 相关的模块（embedder、generator）没有 mock，测试依赖真实 API 调用。CI 环境无 API key 无法运行。

### 需求

| ID | 需求 | 优先级 |
|----|------|--------|
| T1 | `embedder` 测试：mock `dashscope.TextEmbedding.call`，验证分批逻辑、upsert 参数 | must |
| T2 | `generator` 测试：mock `dashscope.Generation.call`，验证 prompt 构建、流式 token 处理 | must |
| T3 | `retriever` 测试：mock embedder 和 bm25 两路，验证 RRF 融合逻辑 | must |
| T4 | 全量测试在无网络环境下 `pytest tests/ -v` 全部通过 | must |
| T5 | 新增错误场景测试：重试耗尽后异常包含有效信息 | should |

### 涉及模块

- `tests/test_rag.py` — 增强，补充 mock 测试

---

## P0-6: 依赖版本锁定

### 现状问题

`requirements.txt` 只有包名无版本号，不同时间安装的依赖版本可能不兼容。

### 需求

| ID | 需求 | 优先级 |
|----|------|--------|
| V1 | 所有直接依赖锁定主版本号（如 `streamlit>=1.32,<2`） | must |
| V2 | `pip freeze` 生成完整依赖版本快照供参考 | should |

### 涉及文件

- `requirements.txt`

---

## Out of Scope (P0 不包含)

| 项目 | 原因 |
|------|------|
| 用户认证 / 多租户 | 体量大，需要数据库 + session 管理，放入 P1+ |
| 数据备份与恢复 | ChromaDB 文件级备份可用 docker volume 解决，暂不单独实现 |
| REST API | 需要 FastAPI/Flask 重构，放入 P1+ |
| CI/CD 流水线 | 需要 GitHub repo，先有 Dockerfile 为 CI 打基础 |
| Embedding 缓存 | 需要引入缓存层（Redis/disk），放入 P1+ |

---

## Tech Stack (P0 无新增依赖)

| 组件 | 选型 | 说明 |
|------|------|------|
| 重试机制 | 自实现 + `time.sleep` | 依赖少，面试可讲原理 |
| HTTP 重试判断 | `dashscope.api_base` 异常类型 | 复用现有 SDK |
| 速率限制 | 内存字典 + `time` 模块 | Streamlit session 级别 |
| 日志 | `logging` 标准库 | 零依赖 |
| 容器化 | Docker + Docker Compose | - |
| Mock 测试 | `unittest.mock` 标准库 | 不用额外安装 |

---

## 文件变更总览

```
新增:
  src/retry.py           # 通用重试函数
  src/security.py        # 输入校验 + 速率限制
  src/logging_config.py  # 日志初始化
  Dockerfile
  docker-compose.yml
  .dockerignore

修改:
  src/config.py          # 新增重试/安全/日志相关配置
  src/embedder.py        # 集成重试 + 日志
  src/generator.py       # 集成重试 + 日志
  src/app.py             # 集成校验 + 安全管理 + 日志
  其他 src/*.py          # print → logger
  requirements.txt       # 版本锁定
  tests/test_rag.py      # 补充 mock 测试
```
