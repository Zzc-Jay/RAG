# ============================================================
# RAG 知识库问答系统 — Docker 镜像
# ============================================================
# 构建: docker build -t rag-app .
# 运行: docker-compose up

FROM python:3.12-slim

# 系统依赖: PyMuPDF 需要 libmupdf
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# 先复制依赖文件（利用 Docker 缓存层）
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY src/ ./src/

# 创建数据目录并设置权限
RUN mkdir -p /app/data/chroma_db /app/data/bm25 /app/data/logs \
    && chown -R appuser:appuser /app

# 非 root 运行
USER appuser

# Streamlit 端口
EXPOSE 8501

# 启动命令
ENTRYPOINT ["python", "-m", "streamlit", "run", "src/app.py", \
    "--server.address=0.0.0.0", "--server.port=8501", \
    "--browser.gatherUsageStats=false"]
