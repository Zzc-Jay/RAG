"""用户认证与多租户 — JWT 认证 + contextvars 用户上下文 + SQLite 用户存储。

教学点：
1. contextvars — 线程/协程安全的隐式上下文传递，避免在所有函数签名上加 user_id
2. JWT — 无状态认证，服务端不存 session，靠签名校验真伪
3. bcrypt — 自适应哈希，salt + rounds=12，抵御彩虹表 + 暴力破解
4. Depends — FastAPI 依赖注入，声明式路由保护
"""

from __future__ import annotations

import contextvars
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from config import DATA_DIR, JWT_SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRE_HOURS

# ── User context (contextvars) ──────────────────────────────────────
_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_user_id", default=""
)

security = HTTPBearer(auto_error=False)


def set_current_user_id(user_id: str) -> None:
    _current_user_id.set(user_id)


def get_current_user_id() -> str:
    uid = _current_user_id.get()
    if not uid:
        raise RuntimeError("未设置用户上下文，请先登录")
    return uid


# ── Password hashing ────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ── JWT ─────────────────────────────────────────────────────────────

def create_access_token(user_id: str, username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {
        "sub": user_id,
        "username": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])


# ── User DB ─────────────────────────────────────────────────────────

USER_DB_PATH = os.path.join(DATA_DIR, "users.db")


def _get_user_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(USER_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  username TEXT UNIQUE NOT NULL,"
        "  password_hash TEXT NOT NULL,"
        "  created_at TEXT NOT NULL"
        ")"
    )
    return conn


def create_user(username: str, password: str) -> dict:
    """创建新用户，返回 {id, username, created_at}。"""
    username = username.strip().lower()
    if not username or len(username) < 2:
        raise ValueError("用户名至少 2 个字符")
    if len(username) > 30:
        raise ValueError("用户名最长 30 个字符")
    if not password or len(password) < 4:
        raise ValueError("密码至少 4 个字符")

    conn = _get_user_conn()
    pw_hash = hash_password(password)
    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, pw_hash, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"用户名 '{username}' 已被注册")

    row = conn.execute(
        "SELECT id, username, created_at FROM users WHERE username = ?", (username,)
    ).fetchone()
    return {"id": str(row[0]), "username": row[1], "created_at": row[2]}


def authenticate_user(username: str, password: str) -> dict | None:
    """验证用户名密码，成功返回 {id, username}，失败返回 None。"""
    username = username.strip().lower()
    conn = _get_user_conn()
    row = conn.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if not row:
        return None
    if not verify_password(password, row[2]):
        return None
    return {"id": str(row[0]), "username": row[1]}


def get_user_by_id(user_id: str) -> dict | None:
    conn = _get_user_conn()
    row = conn.execute(
        "SELECT id, username, created_at FROM users WHERE id = ?", (int(user_id),)
    ).fetchone()
    if not row:
        return None
    return {"id": str(row[0]), "username": row[1], "created_at": row[2]}


# ── FastAPI dependency ──────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    """FastAPI 依赖：从 Authorization Bearer header 提取用户身份。

    注入到受保护的路由端点，返回 {id, username}。
    未认证时抛出 401。
    """
    if credentials is None:
        raise HTTPException(401, "请提供认证 Token（Authorization: Bearer <token>）")

    token = credentials.credentials
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token 已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Token 无效")

    user_id = payload.get("sub", "")
    if not user_id:
        raise HTTPException(401, "Token 缺失用户标识")

    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(401, "用户不存在或已被删除")

    set_current_user_id(user_id)
    return user


# ── Streamlit 入口 ──────────────────────────────────────────────────

def init_streamlit_auth(token: str | None, user_id: str | None) -> dict | None:
    """在 Streamlit rerun 开头恢复用户上下文。

    从 st.session_state 读出 token + user_id，设置 contextvar，
    返回用户信息或 None（未登录）。
    """
    if not token or not user_id:
        _current_user_id.set("")
        return None
    try:
        payload = decode_access_token(token)
    except jwt.InvalidTokenError:
        _current_user_id.set("")
        return None
    if payload.get("sub") != user_id:
        _current_user_id.set("")
        return None
    set_current_user_id(user_id)
    return {"id": user_id, "username": payload.get("username", "")}


def ensure_user_dir(user_id: str) -> str:
    """确保用户数据目录存在，返回路径。"""
    from config import get_user_data_dir
    d = get_user_data_dir(user_id)
    os.makedirs(d, exist_ok=True)
    return d


# ── 旧数据迁移 ──────────────────────────────────────────────────────

def migrate_global_data(user_id: str) -> int:
    """将旧版全局数据迁移到用户目录。返回迁移的 KB 数量。

    仅在旧版全局 kb_registry.json 存在时执行，迁移后不删除原文件。
    """
    import json
    import shutil

    old_registry = os.path.join(DATA_DIR, "kb_registry.json")
    if not os.path.exists(old_registry):
        return 0

    user_dir = ensure_user_dir(user_id)
    new_registry = os.path.join(user_dir, "kb_registry.json")

    # 如果用户已有注册表，跳过
    if os.path.exists(new_registry):
        return 0

    # 复制注册表
    shutil.copy2(old_registry, new_registry)

    # 复制 ChromaDB 和 BM25 数据
    for sub in ("chroma_db", "bm25"):
        old_sub = os.path.join(DATA_DIR, sub)
        new_sub = os.path.join(user_dir, sub)
        if os.path.exists(old_sub) and not os.path.exists(new_sub):
            shutil.copytree(old_sub, new_sub)

    # 复制审计日志
    old_audit = os.path.join(DATA_DIR, "audit")
    new_audit = os.path.join(user_dir, "audit")
    if os.path.exists(old_audit) and not os.path.exists(new_audit):
        shutil.copytree(old_audit, new_audit)

    # 计数
    try:
        with open(new_registry, "r", encoding="utf-8") as f:
            reg = json.load(f)
        return len(reg)
    except Exception:
        return 0
