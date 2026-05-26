# 用户认证与多租户设计

## 问题定义

改造前系统是单机单用户模式：
- 所有 API 端点公开，不需要登录
- 所有用户共享同一份知识库数据
- 审计日志不知道谁做了什么操作

改造后：
- 需要注册/登录才能访问（API 和 UI 都需要）
- 每个用户的数据物理隔离，互不可见
- 审计日志记录每个操作的用户身份

---

## 整体数据流

### 用户注册/登录

```
注册: POST /auth/register {username, password}
  → bcrypt.hashpw(password) → 存入 users.db
  → 签发 JWT Token → 返回 {token, user_id, username}
  → 检测旧全局数据 → 自动迁移到用户目录

登录: POST /auth/login {username, password}
  → 查 users.db → bcrypt.checkpw(password, hash)
  → 签发 JWT Token → 返回 {token, user_id, username}
```

### 请求处理流程

```
HTTP Request (Authorization: Bearer <JWT>)
  │
  ▼
FastAPI get_current_user 依赖
  → 提取 Bearer Token
  → jwt.decode(token, SECRET_KEY) — 验证签名+过期时间
  → payload = {"sub": "1", "username": "admin"}
  → 查 users.db 确认用户存在
  → set_current_user_id("1")  ← 写入 contextvar
  ▼
路由函数 (如 create_kb)
  → kb_manager.create_kb("mykb")
    → _load_registry() 内部调用 get_current_user_id() → "1"
    → 读写 data/users/1/kb_registry.json
  → embedder.add_to_kb(chunks, "mykb")
    → _get_collection("mykb") 内部调用 get_current_user_id() → "1"
    → ChromaDB: data/users/1/chroma_db/mykb/
  → audit.log_event("kb.create", ...)
    → 内部调用 get_current_user_id() → "1"
    → INSERT ... user_id = "1"
```

---

## 核心技术 1：contextvars — 用户上下文的隐形通道

### 为什么不用函数参数

传统做法需要给每个函数加 `user_id` 参数：

```python
# 需要改签名的函数（部分列表）
create_kb(user_id, name)
delete_kb(user_id, name)
list_kbs(user_id)
add_doc(user_id, kb_name, ...)
remove_doc(user_id, kb_name, ...)
add_to_kb(user_id, chunks, kb_name)
search(user_id, query, kb_name)
build_index(user_id, chunks, kb_name)
load_index(user_id, kb_name)
log_event(user_id, event_type, ...)
get_events(user_id, ...)
# ... 40+ 个函数
```

这会带来三个问题：
1. **改动面广**：40+ 函数签名变更，200+ 处调用点全部要改
2. **侵入性强**：每个函数都要知道"用户"这个概念，破坏单一职责
3. **不可组合**：内层函数（如 embedding cache）完全不关心用户——它是纯文本到向量的映射

### contextvars 解决方案

```python
import contextvars

_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_user_id", default=""
)

def set_current_user_id(user_id: str):
    _current_user_id.set(user_id)

def get_current_user_id() -> str:
    uid = _current_user_id.get()
    if not uid:
        raise RuntimeError("未设置用户上下文")
    return uid
```

**工作原理**：

```
每个线程/协程有独立的 contextvar 存储空间

Thread-1 处理 user-A 请求:
  _current_user_id = "1"

Thread-2 处理 user-B 请求:
  _current_user_id = "2"

两个线程互不干扰，各自读取各自的值
```

**类比**：就像在公司门口挂牌子写"今天来访者：张三"。进入公司的每个人（函数）都能看到这个牌子，不需要每个人随身带着来访者信息（函数参数）。

### 三个设置入口

| 场景 | 设置方式 | 代码位置 |
|------|---------|---------|
| FastAPI 请求 | `get_current_user` 依赖中设置 | `auth.py` → `api.py` |
| Streamlit rerun | `init_streamlit_auth()` 中设置 | `auth.py` → `app.py` |
| 测试 | conftest fixture 直接设置 | `tests/conftest.py` |

---

## 核心技术 2：JWT — 无状态身份令牌

### Token 结构

```
eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIiwidXNlcm5hbWUiOiJhZG1pbiIsImV4cCI6MTc3OTg3NzQ5OH0.signature
│                      │                                                                    │
▼                      ▼                                                                    ▼
Base64(Header)         Base64(Payload)                                                     签名
{"alg":"HS256"}        {"sub":"1","username":"admin","exp":1779877498}
```

- **Header**：算法类型（HS256 = HMAC-SHA256）
- **Payload**：用户信息（`sub`=user_id, `username`），过期时间（`exp`）
- **Signature**：对前两段做 HMAC-SHA256，密钥 = `JWT_SECRET_KEY`

**安全性保证**：任何人改了 Payload（比如把自己从 user-2 改成 user-1），签名就对不上了 → 验证失败。

### 签发和验证

```python
def create_access_token(user_id, username):
    expire = datetime.now(UTC) + timedelta(hours=24)
    payload = {"sub": user_id, "username": username, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def decode_access_token(token):
    return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    # 签名无效 → InvalidTokenError
    # 已过期 → ExpiredSignatureError
```

### 对比其他方案

| 方案 | 优点 | 缺点 | 适用场景 |
|------|------|------|---------|
| Session + Cookie | 服务端可控 | 需 Redis/DB 存 session | 传统 Web 应用 |
| API Key（固定串） | 最简单 | 泄露后无法过期 | 机器间调用 |
| **JWT** | 无状态，含用户信息 | 签发后不可撤销 | **本项目** |

JWT 最适合本项目的原因：同时服务 REST API（Header 传 Bearer Token）和 Streamlit UI（session_state 存 Token），不需要额外引入 Redis。

---

## 核心技术 3：bcrypt 密码哈希

### 为什么不能直接 SHA256 存密码

```python
# 如果这样做：
hashlib.sha256("123456").hexdigest()
# → "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92"
```

两个致命问题：

**彩虹表攻击**：有人事先算好了"123456"的 SHA256 值。全网所有用 SHA256 存密码的系统，被拖库后都能直接匹配。

**暴力破解太快**：SHA256 设计目标就是快（GPU 每秒几十亿次）。如果你的数据库泄露，攻击者可以用 GPU 暴力破解所有用户的密码。

### bcrypt 如何解决

```python
import bcrypt

# 存储时
hash = bcrypt.hashpw("123456".encode(), bcrypt.gensalt(rounds=12))
# → "$2b$12$LJ3m4ys3GZ...很长的哈希串..."

# 验证时
bcrypt.checkpw("123456".encode(), hash.encode())
# → True
```

**Salt（盐）**：`gensalt()` 每次生成随机值，同一个密码两次哈希的结果完全不同
- 张三密码 "123456" → `$2b$12$abc...XyZ`
- 李四密码 "123456" → `$2b$12$xyz...AbC`
- 彩虹表失效——因为全网找不到一个 $2b$12$abc... 开头的预计算哈希

**rounds=12**：2^12 = 4096 次迭代，故意算得慢。用户登录时多花 0.2 秒没感觉，攻击者暴力破解时要多花 4096 倍的时间。

---

## 核心技术 4：数据物理隔离

### 目录结构

```
data/
├── users.db                     # 用户账户表（全局唯一）
│   └── users 表: id, username, password_hash, created_at
│
├── embedding_cache/             # 嵌入缓存（全局共享）
│   └── cache.db: text_hash → embedding_vector
│
└── users/                       # 用户数据根目录
    ├── 1/                       # user_id=1 的全部数据
    │   ├── kb_registry.json     # 知识库注册表
    │   ├── chroma_db/           # ChromaDB 向量存储
    │   │   └── {kb_name}/       # 每个知识库一个 collection
    │   ├── bm25/                # BM25 索引 pickle 文件
    │   │   └── {kb_name}/
    │   └── audit/               # 审计日志
    │       └── audit.db
    │
    └── 2/                       # user_id=2 的全部数据
        └── ...
```

### 隔离级别

| 数据 | 隔离方式 | 原因 |
|------|---------|------|
| 知识库注册表 | 每用户独立 JSON 文件 | 不同用户的 KB 命名空间完全独立 |
| ChromaDB 向量 | 每用户独立目录 | 物理隔离，user-1 查不到 user-2 的数据 |
| BM25 索引 | 每用户独立目录 | 同上 |
| 审计日志 | 每用户独立 SQLite | `user_id` 列 + 查询自动按当前用户筛选 |
| 嵌入缓存 | **全局共享** | 内容寻址（SHA256），同一文本任何人嵌入结果相同，无隐私泄漏 |
| 用户账户 | **全局唯一** | 用户名不能重复，存在 `users.db` 中 |

### 嵌入缓存为什么可以共享

嵌入缓存存储的是 `<文本SHA256> → <向量>` 的映射。这个映射不包含任何用户信息——"Python是编程语言"这个字符串的嵌入向量和谁上传的、在哪个知识库里没有关系。

这就好比字典里查"hello"的意思，不管张三查还是李四查，字典给的解释都一样。嵌入缓存就是这个"字典"。

---

## 核心技术 5：API 依赖注入

### FastAPI Depends 机制

```python
# 定义依赖
async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    token = credentials.credentials
    payload = decode_access_token(token)
    set_current_user_id(payload["sub"])
    return {"id": payload["sub"], "username": payload["username"]}

# 使用依赖
@app.get("/api/kb")
def api_list_kbs(current_user: dict = Depends(get_current_user)):
    # current_user 自动注入，不需要手动验证
    ...
```

执行流程：
```
请求到达 → FastAPI 解析 Depends → 调用 get_current_user()
  → 验证 Token → 设置 contextvar → 返回 user 对象
  → 注入到 current_user 参数
  → 执行路由函数
```

如果 Token 无效，`get_current_user()` 抛出 `HTTPException(401)`，路由函数根本不会执行。**声明式安全**——改成受保护的只需在一行加 `Depends(get_current_user)`。

### 测试中的依赖覆盖

```python
# conftest.py
app.dependency_overrides[get_current_user] = _fake_get_current_user
```

测试时不需要真实 JWT——一行代码替换整个认证逻辑，让 TestClient 请求自动获得一个假用户身份。

---

## 核心技术 6：Streamlit 认证流程

```
用户访问 localhost:8501
  │
  ▼
检查 st.session_state.auth_token
  │
  ├─ 无 token → 渲染登录/注册表单 → st.stop()
  │
  └─ 有 token → init_streamlit_auth(token, user_id)
       │
       ├─ 解码 JWT，验证 sub == user_id
       ├─ set_current_user_id(user_id)  ← 写入 contextvar
       └─ 渲染主应用
            │
            ├─ 侧边栏顶部: 👤 username | [登出]
            │   点击登出 → 清空 auth session_state → 跳登录页
            │
            └─ 所有操作自动使用当前用户的数据（contextvar 传递）
```

### 为什么每次 rerun 都要重新验证

Streamlit 的每次交互（点击按钮、输入文本）都会触发一次完整的脚本重新执行（rerun）。所以在脚本开头必须：

1. 从 `session_state` 读取 token
2. 解码 JWT 验证
3. 设置 contextvar

如果 Token 过期了，`init_streamlit_auth()` 返回 `None` → 显示登录页。

---

## 旧数据迁移

注册或登录成功后自动执行：

```python
def migrate_global_data(user_id):
    # 1. 检查旧 data/kb_registry.json 是否存在
    # 2. 不存在 → 无需迁移
    # 3. 用户已有自己的数据 → 跳过（防重复迁移）
    # 4. 复制: registry + chroma_db/ + bm25/ + audit/
    #    → data/users/{user_id}/
    # 5. 返回迁移的 KB 数量
```

原旧数据文件保留不删，用户可以手动清理。

---

## 设计总结

| 决策 | 选择 | 替代方案 | 选择理由 |
|------|------|---------|---------|
| 用户上下文传递 | contextvars | 函数参数 | 不改 40+ 函数签名 |
| 认证令牌 | JWT (HS256) | Session/API Key | 无状态，API+UI 复用 |
| 密码存储 | bcrypt (rounds=12) | SHA256+盐 | 抗彩虹表+抗暴力 |
| 数据隔离 | 物理目录分离 | 查询过滤 | 最简单可靠 |
| 嵌入缓存 | 全局共享 | 每用户独立 | SHA256 内容寻址无泄漏 |
| API 保护 | FastAPI Depends | 手动中间件 | 声明式、测试友好 |
| 测试认证 | dependency_overrides | 真实 Token | 零配置、零开销 |
