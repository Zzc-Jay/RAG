"""审计日志 — append-only 操作记录，用于追溯和合规。

教学点 — 审计日志 vs 应用日志：
1. 审计日志记录 "谁做了什么"（业务事件），应用日志记录 "程序怎么运行的"（调试信息）
2. Append-only 设计：只 INSERT 不 UPDATE/DELETE，保证日志完整性，防止篡改
3. 结构化存储：details 存 JSON，兼顾查询灵活性（索引字段）和扩展性（自由字段）
4. SQLite 索引策略：按时间 DESC、事件类型、知识库名建索引，覆盖常见查询模式
5. 多租户：每条日志记录 user_id，查询按当前用户自动筛选
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone


def _get_audit_dir() -> str:
    from auth import get_current_user_id, ensure_user_dir
    d = os.path.join(ensure_user_dir(get_current_user_id()), "audit")
    os.makedirs(d, exist_ok=True)
    return d


def _get_db_path() -> str:
    return os.path.join(_get_audit_dir(), "audit.db")


_local = threading.local()

# 时区：东八区
TZ = timezone(timedelta(hours=8))


def _get_conn() -> sqlite3.Connection:
    """获取线程本地的 SQLite 连接（线程安全）。"""
    path = _get_db_path()
    key = f"conn_{path}"
    if not hasattr(_local, key) or getattr(_local, key) is None:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS audit_log ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  timestamp TEXT NOT NULL,"
            "  event_type TEXT NOT NULL,"
            "  kb_name TEXT NOT NULL DEFAULT '',"
            "  details TEXT NOT NULL DEFAULT '{}',"
            "  user_id TEXT NOT NULL DEFAULT ''"
            ")"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_ts "
            "ON audit_log(timestamp DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_type "
            "ON audit_log(event_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_kb "
            "ON audit_log(kb_name)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_user "
            "ON audit_log(user_id)"
        )
        # 兼容旧表结构：为已有 audit.db 添加 user_id 列
        try:
            conn.execute("ALTER TABLE audit_log ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # 列已存在
        setattr(_local, key, conn)
    return getattr(_local, key)


def log_event(
    event_type: str,
    kb_name: str = "",
    details: dict | None = None,
    session_id: str = "",
) -> int:
    """记录一条审计事件（append-only）。user_id 从用户上下文自动获取。

    返回新记录的 id。
    """
    # 获取当前用户 ID（未设置时用空串，兼容无认证场景）
    try:
        from auth import get_current_user_id
        user_id = get_current_user_id()
    except RuntimeError:
        user_id = ""

    conn = _get_conn()
    ts = datetime.now(TZ).isoformat(timespec="seconds")
    details_json = json.dumps(details or {}, ensure_ascii=False, separators=(",", ":"))
    conn.execute(
        "INSERT INTO audit_log (timestamp, event_type, kb_name, details, user_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (ts, event_type, kb_name, details_json, user_id),
    )
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid()").fetchone()
    return row[0] if row else 0


def get_events(
    kb_name: str = "",
    event_type: str = "",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """分页查询审计日志，自动按当前用户筛选，支持按知识库和事件类型进一步筛选。

    返回列表按时间倒序排列。
    """
    conn = _get_conn()

    try:
        from auth import get_current_user_id
        user_id = get_current_user_id()
    except RuntimeError:
        user_id = ""

    conditions: list[str] = ["user_id = ?"]
    params: list[str] = [user_id]

    if kb_name:
        conditions.append("kb_name = ?")
        params.append(kb_name)
    if event_type:
        conditions.append("event_type = ?")
        params.append(event_type)

    where = "WHERE " + " AND ".join(conditions)

    query = (
        f"SELECT id, timestamp, event_type, kb_name, details, user_id "
        f"FROM audit_log {where} "
        f"ORDER BY timestamp DESC, id DESC "
        f"LIMIT ? OFFSET ?"
    )
    params.extend([str(limit), str(offset)])

    rows = conn.execute(query, params).fetchall()
    return [
        {
            "id": r[0],
            "timestamp": r[1],
            "event_type": r[2],
            "kb_name": r[3],
            "details": json.loads(r[4]) if r[4] else {},
            "user_id": r[5],
        }
        for r in rows
    ]


def count_events(kb_name: str = "", event_type: str = "") -> int:
    """返回符合条件的日志总数（用于分页 total），自动按当前用户筛选。"""
    conn = _get_conn()

    try:
        from auth import get_current_user_id
        user_id = get_current_user_id()
    except RuntimeError:
        user_id = ""

    conditions: list[str] = ["user_id = ?"]
    params: list[str] = [user_id]

    if kb_name:
        conditions.append("kb_name = ?")
        params.append(kb_name)
    if event_type:
        conditions.append("event_type = ?")
        params.append(event_type)

    where = "WHERE " + " AND ".join(conditions)

    row = conn.execute(f"SELECT COUNT(*) FROM audit_log {where}", params).fetchone()
    return row[0] if row else 0


def get_stats() -> dict:
    """返回审计日志统计：按事件类型分组计数 + 时间范围。自动按当前用户筛选。"""
    conn = _get_conn()

    try:
        from auth import get_current_user_id
        user_id = get_current_user_id()
    except RuntimeError:
        user_id = ""

    rows = conn.execute(
        "SELECT event_type, COUNT(*) FROM audit_log WHERE user_id = ? GROUP BY event_type",
        (user_id,),
    ).fetchall()

    time_range = conn.execute(
        "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM audit_log WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    return {
        "by_type": {r[0]: r[1] for r in rows},
        "total": time_range[2] if time_range else 0,
        "first_event": time_range[0] if time_range else None,
        "last_event": time_range[1] if time_range else None,
    }


def cleanup(days: int = 90) -> int:
    """清理超过 N 天的记录，返回删除的记录数。days=0 时清空全部。"""
    conn = _get_conn()

    try:
        from auth import get_current_user_id
        user_id = get_current_user_id()
    except RuntimeError:
        user_id = ""

    if days <= 0:
        row = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE user_id = ?", (user_id,)
        ).fetchone()
        count = row[0] if row else 0
        conn.execute("DELETE FROM audit_log WHERE user_id = ?", (user_id,))
        conn.commit()
        return count

    cutoff = (datetime.now(TZ) - timedelta(days=days)).isoformat(timespec="seconds")
    row = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE user_id = ? AND timestamp < ?",
        (user_id, cutoff),
    ).fetchone()
    count = row[0] if row else 0
    conn.execute(
        "DELETE FROM audit_log WHERE user_id = ? AND timestamp < ?",
        (user_id, cutoff),
    )
    conn.commit()
    return count
