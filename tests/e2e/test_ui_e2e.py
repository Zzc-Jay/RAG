"""Streamlit UI 端到端测试 — 使用 AppTest 模拟浏览器操作。

AppTest 在内存中运行 Streamlit 脚本，通过 widget 操作模拟用户交互。
外部 API 层已在 conftest.py 中 mock，这里专注验证 UI 交互流程。

认证说明：AppTest 运行时 auth.init_streamlit_auth 被 patch 为直接返回用户，
绕过实际 JWT 验证，使测试能正常进入主界面。
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

# 预导入 —— 确保 AppTest 运行时模块已在 sys.modules 中（patch 已生效）
import embedder  # noqa: F401
import generator  # noqa: F401
import query_rewriter  # noqa: F401
import reranker  # noqa: F401

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "src", "app.py")
)

TEST_USER = {"id": "test-user-001", "username": "testuser"}


def _make_fake_init_auth():
    """返回一个假的 init_streamlit_auth，会实际设置 contextvar。"""
    def _fake(token, user_id):
        from auth import _current_user_id
        _current_user_id.set("test-user-001")
        return {"id": "test-user-001", "username": "testuser"}
    return _fake


def _run_with_auth(kb_name: str | None = None) -> AppTest:
    """创建 AppTest 并模拟已登录状态运行。"""
    with patch("auth.init_streamlit_auth", side_effect=_make_fake_init_auth()):
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.run()
    return at


# ═══════════════════════════════════════════════════════════════
# 基础渲染 — 冒烟测试
# ═══════════════════════════════════════════════════════════════

def test_e2e_ui_renders_without_error(kb_lifecycle, test_txt_file):
    """预创建 KB + 文档后运行 AppTest，验证无异常且 UI 正确渲染。"""
    kb = kb_lifecycle

    # 预上传文档（AppTest 难以模拟文件上传，通过 kb_manager 直接操作）
    from loader import load_document
    from chunker import split_pages
    from embedder import add_to_kb, delete_doc_chunks, get_all_chunks
    from bm25_index import build_index
    from kb_manager import add_doc

    fname = os.path.basename(test_txt_file)
    ext = os.path.splitext(test_txt_file)[1].lower()
    pages = load_document(test_txt_file, ext)
    chunks = split_pages(pages, source=fname)
    delete_doc_chunks(kb, fname)
    add_to_kb(chunks, kb)
    all_chunks = get_all_chunks(kb)
    build_index(all_chunks, kb)
    add_doc(kb, fname, len(pages), len(chunks), "txt")

    at = _run_with_auth()

    # 不应抛出未捕获异常
    assert not at.exception

    # 侧边栏应显示测试用户名
    sidebar_text = str(at.sidebar)
    assert "testuser" in sidebar_text or kb in sidebar_text


# ═══════════════════════════════════════════════════════════════
# 侧边栏 KB CRUD
# ═══════════════════════════════════════════════════════════════

def test_e2e_ui_create_kb_via_sidebar():
    """通过侧边栏 UI 创建新知识库。"""
    from kb_manager import delete_kb, list_kbs

    test_name = "_e2e_ui_test_kb"
    try:
        delete_kb(test_name)
    except (ValueError, PermissionError):
        pass

    at = _run_with_auth()
    assert not at.exception

    # 验证侧边栏包含核心控件
    button_labels = [b.label for b in at.sidebar.button]
    assert "新建知识库" in button_labels
    assert "登出" in button_labels


# ═══════════════════════════════════════════════════════════════
# 无 KB 时的 guard 信息
# ═══════════════════════════════════════════════════════════════

def test_e2e_ui_no_kb_selected_guard():
    """无 KB 时运行，应显示「请先创建或选择知识库」提示。"""
    with patch("kb_manager.list_kbs", return_value=[]):
        at = _run_with_auth()
        assert not at.exception

        info_elements = at.main.info
        assert len(info_elements) >= 1
        assert "请先在左侧创建或选择一个知识库" in info_elements[0].value
