"""审计日志测试 — append-only 操作记录。

教学点：
1. 审计日志记录 "谁在什么时候做了什么"（业务事件）
2. Append-only：只 INSERT 不 UPDATE/DELETE（除 cleanup）
3. 结构化 JSON details 字段支持灵活查询
4. 与业务模块集成：kb_manager 操作自动记入审计日志
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from audit import (
    log_event,
    get_events,
    count_events,
    get_stats,
    cleanup,
)


@pytest.fixture(autouse=True)
def _clear_audit():
    """每个测试前后清空审计日志，保证隔离。"""
    cleanup(days=0)
    yield
    cleanup(days=0)


# ═══════════════════════════════════════════════════════════════
# 基本写入
# ═══════════════════════════════════════════════════════════════

def test_log_event_returns_id():
    """log_event 返回新记录的 id。"""
    eid = log_event("kb.create", kb_name="test_kb")
    assert eid > 0


def test_log_event_with_details():
    """带 details 的事件记录。"""
    eid = log_event("doc.upload", kb_name="mykb", details={
        "doc_name": "test.pdf",
        "pages": 3,
        "chunks": 12,
    })
    assert eid > 0


def test_log_event_optional_fields():
    """kb_name 和 details 可以为空。"""
    eid = log_event("system.startup")
    assert eid > 0


# ═══════════════════════════════════════════════════════════════
# 查询
# ═══════════════════════════════════════════════════════════════

def test_get_events_empty():
    """空数据库返回空列表。"""
    events = get_events()
    assert events == []


def test_get_events_returns_recent_first():
    """get_events 按时间倒序返回。"""
    log_event("kb.create", kb_name="kb_a")
    time.sleep(0.01)
    log_event("kb.create", kb_name="kb_b")

    events = get_events(limit=10)
    assert len(events) == 2
    # 后创建的在前
    assert events[0]["kb_name"] == "kb_b"
    assert events[1]["kb_name"] == "kb_a"


def test_get_events_filter_by_kb():
    """按知识库筛选。"""
    log_event("kb.create", kb_name="sales")
    log_event("doc.upload", kb_name="sales", details={"doc_name": "a.pdf"})
    log_event("kb.create", kb_name="marketing")

    events = get_events(kb_name="sales")
    assert len(events) == 2
    assert all(e["kb_name"] == "sales" for e in events)


def test_get_events_filter_by_type():
    """按事件类型筛选。"""
    log_event("kb.create", kb_name="kb1")
    log_event("doc.upload", kb_name="kb1")
    log_event("doc.upload", kb_name="kb2")

    events = get_events(event_type="doc.upload")
    assert len(events) == 2
    assert all(e["event_type"] == "doc.upload" for e in events)


def test_get_events_filter_both():
    """同时按知识库和事件类型筛选。"""
    log_event("query", kb_name="kb1", details={"query": "hello"})
    log_event("query", kb_name="kb2", details={"query": "world"})
    log_event("doc.upload", kb_name="kb1")

    events = get_events(kb_name="kb1", event_type="query")
    assert len(events) == 1
    assert events[0]["kb_name"] == "kb1"
    assert events[0]["event_type"] == "query"


def test_get_events_pagination():
    """分页查询（limit + offset）。"""
    for i in range(10):
        log_event("kb.create", kb_name=f"kb_{i}")

    page1 = get_events(limit=4, offset=0)
    page2 = get_events(limit=4, offset=4)
    page3 = get_events(limit=4, offset=8)

    assert len(page1) == 4
    assert len(page2) == 4
    assert len(page3) == 2
    # 验证不重叠
    ids_page1 = {e["id"] for e in page1}
    ids_page2 = {e["id"] for e in page2}
    assert ids_page1.isdisjoint(ids_page2)


def test_get_events_details_parsed():
    """details 字段正确解析为 dict。"""
    log_event("doc.upload", kb_name="test", details={
        "doc_name": "report.pdf",
        "pages": 5,
    })

    events = get_events(limit=1)
    assert isinstance(events[0]["details"], dict)
    assert events[0]["details"]["doc_name"] == "report.pdf"
    assert events[0]["details"]["pages"] == 5


# ═══════════════════════════════════════════════════════════════
# 计数
# ═══════════════════════════════════════════════════════════════

def test_count_events_total():
    """count_events 返回总数。"""
    for i in range(5):
        log_event("kb.create", kb_name=f"kb_{i}")
    assert count_events() == 5


def test_count_events_filtered():
    """count_events 支持筛选。"""
    log_event("query", kb_name="a")
    log_event("query", kb_name="a")
    log_event("query", kb_name="b")

    assert count_events(kb_name="a") == 2
    assert count_events(event_type="query") == 3
    assert count_events(kb_name="a", event_type="query") == 2


# ═══════════════════════════════════════════════════════════════
# 统计
# ═══════════════════════════════════════════════════════════════

def test_get_stats_empty():
    """空数据库统计。"""
    s = get_stats()
    assert s["total"] == 0
    assert s["by_type"] == {}
    assert s["first_event"] is None


def test_get_stats_with_data():
    """有数据后的统计。"""
    log_event("kb.create", kb_name="a")
    log_event("doc.upload", kb_name="a")
    log_event("query", kb_name="a")
    log_event("query", kb_name="b")

    s = get_stats()
    assert s["total"] == 4
    assert s["by_type"] == {"kb.create": 1, "doc.upload": 1, "query": 2}
    assert s["first_event"] is not None
    assert s["last_event"] is not None


# ═══════════════════════════════════════════════════════════════
# 清理
# ═══════════════════════════════════════════════════════════════

def test_cleanup_removes_old():
    """清理超过 N 天的记录。"""
    log_event("kb.create", kb_name="old_kb")
    count_before = count_events()
    assert count_before == 1

    # cleanup(days=0) 删除所有记录
    deleted = cleanup(days=0)
    assert deleted > 0
    assert count_events() == 0


def test_cleanup_zero_days_removes_all():
    """days=0 清空全部。"""
    for _ in range(3):
        log_event("kb.create", kb_name="test")
    assert count_events() == 3
    cleanup(days=0)
    assert count_events() == 0


# ═══════════════════════════════════════════════════════════════
# 集成：kb_manager 触发审计日志
# ═══════════════════════════════════════════════════════════════

def test_kb_create_triggers_audit():
    """create_kb 自动产生 kb.create 事件。"""
    from kb_manager import create_kb as kb_create, delete_kb as kb_del
    kb_name = "_audit_test_kb_create"

    try:
        kb_del(kb_name)
    except ValueError:
        pass

    kb_create(kb_name)

    events = get_events(event_type="kb.create", kb_name=kb_name)
    assert len(events) >= 1
    assert events[0]["kb_name"] == kb_name

    kb_del(kb_name)


def test_kb_delete_triggers_audit():
    """delete_kb 自动产生 kb.delete 事件。"""
    from kb_manager import create_kb as kb_create, delete_kb as kb_del
    kb_name = "_audit_test_kb_del"

    try:
        kb_del(kb_name)
    except ValueError:
        pass

    kb_create(kb_name)
    kb_del(kb_name)

    events = get_events(event_type="kb.delete", kb_name=kb_name)
    assert len(events) >= 1
    assert events[0]["kb_name"] == kb_name


# ═══════════════════════════════════════════════════════════════
# 事件完整性
# ═══════════════════════════════════════════════════════════════

def test_all_event_types():
    """验证所有事件类型都能正确记录和查询。"""
    events_to_log = [
        ("kb.create", "test", {"action": "create"}),
        ("kb.delete", "test", {"action": "delete"}),
        ("doc.upload", "test", {"doc_name": "f.pdf"}),
        ("doc.url", "test", {"url": "https://x.com"}),
        ("doc.delete", "test", {"doc_name": "f.pdf"}),
        ("query", "test", {"query": "what is AI"}),
        ("query.stream", "test", {"query": "what is AI"}),
    ]
    for event_type, kb, details in events_to_log:
        log_event(event_type, kb_name=kb, details=details)

    s = get_stats()
    assert s["total"] == 7
    assert set(s["by_type"].keys()) == {
        "kb.create", "kb.delete", "doc.upload", "doc.url",
        "doc.delete", "query", "query.stream",
    }


def test_event_timestamp_iso_format():
    """时间戳应为 ISO 8601 格式。"""
    eid = log_event("kb.create", kb_name="fmt_test")
    events = get_events(limit=1)
    ts = events[0]["timestamp"]
    assert "T" in ts
    # 格式: 2026-05-24T15:30:00+08:00
    assert len(ts) >= 19
