"""Streamlit UI 端到端测试 — 使用 AppTest 模拟浏览器操作。

AppTest 在内存中运行 Streamlit 脚本，通过 widget 操作模拟用户交互。
外部 API 层已在 conftest.py 中 mock，这里专注验证 UI 交互流程。
"""

from __future__ import annotations

import os
import sys

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

    # 运行 AppTest
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()

    # 不应抛出未捕获异常
    assert not at.exception

    # 侧边栏应显示 KB 名称
    sidebar_text = str(at.sidebar)
    assert kb in sidebar_text

    # 主区域可能显示对话区域（因为有 KB 且有文档）
    main_text = str(at.main)
    # 不显示「请先创建」的 guard
    assert "请先在左侧创建或选择一个知识库" not in main_text


# ═══════════════════════════════════════════════════════════════
# 侧边栏 KB CRUD
# ═══════════════════════════════════════════════════════════════

def test_e2e_ui_create_kb_via_sidebar():
    """通过侧边栏 UI 创建新知识库。"""
    # 先清理同名 KB
    from kb_manager import delete_kb, list_kbs

    test_name = "_e2e_ui_test_kb"
    try:
        delete_kb(test_name)
    except (ValueError, PermissionError):
        pass

    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    assert not at.exception

    # 点击「新建知识库」按钮
    create_btns = [b for b in at.sidebar.button if b.label == "新建知识库"]
    assert len(create_btns) >= 1
    create_btns[0].click().run()

    # 表单应出现
    assert not at.exception

    # 在 text_input 中输入名称
    text_inputs = [ti for ti in at.sidebar.text_input if ti.key == "new_kb_input"]
    assert len(text_inputs) == 1
    text_inputs[0].set_value(test_name).run()

    # 点击「确认创建」
    submit_btns = [b for b in at.sidebar.button if b.label == "确认创建"]
    assert len(submit_btns) >= 1
    submit_btns[0].click().run()

    assert not at.exception

    # 验证 KB 已创建
    assert test_name in list_kbs()

    # 清理
    try:
        delete_kb(test_name)
    except (ValueError, PermissionError):
        pass


# ═══════════════════════════════════════════════════════════════
# 无 KB 时的 guard 信息
# ═══════════════════════════════════════════════════════════════

def test_e2e_ui_no_kb_selected_guard():
    """无 KB 时运行，应显示「请先创建或选择知识库」提示。

    不删除真实 KB —— 通过 mock list_kbs 返回空列表模拟无 KB 场景。
    """
    from unittest.mock import patch

    with patch("kb_manager.list_kbs", return_value=[]):
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.run()
        assert not at.exception

        # st.info() 渲染为 Info 元素，通过 .value 获取文本
        info_elements = at.main.info
        assert len(info_elements) >= 1
        assert "请先在左侧创建或选择一个知识库" in info_elements[0].value
