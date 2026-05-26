"""审计日志 — append-only 操作记录，用于追溯和合规。

教学点 — 审计日志 vs 应用日志：
1. 审计日志记录 "谁做了什么"（业务事件），应用日志记录 "程序怎么运行的"（调试信息）
2. Append-only 设计：只 INSERT 不 UPDATE/DELETE，保证日志完整性，防止篡改
3. 结构化存储：details 存 JSON，兼顾查询灵活性（索引字段）和扩展性（自由字段）
4. SQLite 索引策略：按时间 DESC、事件类型、知识库名建索引，覆盖常见查询模式
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from config import DATA_DIR

AUDIT_DIR = os.path.join(DATA_DIR, "audit")
os.makedirs(AUDIT_DIR, exist_ok=True)
DB_PATH = os.path.join(AUDIT_DIR, "audit.db")

_local = threading.local()

# 时区：东八区
TZ = timezone(timedelta(hours=8))


def _get_conn() -> sqlite3.Connection:
    """获取线程本地的 SQLite 连接（线程安全）。"""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute(
            "CREATE TABLE IF NOT EXISTS audit_log ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  timestamp TEXT NOT NULL,"
            "  event_type TEXT NOT NULL,"
            "  kb_name TEXT NOT NULL DEFAULT '',"
            "  details TEXT NOT NULL DEFAULT '{}',"
            "  session_id TEXT NOT NULL DEFAULT ''"
            ")"
        )
        _local.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_ts "
            "ON audit_log(timestamp DESC)"
        )
        _local.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_type "
            "ON audit_log(event_type)"
        )
        _local.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_kb "
            "ON audit_log(kb_name)"
        )
    return _local.conn


def log_event(
    event_type: str,
    kb_name: str = "",
    details: dict | None = None,
    session_id: str = "",
) -> int:
    """记录一条审计事件（append-only）。

    返回新记录的 id。
    """
    conn = _get_conn()
    ts = datetime.now(TZ).isoformat(timespec="seconds")
    details_json = json.dumps(details or {}, ensure_ascii=False, separators=(",", ":"))
    conn.execute(
        "INSERT INTO audit_log (timestamp, event_type, kb_name, details, session_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (ts, event_type, kb_name, details_json, session_id),
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
    """分页查询审计日志，支持按知识库和事件类型筛选。

    返回列表按时间倒序排列。
    """
    conn = _get_conn()
    conditions: list[str] = []
    params: list[str] = []

    if kb_name:
        conditions.append("kb_name = ?")
        params.append(kb_name)
    if event_type:
        conditions.append("event_type = ?")
        params.append(event_type)

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    query = (
        f"SELECT id, timestamp, event_type, kb_name, details, session_id "
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
            "session_id": r[5],
        }
        for r in rows
    ]


def count_events(kb_name: str = "", event_type: str = "") -> int:
    """返回符合条件的日志总数（用于分页 total）。"""
    conn = _get_conn()
    conditions: list[str] = []
    params: list[str] = []

    if kb_name:
        conditions.append("kb_name = ?")
        params.append(kb_name)
    if event_type:
        conditions.append("event_type = ?")
        params.append(event_type)

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    row = conn.execute(f"SELECT COUNT(*) FROM audit_log {where}", params).fetchone()
    return row[0] if row else 0


def get_stats() -> dict:
    """返回审计日志统计：按事件类型分组计数 + 时间范围。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT event_type, COUNT(*) FROM audit_log GROUP BY event_type"
    ).fetchall()

    time_range = conn.execute(
        "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM audit_log"
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
    if days <= 0:
        row = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
        count = row[0] if row else 0
        conn.execute("DELETE FROM audit_log")
        conn.commit()
        return count

    cutoff = (datetime.now(TZ) - timedelta(days=days)).isoformat(timespec="seconds")
    row = conn.execute("SELECT COUNT(*) FROM audit_log WHERE timestamp < ?", (cutoff,)).fetchone()
    count = row[0] if row else 0
    conn.execute("DELETE FROM audit_log WHERE timestamp < ?", (cutoff,))
    conn.commit()
    return count
