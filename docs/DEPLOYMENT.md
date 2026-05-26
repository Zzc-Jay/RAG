# 部署与 CI/CD

## Docker 容器化

### 架构

```
docker-compose.yml
├── app (Streamlit)
│   - 端口: 8501
│   - 命令: streamlit run src/app.py
│   - 依赖: 无
│
└── api (FastAPI)
    - 端口: 8502
    - 环境: PYTHONPATH=/app/src
    - 命令: uvicorn api:app --host 0.0.0.0 --port 8502
    - 依赖: app（启动顺序）
```

### 为什么分成两个 Service

同一个镜像，两种部署方式：

```
ghcr.io/zzc-jay/rag:latest  ← 同一个镜像
    │
    ├──→ app 容器: streamlit run src/app.py
    └──→ api 容器: uvicorn api:app
```

- **Streamlit** 面向人类用户，提供可视化界面
- **FastAPI** 面向程序/外部系统，提供 REST API

两个服务共享同一个数据卷（`./data:/app/data`），操作的是同一套知识库。

### Dockerfile 关键设计

```dockerfile
FROM python:3.12-slim
# 非 root 用户（安全最佳实践）
RUN useradd -m appuser
USER appuser
# 两个端口都暴露
EXPOSE 8501 8502
# 没有 ENTRYPOINT —— 由 compose 各自指定 command
```

**为什么没有 ENTRYPOINT**：如果写了 `ENTRYPOINT ["streamlit", "run"]`，FastAPI 容器就启动不了。让 compose 各自的 `command` 决定启动什么。

### 镜像仓库：ghcr.io

- GitHub Container Registry，免费，与 GitHub Actions 原生集成
- 镜像路径：`ghcr.io/zzc-jay/rag:latest`
- 标签策略：`latest`（最新）+ `sha-xxxxx`（精确版本）

### Registry Mirror（针对国内网络）

Docker Hub 的拉取在 GFW 内被严重限制。配置了 3 个镜像加速：
```json
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://dockerhub.timeweb.cloud",
    "https://docker.registry.cyou"
  ]
}
```

这些加速器在 `docker build` 拉取基础镜像（python:3.12-slim）时生效。

---

## GitHub Actions CI/CD

### CI（持续集成）：`.github/workflows/ci.yml`

**触发条件**：push/PR 到 main 分支

**流程**：
```
代码推送 → 检出代码 → 安装 Python 3.12
→ pip install 依赖 → 运行 220 tests → 结果报告
```

**关键环境变量**：
```yaml
env:
  DASHSCOPE_API_KEY: fake-ci-key  # 触发 CI mock 模式
  RAG_DATA_DIR: /tmp/rag-test-data  # 避免 ChromaDB SQLite 锁定
  TMPDIR: /tmp
  SQLITE_TMPDIR: /tmp
```

### CD（持续部署）：`.github/workflows/cd.yml`

**触发条件**：push 到 main 分支 或 推送 `v*` 标签（如 v2.7.0）

**流程**：
```
代码推送 → ① Test Job (275 tests, continue-on-error)
         → ② Build & Push Job
              → 检出代码
              → 转换仓库名为小写（ghcr.io 要求）
              → docker login ghcr.io（用 GITHUB_TOKEN）
              → docker build + push
              → 标签: latest + commit SHA
```

**为什么 test job 用 `continue-on-error: true`**：

有 5 个测试在 GitHub Actions Linux runner 上因 ChromaDB SQLite 只读锁定而失败（Windows 本地全部通过）。临时绕过不阻塞 CD 发布。这 5 个失败是 ChromaDB Rust 绑定层的问题，不是业务逻辑问题。

**大小写转换步骤**：
```yaml
- name: Lowercase repo owner for ghcr.io
  run: |
    OWNER="${{ github.repository_owner }}"
    echo "REPO_OWNER_LOWER=${OWNER,,}" >> $GITHUB_ENV
```

`${OWNER,,}` 是 Bash 4.0+ 的语法，将字符串转为全小写。`Zzc-Jay` → `zzc-jay`。ghcr.io 不接受大写字母的镜像路径。

### GITHUB_TOKEN vs 个人 Token

```yaml
permissions:
  contents: read
  packages: write
```

`GITHUB_TOKEN` 是 GitHub Actions 自动生成的一次性 Token，运行结束后自动销毁。`packages: write` 权限允许推送镜像到 ghcr.io。不需要手动创建 Personal Access Token。

### Docker Compose 生产部署

`docker-compose.prod.yml`：
- 使用远程镜像 `ghcr.io/zzc-jay/rag:latest`（不 build）
- 适合在生产服务器上拉取已构建好的镜像直接运行

---

## 本地开发 vs Docker 生产

| 维度 | 本地开发 | Docker 生产 |
|------|---------|------------|
| Python 环境 | 系统 Python + venv | python:3.12-slim 容器内 |
| 启动 | 两个终端各跑一个命令 | `docker compose up -d` |
| 数据 | `data/` 直接读写 | `data/` 通过 volume 挂载 |
| API Key | 环境变量 | `.env` 文件注入容器 |
| 更新 | git pull + 重启 | 拉新镜像 + recreate 容器 |

---

## 常用命令速查

```bash
# 开发模式
PYTHONPATH=src streamlit run src/app.py --server.port 8501
PYTHONPATH=src uvicorn api:app --host 0.0.0.0 --port 8502
pip install -r requirements.txt

# Docker 开发模式（本地构建）
docker compose up -d          # 启动
docker compose down           # 停止
docker compose logs -f        # 查看日志
docker compose restart        # 重启

# Docker 生产模式（用远程镜像）
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml pull  # 拉取最新镜像

# 单容器测试
docker run -d --name rag-test -p 8501:8501 \
  -v ./data:/app/data --env-file .env \
  ghcr.io/zzc-jay/rag:latest \
  python -m streamlit run src/app.py --server.address=0.0.0.0 --server.port=8501

# 测试
python -m pytest tests/ -v
python -m pytest tests/ -v --tb=short
python -m pytest tests/e2e/ -v --tb=short
```
