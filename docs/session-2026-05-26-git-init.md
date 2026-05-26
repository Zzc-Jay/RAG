# RAG 项目 Git 初始化 & GitHub 推送 — 操作记录与详解

> 日期：2026-05-26 | 会话目标：将 RAG 项目纳入 Git 版本控制并推送到 GitHub 公开仓库

---

## 第一部分：项目现状检查

### 1.1 检查项目目录

```bash
ls -la D:/ai/rag/
```

**含义**：列出 `D:/ai/rag/` 目录下的所有文件和子目录。`-la` 参数表示：
- `-l`：长格式，显示文件大小、修改时间、权限等详细信息
- `-a`：显示所有文件，包括以 `.` 开头的隐藏文件（如 `.gitignore`）

**目的**：确认 RAG 项目确实存在于 `D:/ai/rag/`，且包含预期的子目录（`src/`、`tests/`）和文件（`Dockerfile`、`requirements.txt`）。

---

### 1.2 列出来源文件结构

```bash
ls D:/ai/rag/src/
ls D:/ai/rag/tests/
```

**含义**：分别列出 `src/`（源代码）和 `tests/`（测试代码）目录的内容。

**目的**：核实项目模块完整，对照 memory 中记录的功能清单确认没有缺失文件。

---

### 1.3 确认 Docker 文件存在

```bash
ls D:/ai/rag/Dockerfile D:/ai/rag/docker-compose.yml
```

**含义**：用 `ls` 检查两个指定文件是否存在。如果文件存在则正常显示文件名，如果不存在会打印错误信息。`exit: $?` 打印上一条命令的退出码（0=成功，非0=失败）。

**目的**：验证 Docker 容器化相关文件是否就位。

---

### 1.4 运行全部测试

```bash
cd D:/ai/rag && PYTHONPATH=D:/ai/rag/src python -m pytest tests/ -v --tb=short 2>&1 | tail -80
```

**逐段解析**：
- `cd D:/ai/rag` — 切换当前目录到项目根目录
- `&&` — 逻辑与，前一条命令成功（退出码=0）才执行下一条
- `PYTHONPATH=D:/ai/rag/src` — 设置环境变量，将 `src/` 加入 Python 模块搜索路径，使得 `import config` 能找到 `src/config.py`
- `python -m pytest tests/` — 以模块方式运行 pytest，扫描 `tests/` 目录
- `-v` — verbose 模式，显示每个测试用例的名称和 PASS/FAIL 状态
- `--tb=short` — 失败时显示简短的 traceback（只显示关键行，不显示完整调用栈）
- `2>&1` — 将标准错误（stderr, 文件描述符2）重定向到标准输出（stdout, 文件描述符1），合并输出
- `| tail -80` — 管道，只取最后 80 行（因为 220 个测试输出很长，只看摘要）

**目的**：验证项目健壮性，220 个测试全部通过。

---

### 1.5 检查 Git 状态

```bash
git -C D:/ai/rag log --oneline -10
git -C D:/ai/rag status --short
```

**含义**：
- `-C D:/ai/rag` — 在指定目录执行 git 命令，无需先 cd（`-C` = `--chdir`）
- `log --oneline -10` — 显示最近 10 条提交，每条一行（`--oneline` 只显示短 hash + 提交信息第一行）
- `status --short` — 简洁状态输出，`??` 表示未跟踪文件，`M` 表示已修改

**目的**：确认项目此前没有 Git 历史，是全新纳入版本控制。

---

## 第二部分：环境和依赖安装

### 2.1 检查 GitHub CLI 是否安装

```bash
gh auth status
```

**含义**：`gh` 是 GitHub 官方命令行工具。`auth status` 检查当前登录状态，显示已认证的账号名和 GitHub 实例地址。

**结果**：`gh: command not found`——系统未安装。

---

### 2.2 安装 GitHub CLI

```bash
winget install --id GitHub.cli --accept-source-agreements --accept-package-agreements
```

**逐段解析**：
- `winget` — Windows Package Manager，Microsoft 官方的包管理器（类似 Linux 的 apt、macOS 的 brew）
- `install` — 安装指定软件包
- `--id GitHub.cli` — 通过唯一 ID 指定要安装的包（每个 winget 包有 id/name/moniker 三种标识）
- `--accept-source-agreements` — 自动接受 winget 源的许可协议，跳过交互确认
- `--accept-package-agreements` — 自动接受软件包本身的许可协议

**目的**：安装 `gh` 命令行工具，用于后续创建 GitHub 仓库和推送代码。

---

### 2.3 查找 gh.exe 安装路径

```bash
"C:/Program Files/GitHub CLI/gh.exe" auth status
```

**含义**：用完整路径调用刚安装的 gh。Windows 上 winget 安装的 GitHub CLI 默认路径是 `C:\Program Files\GitHub CLI\`。

**目的**：安装完成后，当前 shell 的 PATH 缓存还没有刷新，所以直接用全路径执行。前面不加路径直接写 `gh` 会报 `command not found`。

---

## 第三部分：网络配置（关键）

### 3.1 检测代理工具和端口

```bash
netstat -an | grep LISTENING | grep -E "127.0.0.1:(7890|7891|1080|10809|...)"
```

**逐段解析**：
- `netstat -an` — 显示所有网络连接和监听端口
  - `-a`：显示所有连接（包括 LISTENING/ESTABLISHED 等状态）
  - `-n`：以数字形式显示地址和端口（不尝试解析主机名，速度更快）
- `|` — 管道，将前一个命令的输出传给后一个命令
- `grep LISTENING` — 过滤出处于"监听中"状态的端口（即本地正在运行的服务）
- `grep -E "127.0.0.1:(7890|7891|...)"` — 用正则表达式匹配常见的代理软件端口：
  - `7890`：Clash HTTP 代理默认端口
  - `7891`：Clash SOCKS5 代理默认端口
  - `1080/10809`：V2Ray/Shadowsocks 常用端口
  - `-E`：扩展正则表达式模式

```bash
tasklist | grep -iE "clash|v2ray|ssr|...|trojan|hysteria"
```

**含义**：
- `tasklist` — Windows 命令，列出正在运行的所有进程
- `grep -iE` — `-i` 忽略大小写，`-E` 扩展正则，匹配常见代理软件进程名

**结果**：发现 `Clash Party.exe` 进程监听 `127.0.0.1:7890` 和 `127.0.0.1:7891`。

---

### 3.2 配置 Git 代理

```bash
git config --global http.proxy http://127.0.0.1:7890
git config --global https.proxy http://127.0.0.1:7890
```

**逐段解析**：
- `git config --global` — 修改 Git 的全局配置（写入 `~/.gitconfig`），对所有仓库生效
- `http.proxy` — 指定 HTTP 协议的代理地址。Git 通过 HTTP/HTTPS 与远程仓库通信时，请求会先发送到这个代理
- `https.proxy` — 指定 HTTPS 协议的代理地址（与 http.proxy 地址相同，因为 Clash 同时处理 HTTP 和 HTTPS 流量）

**原理**：
```
你的 Git → Clash 代理 (127.0.0.1:7890) → GitHub 服务器 (国外)
                  ↑ 本地转发                ↑ 代理加密隧道
```
不经过代理时，Git 直接尝试连接 GitHub 服务器，但 GFW（防火墙）会阻断这个连接（TCP 连接超时）。经过代理后，流量通过加密隧道出国，绕过了阻断。

---

### 3.3 通过代理完成 gh 认证

```bash
set HTTPS_PROXY=http://127.0.0.1:7890
gh auth login -h github.com -p https -w
```

**逐段解析**：
- `set HTTPS_PROXY=...` — Windows cmd 语法，设置环境变量。`gh` CLI 本身不读取 git 的代理配置，而是读 `HTTPS_PROXY` / `HTTP_PROXY` 环境变量
- `-h github.com` — 登录 GitHub.com（而非 GitHub Enterprise）
- `-p https` — 使用 HTTPS 协议（而非 SSH）
- `-w` — 通过浏览器完成 OAuth 认证（`--web`）。这会打印一个一次性验证码，用户打开 `https://github.com/login/device` 输入验证码，点击授权后完成认证

**OAuth Device Flow 流程**：
1. gh 向 GitHub 请求一个设备验证码（如 `D2A4-B8CA`）
2. 用户在浏览器打开 `github.com/login/device`，输入验证码
3. 用户在浏览器确认授权（登录 GitHub 账号后点击 "Authorize"）
4. gh 轮询 GitHub 直到收到授权确认，本地保存 token
5. 后续 gh 命令用 token 认证，不再需要浏览器

---

## 第四部分：Git 初始化和提交

### 4.1 完善 .gitignore

```bash
# 读取当前 .gitignore 内容
# 添加 env/ 到忽略列表
```

**.gitignore 最终内容**：
```
.env          # 环境变量文件（含 API Key 等敏感信息）
data/         # 运行时数据目录（ChromaDB、BM25 索引、审计日志、知识库注册表）
__pycache__/  # Python 编译缓存
.venv/        # Python 虚拟环境（项目级）
env/          # Python 虚拟环境（手建在项目目录下的）
*.pyc         # Python 编译后的字节码文件
```

**原理**：`.gitignore` 告诉 Git 哪些文件/目录不应该纳入版本控制。规则语法：
- `file` — 精确匹配
- `dir/` — 匹配该目录及其中所有内容
- `*.pyc` — 通配符匹配（所有 `.pyc` 后缀文件）

---

### 4.2 暂存文件（git add）

```bash
git -C D:/ai/rag add .gitignore .dockerignore Dockerfile docker-compose.yml requirements.txt src/ tests/ docs/
```

**含义**：
- `git add` — 将文件从工作区（Working Directory）添加到暂存区（Staging Area / Index）
- 显式列出了每个要添加的路径，而非 `git add .` 或 `git add -A`

**Git 三区模型**：
```
工作区 (Working Directory)    暂存区 (Staging Area)     仓库 (.git/)
  ├── 编辑文件                  ├── git add →            ├── git commit →
  └── git checkout ←                                  └── git checkout ←
```
- **工作区**：你实际看到的文件，可以自由修改
- **暂存区**：下一次 commit 的"快照预览"，选择哪些修改要放入这次提交
- **仓库**：Git 数据库，存储所有历史版本的完整快照

**为什么不用 `git add .`**：
`git add .` 会添加当前目录所有未忽略的文件。如果意外有 `.env` 文件没被 ignore，会被提交（泄漏敏感信息）。显式指定路径更安全。

---

### 4.3 验证暂存区

```bash
git -C D:/ai/rag diff --cached --stat
```

**含义**：
- `git diff` — 显示差异
- `--cached` — 比较暂存区 vs 最后一次 commit（即"下一次 commit 会提交什么"）
- `--stat` — 只显示统计摘要（文件列表 + 增删行数），不显示具体内容

**输出**：`55 files changed, 11210 insertions(+)`——55个文件，新增11210行。

---

### 4.4 创建提交（git commit）

```bash
git -C D:/ai/rag commit -m "$(cat <<'EOF'
Initial commit: RAG multi-format knowledge base Q&A system V2.5
...
EOF
)"
```

**逐段解析**：
- `git commit -m "..."` — 创建一次提交，`-m` 指定提交信息（commit message）
- `$(cat <<'EOF' ... EOF)` — Shell heredoc 语法，用于编写多行字符串
  - `<<'EOF'` — 开始 heredoc，`EOF` 是分隔符（带引号表示不展开变量）
  - 中间的内容就是提交信息正文
  - 最后单独一行的 `EOF` 标记结束
- 提交信息第一行是简短摘要（conventional commit 风格），空行后是详细说明

**提交对象包含什么**：
- 文件快照（55个文件此时的内容）
- 作者信息（从 git config user.name/user.email 读取）
- 时间戳
- 父提交 ID（这次是 root-commit，没有父提交）
- SHA-1 哈希值（`75d8752...`），作为这次提交的唯一标识

---

## 第五部分：GitHub 仓库创建和推送

### 5.1 创建 GitHub 仓库并推送

```bash
set HTTPS_PROXY=http://127.0.0.1:7890
gh repo create RAG --public --source D:/ai/rag --remote origin --push
```

**逐段解析**：
- `set HTTPS_PROXY=...` — 同样需要给 gh 设置代理环境变量
- `gh repo create` — 调用 GitHub API 创建新仓库
- `RAG` — 仓库名称（会出现在 URL `github.com/Zzc-Jay/RAG` 中）
- `--public` — 公开仓库（任何人都能查看，不需要登录）
- `--source D:/ai/rag` — 指定本地项目目录
- `--remote origin` — 自动添加名为 `origin` 的远程仓库地址
- `--push` — 创建仓库后立即推送本地所有提交

**`origin` 是什么**：
`origin` 是 Git 对远程仓库的默认命名（约定俗成）。执行后：
```bash
git remote -v
# origin  https://github.com/Zzc-Jay/RAG.git (fetch)
# origin  https://github.com/Zzc-Jay/RAG.git (push)
```

**push 做了什么**：
将本地所有 commit 对象和文件传输到 GitHub 服务器，远程仓库就有了和本地完全一样的历史记录。

---

## 第六部分：分支改名（master → main）

### 6.1 重命名本地分支

```bash
git -C D:/ai/rag branch -m master main
```

**含义**：`git branch -m master main` — 将当前所在分支从 `master` 重命名为 `main`。

`-m` 是 `--move` 的缩写。这个操作不会改变任何文件内容，只改变分支的"标签"指向。

---

### 6.2 推送新分支名并设置默认

```bash
git -C D:/ai/rag push -u origin main
```

**含义**：
- `push` — 把 `main` 分支推送到远程
- `-u origin main` — `-u` = `--set-upstream`，将本地 `main` 关联（track）到远程 `origin/main`。之后只需 `git push` 不用指定参数。

---

### 6.3 更新 GitHub 默认分支

```bash
gh api repos/Zzc-Jay/RAG -X PATCH -f default_branch=main
```

**逐段解析**：
- `gh api` — 直接调用 GitHub REST API
- `repos/Zzc-Jay/RAG` — API 端点路径（完整 URL：`https://api.github.com/repos/Zzc-Jay/RAG`）
- `-X PATCH` — 使用 HTTP PATCH 方法（部分更新资源，对比 PUT 是全量替换）
- `-f default_branch=main` — 请求 body 参数，修改仓库的默认分支设置

**为什么要这步**：
GitHub 每个仓库有一个"默认分支"，新建仓库默认是 `main`（GitHub 2020 年后的默认）。但因为我们用 `gh repo create --push` 时本地还是 `master`，所以需要手动改为 `main`。默认分支影响：
- 打开仓库时显示的代码
- Pull Request 的默认合入目标
- 克隆仓库时 checkout 的分支

---

### 6.4 删除远程旧分支

```bash
git -C D:/ai/rag push origin --delete master
```

**含义**：`git push origin --delete master` — 删除远程仓库上的 `master` 分支。如果只做了改名（`branch -m`）但没删远程的，会同时存在 `main` 和 `master` 两个分支，造成混淆。

---

### 6.5 全局配置默认分支名

```bash
git config --global init.defaultBranch main
```

**含义**：之后 `git init` 创建新仓库时，默认分支名就是 `main`，不会再产生 `master`。写入 `~/.gitconfig`，对所有新仓库生效。

---

## 命令清单速查表

| 序号 | 命令 | 作用 |
|------|------|------|
| 1 | `ls -la` | 列出目录文件（含隐藏文件和详细信息） |
| 2 | `PYTHONPATH=... pytest tests/ -v` | 运行测试套件 |
| 3 | `git -C <path> log --oneline` | 在指定目录查看提交历史 |
| 4 | `winget install --id GitHub.cli` | 安装 GitHub CLI |
| 5 | `netstat -an \| grep LISTENING` | 查找代理服务端口 |
| 6 | `git config --global http.proxy <url>` | 配置 Git 代理 |
| 7 | `set HTTPS_PROXY=...` | 设置 gh 代理环境变量 |
| 8 | `gh auth login -p https -w` | GitHub OAuth 设备认证 |
| 9 | `git add <path> <path> ...` | 暂存指定文件 |
| 10 | `git diff --cached --stat` | 查看暂存区改动摘要 |
| 11 | `git commit -m "..."` | 创建提交 |
| 12 | `gh repo create RAG --public --source ... --push` | 创建 GitHub 仓库并推送 |
| 13 | `git branch -m master main` | 重命名本地分支 |
| 14 | `git push -u origin main` | 推送并设置上游跟踪 |
| 15 | `gh api ... -X PATCH` | 调用 GitHub API 修改仓库设置 |
| 16 | `git push origin --delete master` | 删除远程分支 |
| 17 | `git config --global init.defaultBranch main` | 全局设置默认分支名 |
