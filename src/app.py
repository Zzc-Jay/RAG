from __future__ import annotations
import html as _html
import os
import re
import tempfile
from urllib.parse import unquote as _url_unquote
import streamlit as st

from loader import load_document, SUPPORTED_TYPES
from chunker import split_pages
from embedder import add_to_kb, delete_doc_chunks, get_all_chunks
from bm25_index import build_index as build_bm25_index
from retriever import retrieve
from generator import generate_stream
from config import MODEL_LABELS, MODEL_PRICING, get_api_keys
from providers import PROVIDER_REGISTRY, get_provider
from kb_manager import (
    create_kb,
    delete_kb,
    list_kbs,
    add_doc,
    remove_doc,
    get_kb_docs,
)
from security import (
    validate_question,
    validate_kb_name,
    validate_file_extension,
    RateLimiter,
)
from logging_config import setup_logging, get_logger
from conversation import (
    create_conversation,
    add_turn,
)
from token_tracker import TokenTracker, format_cost
from audit import log_event as audit_log, get_events as audit_get_events, get_stats as audit_get_stats

# 初始化日志
setup_logging()
logger = get_logger("app")

st.set_page_config(page_title="RAG 知识库问答", page_icon="📚", layout="wide")

URL_RE = re.compile(r"^https?://", re.IGNORECASE)

MAX_DOC_NAME_LEN = 12  # 文档名在 UI 中显示的最大字符数


def _truncate_name(name: str, max_len: int = MAX_DOC_NAME_LEN) -> str:
    """截断过长的文档名，超出部分用省略号。"""
    if len(name) <= max_len:
        return name
    return name[:max_len] + "..."

# ── 自定义 CSS ───────────────────────────────────────────────────
st.markdown("""
<style>
    /* === 全局 === */
    [data-testid="stAppViewContainer"] { background: #ffffff; }
    [data-testid="stSidebar"] { background: #f4f5f7; }
    [data-testid="stAppViewContainer"] > .block-container {
        padding-top: 1rem !important;
        padding-bottom: 1rem !important;
    }

    /* === 侧边栏输入控件 === */
    [data-testid="stSidebar"] input[type="text"],
    [data-testid="stSidebar"] .stSelectbox [data-baseweb="select"] > div,
    [data-testid="stSidebar"] [data-testid="stFileUploader"] {
        border: 1px solid #d1d5db !important;
        border-radius: 8px !important;
        background: #ffffff !important;
    }
    [data-testid="stSidebar"] input[type="text"]:focus {
        border-color: #4a90d9 !important;
        box-shadow: 0 0 0 2px rgba(74,144,217,0.15) !important;
    }

    /* === 按钮 === */
    [data-testid="stSidebar"] .stButton > button {
        border-radius: 8px !important;
        font-size: 0.82rem !important;
        transition: all 0.15s;
    }
    [data-testid="stSidebar"] .stButton > button[kind="primary"] {
        background: #4a90d9 !important;
        border-color: #4a90d9 !important;
        color: #fff !important;
    }
    [data-testid="stSidebar"] .stButton > button[kind="secondary"] {
        background: #fff !important;
        border-color: #d1d5db !important;
        color: #374151 !important;
    }

    [data-testid="stSidebar"] h2 { font-size: 1rem !important; margin-top: 0.5rem !important; }
    [data-testid="stSidebar"] .stTabs [data-baseweb="tab"] {
        border-radius: 6px 6px 0 0 !important;
        font-size: 0.8rem !important;
        padding: 0.4rem 0.8rem !important;
    }

    [data-testid="stForm"] small,
    [data-testid="stForm"] [data-testid="stCaptionContainer"],
    [data-testid="stForm"] .st-caption,
    [data-testid="stForm"] div[data-testid*="caption"],
    [data-testid="InputInstructions"] { display: none !important; }

    /* === 对话气泡 === */
    .q-bubble {
        background: #f0f4ff;
        border-left: 4px solid #4a90d9;
        border-radius: 0 10px 10px 10px;
        padding: 0.7rem 1rem;
        margin: 0.5rem 0 0.3rem 0;
        font-weight: 500;
        color: #1a1a1a;
    }
    .a-block {
        background: linear-gradient(135deg, #f8f9fa 0%, #ffffff 100%);
        border-left: 4px solid #34a853;
        border-radius: 0 10px 10px 10px;
        padding: 1rem 1.2rem;
        margin: 0.3rem 0 0.3rem 0;
        line-height: 1.8;
        font-size: 0.95rem;
    }

    /* === 操作栏 === */
    .action-bar {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        margin: 0.2rem 0 0.8rem 0.5rem;
        font-size: 0.78rem;
        color: #888;
    }

    /* === Token 统计条 === */
    .token-bar {
        color: #888;
        font-size: 0.75rem;
        margin-right: 0.8rem;
    }

    /* === 来源 === */
    .source-item {
        background: #fafbfc;
        border: 1px solid #e8ecf1;
        border-radius: 8px;
        padding: 0.7rem 1rem;
        margin-bottom: 0.45rem;
        font-size: 0.86rem;
    }
    .source-label { font-weight: 600; color: #2c3e50; }
    .source-page {
        background: #e8f0fe;
        color: #3b6cb4;
        border-radius: 4px;
        padding: 1px 7px;
        font-size: 0.76rem;
        margin-left: 6px;
    }

    /* === 文档列表 === */
    .doc-item {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0.35rem 0;
        border-bottom: 1px solid #e5e7eb;
        font-size: 0.82rem;
    }
    .doc-item:last-child { border-bottom: none; }

    .kb-badge {
        display: inline-block;
        background: #ecfdf5;
        color: #059669;
        border-radius: 6px;
        padding: 2px 10px;
        font-size: 0.76rem;
        font-weight: 600;
    }

    /* === 对话操作栏：去掉按钮边框 + 垂直居中 === */
    [data-testid="stHorizontalBlock"] {
        align-items: center !important;
    }
    [data-testid="stHorizontalBlock"] [data-testid="stButton"] button[kind="tertiary"] {
        border: none !important;
        background: transparent !important;
        box-shadow: none !important;
        padding: 0.15rem 0.3rem !important;
        font-size: 0.9rem !important;
        min-height: 1.6rem !important;
        line-height: 1 !important;
    }
    [data-testid="stHorizontalBlock"] [data-testid="stButton"] button[kind="tertiary"]:hover {
        background: #f0f0f0 !important;
        border-radius: 4px !important;
    }
    /* 操作栏 caption 间距归零 */
    [data-testid="stHorizontalBlock"] [data-testid="stCaptionContainer"] {
        margin: 0 !important;
        padding: 0 !important;
    }
</style>
""", unsafe_allow_html=True)

# ── 初始化 session state ──────────────────────────────────────────
if "kb_list" not in st.session_state:
    st.session_state.kb_list = list_kbs()
if "current_kb" not in st.session_state:
    existing = list_kbs()
    st.session_state.current_kb = existing[0] if existing else None
if "show_create" not in st.session_state:
    st.session_state.show_create = False
if "show_delete_confirm" not in st.session_state:
    st.session_state.show_delete_confirm = False
if "kb_version" not in st.session_state:
    st.session_state.kb_version = 0
if "input_version" not in st.session_state:
    st.session_state.input_version = 0
if "rate_limiter" not in st.session_state:
    st.session_state.rate_limiter = RateLimiter()
if "conversation" not in st.session_state:
    st.session_state.conversation = create_conversation()
if "token_tracker" not in st.session_state:
    st.session_state.token_tracker = TokenTracker()
if "llm_model" not in st.session_state:
    import config
    st.session_state.llm_model = config.LLM_MODEL
# 跟踪每轮回答的 token 用量（用于渲染统计条）
if "turn_usage" not in st.session_state:
    st.session_state.turn_usage: list[tuple[int, int]] = []
# 用于触发 UI 刷新
if "answer_counter" not in st.session_state:
    st.session_state.answer_counter = 0
# 检索策略配置（即时生效）
if "retrieval_top_k" not in st.session_state:
    st.session_state.retrieval_top_k = 5
if "retrieval_rrf_k" not in st.session_state:
    st.session_state.retrieval_rrf_k = 60
if "retrieval_vector_weight" not in st.session_state:
    st.session_state.retrieval_vector_weight = 1.0
if "retrieval_bm25_weight" not in st.session_state:
    st.session_state.retrieval_bm25_weight = 1.0
# 查询改写 & 重排序
if "enable_rewrite" not in st.session_state:
    st.session_state.enable_rewrite = False
if "enable_rerank" not in st.session_state:
    st.session_state.enable_rerank = False
# 批量选择模式
if "batch_delete_mode" not in st.session_state:
    st.session_state.batch_delete_mode = False


def refresh_kb_list() -> None:
    st.session_state.kb_list = list_kbs()
    if st.session_state.current_kb not in st.session_state.kb_list:
        st.session_state.current_kb = st.session_state.kb_list[0] if st.session_state.kb_list else None


def rebuild_bm25(kb_name: str) -> None:
    chunks = get_all_chunks(kb_name)
    if chunks:
        build_bm25_index(chunks, kb_name)


def switch_kb(name: str) -> None:
    st.session_state.current_kb = name
    st.session_state.kb_version += 1
    st.session_state.conversation = create_conversation()
    st.session_state.turn_usage = []


def clear_inputs() -> None:
    st.session_state.input_version += 1


# ── 文档处理核心逻辑 ──────────────────────────────────────────────
def process_document(source: str, doc_type: str, display_name: str, kb_name: str, status) -> bool:
    status.write(f"  - 提取文本 ({SUPPORTED_TYPES.get(doc_type, doc_type)})...")
    pages = load_document(source, doc_type)

    if not pages:
        status.warning(f"  - {display_name}: 未提取到文字内容，已跳过")
        return False

    # 统计表格和扫描页
    total_tables = sum(p.get("table_count", 0) for p in pages)
    scanned_pages = [p["page"] for p in pages if p.get("is_scanned")]

    if total_tables:
        status.write(f"  - 检测到 {total_tables} 个表格")
    if scanned_pages:
        status.warning(f"  - 第{scanned_pages}页为图片型页面，建议 OCR 预处理")

    status.write(f"  - 切分文本 ({len(pages)} 页/段)...")
    chunks = split_pages(pages, source=display_name)

    if not chunks:
        status.warning(f"  - {display_name}: 文本过短无法切分，已跳过")
        return False

    status.write(f"  - 向量嵌入 ({len(chunks)} 个片段)...")
    delete_doc_chunks(kb_name, display_name)
    add_to_kb(chunks, kb_name, token_tracker=st.session_state.token_tracker)

    status.write("  - 构建 BM25 索引...")
    rebuild_bm25(kb_name)

    add_doc(kb_name, display_name, len(pages), len(chunks), doc_type)

    # 审计日志
    event_type = "doc.url" if doc_type == "url" else "doc.upload"
    audit_log(event_type, kb_name=kb_name, details={
        "doc_name": display_name,
        "pages": len(pages),
        "chunks": len(chunks),
        "doc_type": SUPPORTED_TYPES.get(doc_type, doc_type),
    })

    summary = f"  - 完成 ({len(pages)} 页, {len(chunks)} 片段"
    if total_tables:
        summary += f", {total_tables} 表格"
    summary += ")"
    status.write(summary)
    logger.info(f"文档入库完成: '{display_name}' -> 知识库 '{kb_name}'，{len(chunks)} 个片段"
                f"{'，' + str(total_tables) + ' 个表格' if total_tables else ''}")
    return True


def _export_conversation_md() -> str:
    """将当前对话导出为 Markdown 文本。"""
    conv = st.session_state.conversation
    if not conv:
        return "暂无对话记录"

    lines = [
        f"# RAG 对话记录",
        f"知识库: {st.session_state.current_kb}",
        f"导出时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
    ]

    for i, turn in enumerate(conv, start=1):
        lines.append(f"## 第 {i} 轮")
        lines.append(f"**问题**: {turn['question']}")
        lines.append(f"**回答**: {turn['answer']}")
        if turn.get("references"):
            lines.append("**参考来源**:")
            for ref in turn["references"]:
                lines.append(f"- [{ref['source']}] 第{ref['page']}页: {ref['text'][:100]}...")
        lines.append("")

    return "\n".join(lines)


# ── 侧边栏 ────────────────────────────────────────────────────────
with st.sidebar:
    # ── 模型选择器 ──────────────────────────────────────────────
    st.markdown("## 🤖 模型选择")

    available_keys = get_api_keys()
    # 构建可选模型列表（只显示已配 API Key 的）
    model_options: list[tuple[str, str]] = []  # [(model_name, label)]
    for model_name, (provider_cls, extra_kwargs) in PROVIDER_REGISTRY.items():
        from providers import _get_required_key_name
        key_name = _get_required_key_name(provider_cls, extra_kwargs)
        # 检查是否有可用的 Key
        has_key = bool(available_keys.get(key_name, ""))
        if not has_key and "base_url" in extra_kwargs:
            # OpenAI-compatible: fallback to OPENAI_API_KEY
            has_key = bool(available_keys.get("OPENAI_API_KEY", ""))
        if not has_key:
            # DashScope fallback
            has_key = bool(available_keys.get("DASHSCOPE_API_KEY", ""))
        if has_key:
            label = MODEL_LABELS.get(model_name, model_name)
            model_options.append((model_name, label))

    if not model_options:
        model_options = [("qwen-plus", MODEL_LABELS.get("qwen-plus", "Qwen Plus"))]

    # 排序: DashScope → DeepSeek → 豆包
    def _provider_order(item: tuple[str, str]) -> int:
        m = item[0]
        if m.startswith("qwen"): return 0
        if m.startswith("deepseek"): return 1
        if m.startswith("doubao"): return 2
        return 3

    model_options.sort(key=_provider_order)

    label_to_model = {label: model for model, label in model_options}
    current_model = st.session_state.llm_model
    current_label = MODEL_LABELS.get(current_model, current_model)
    if current_label not in label_to_model:
        current_label = model_options[0][1] if model_options else "Qwen Plus (通义千问)"

    selected_label = st.selectbox(
        "生成模型",
        options=list(label_to_model.keys()),
        index=list(label_to_model.keys()).index(current_label) if current_label in label_to_model else 0,
        label_visibility="collapsed",
    )
    selected_model = label_to_model[selected_label]

    if selected_model != st.session_state.llm_model:
        st.session_state.llm_model = selected_model
        # 更新 token 定价
        pricing = MODEL_PRICING.get(selected_model, {})
        st.session_state.token_tracker.update_pricing(pricing)
        # 切换模型时重置对话（不同模型上下文不共享）
        st.session_state.conversation = create_conversation()
        st.session_state.turn_usage = []
        logger.info(f"模型切换: -> {selected_model}")
        st.rerun()

    st.divider()

    # ── 检索策略配置 ────────────────────────────────────────────
    st.markdown("## 🎯 检索策略")

    with st.expander("参数调整", expanded=False):
        new_top_k = st.slider(
            "返回片段数 (top-K)",
            min_value=1, max_value=20,
            value=st.session_state.retrieval_top_k,
            step=1,
            help="最终返回给 LLM 的文档片段数量。越大上下文越多，但可能稀释关键信息。",
        )
        new_rrf_k = st.slider(
            "RRF 融合参数 (K)",
            min_value=0, max_value=120,
            value=st.session_state.retrieval_rrf_k,
            step=10,
            help="K=0 时排名影响最大（第一名和第十名分数差距大），K=60 时较平滑。",
        )

        balance = st.select_slider(
            "检索偏好",
            options=["纯关键词", "偏向关键词", "均衡", "偏向语义", "纯语义"],
            value=(
                "纯关键词" if st.session_state.retrieval_vector_weight == 0.0 else
                "偏向关键词" if st.session_state.retrieval_bm25_weight > st.session_state.retrieval_vector_weight else
                "均衡" if st.session_state.retrieval_vector_weight == st.session_state.retrieval_bm25_weight else
                "偏向语义" if st.session_state.retrieval_bm25_weight > 0.0 else
                "纯语义"
            ),
            help="语义检索擅长理解意思，关键词检索擅长匹配专有名词和代码。",
        )

        # 将选项映射为权重
        balance_map = {
            "纯关键词":   (0.0, 1.0),
            "偏向关键词": (0.5, 1.0),
            "均衡":       (1.0, 1.0),
            "偏向语义":   (1.0, 0.5),
            "纯语义":     (1.0, 0.0),
        }
        vw, bw = balance_map[balance]

        if (new_top_k != st.session_state.retrieval_top_k
                or new_rrf_k != st.session_state.retrieval_rrf_k
                or vw != st.session_state.retrieval_vector_weight
                or bw != st.session_state.retrieval_bm25_weight):
            st.session_state.retrieval_top_k = new_top_k
            st.session_state.retrieval_rrf_k = new_rrf_k
            st.session_state.retrieval_vector_weight = vw
            st.session_state.retrieval_bm25_weight = bw
            st.caption("✅ 参数已更新，下次提问即时生效")

    st.divider()

    # ── 高级检索功能 ──────────────────────────────────────────
    st.markdown("## 🧠 高级检索")

    new_rewrite = st.checkbox(
        "查询改写",
        value=st.session_state.enable_rewrite,
        help="基于对话历史，将模糊问题（如'它有什么优点？'）改写为独立完整的问题，提升多轮对话检索质量。",
    )
    if new_rewrite != st.session_state.enable_rewrite:
        st.session_state.enable_rewrite = new_rewrite

    new_rerank = st.checkbox(
        "LLM 精排",
        value=st.session_state.enable_rerank,
        help="用 LLM 对检索结果重新打分排序，精确度更高但增加一次 API 调用。",
    )
    if new_rerank != st.session_state.enable_rerank:
        st.session_state.enable_rerank = new_rerank

    st.divider()

    st.markdown("## 📚 知识库")

    kb_names = st.session_state.kb_list
    if kb_names:
        current = st.session_state.current_kb
        default_idx = kb_names.index(current) if current in kb_names else 0

        selected = st.selectbox(
            "当前知识库",
            kb_names,
            index=default_idx,
            key=f"kb_selector_{st.session_state.kb_version}",
            label_visibility="collapsed",
        )
        if selected != st.session_state.current_kb:
            switch_kb(selected)
            st.rerun()

        st.markdown(f'<span class="kb-badge">已选择</span>', unsafe_allow_html=True)
    else:
        st.info("尚未创建知识库")

    # ── 新建 / 删除 ────────────────────────────────────────────────
    c_new, c_del = st.columns(2)
    with c_new:
        if st.button("新建知识库", use_container_width=True):
            st.session_state.show_create = not st.session_state.show_create
            st.session_state.show_delete_confirm = False
            st.rerun()
    with c_del:
        del_disabled = not st.session_state.current_kb
        if st.button("删除知识库", use_container_width=True, disabled=del_disabled):
            st.session_state.show_delete_confirm = not st.session_state.show_delete_confirm
            st.session_state.show_create = False
            st.rerun()

    if st.session_state.show_create:
        with st.form(key="create_kb_form", clear_on_submit=True):
            new_name = st.text_input(
                "知识库名称",
                placeholder="输入名称后按回车",
                key="new_kb_input",
                label_visibility="collapsed",
            )
            fc1, fc2 = st.columns([1, 1])
            submitted_create = fc1.form_submit_button("确认创建", use_container_width=True, type="primary")
            cancelled_create = fc2.form_submit_button("取消", use_container_width=True)

        if submitted_create:
            try:
                name = validate_kb_name(new_name)
                create_kb(name)
                refresh_kb_list()
                switch_kb(name)
                clear_inputs()
                st.session_state.show_create = False
                logger.info(f"知识库已创建: '{name}'")
                st.success(f"'{name}' 已创建")
                st.rerun()
            except ValueError as e:
                st.error(str(e))
        if cancelled_create:
            st.session_state.show_create = False
            st.rerun()

    if st.session_state.show_delete_confirm and st.session_state.current_kb:
        st.warning(f"确定删除「{st.session_state.current_kb}」？此操作不可恢复。")
        c1, c2 = st.columns(2)
        if c1.button("确认删除", use_container_width=True, type="primary"):
            try:
                name = st.session_state.current_kb
                delete_kb(name)
                refresh_kb_list()
                st.session_state.kb_version += 1
                st.session_state.show_delete_confirm = False
                st.session_state.conversation = create_conversation()
                st.session_state.turn_usage = []
                logger.warning(f"知识库已删除: '{name}'")
                st.rerun()
            except ValueError as e:
                st.error(str(e))
        if c2.button("取消", use_container_width=True):
            st.session_state.show_delete_confirm = False
            st.rerun()

    st.divider()

    # ── 文档管理 ────────────────────────────────────────────────
    st.markdown("## 📄 文档")

    if st.session_state.current_kb:
        tab_file, tab_url = st.tabs(["上传文件", "网页链接"])

        with tab_file:
            uploaded_files = st.file_uploader(
                "支持 PDF / TXT / MD / DOCX",
                type=["pdf", "txt", "md", "docx"],
                accept_multiple_files=True,
                key=f"file_uploader_{st.session_state.input_version}",
                label_visibility="collapsed",
            )
            if uploaded_files:
                invalid_files = []
                for uf in uploaded_files:
                    try:
                        validate_file_extension(uf.name)
                    except ValueError:
                        invalid_files.append(uf.name)
                if invalid_files:
                    st.error(f"不支持的文件类型: {', '.join(invalid_files)}")

                if st.button(f"处理 {len(uploaded_files)} 个文件", use_container_width=True, type="primary"):
                    kb_name = st.session_state.current_kb
                    with st.status("处理文档中...", expanded=True) as status:
                        for uf in uploaded_files:
                            try:
                                validate_file_extension(uf.name)
                            except ValueError:
                                status.warning(f"  - {uf.name}: 不支持的文件类型，已跳过")
                                continue
                            st.write(f"**{uf.name}**")
                            ext = os.path.splitext(uf.name)[1].lower()
                            try:
                                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                                    tmp.write(uf.read())
                                    tmp_path = tmp.name
                                try:
                                    process_document(tmp_path, ext, uf.name, kb_name, status)
                                finally:
                                    os.unlink(tmp_path)
                            except Exception as e:
                                logger.error(f"处理文件失败 '{uf.name}': {e}")
                                status.error(f"  - 处理失败: {e}")
                        status.update(label="处理完成！", state="complete")
                    clear_inputs()
                    st.rerun()

        with tab_url:
            url_input = st.text_area(
                "网页 URL（每行一个）",
                placeholder="https://example.com/article1\nhttps://example.com/article2",
                key=f"url_input_{st.session_state.input_version}",
                label_visibility="collapsed",
                height=100,
            )
            if st.button("抓取并入库", use_container_width=True, key="add_url_btn"):
                urls = [u.strip() for u in url_input.splitlines() if u.strip()]
                if not urls:
                    st.error("请输入至少一个 URL")
                else:
                    kb_name = st.session_state.current_kb
                    with st.status(f"处理 {len(urls)} 个网页...", expanded=True) as status:
                        success = 0
                        for url in urls:
                            if not URL_RE.match(url):
                                status.warning(f"  - {url}: 无效链接，已跳过")
                                continue
                            raw_name = _url_unquote(url.rstrip("/").rsplit("/", 1)[-1]) or "网页"
                            display = _truncate_name(raw_name)
                            st.write(f"**{display}**")
                            try:
                                ok = process_document(url, "url", display, kb_name, status)
                                if ok:
                                    success += 1
                            except Exception as e:
                                logger.error(f"抓取 URL 失败 '{url}': {e}")
                                status.error(f"  - 抓取失败: {e}")
                        status.update(label=f"完成: {success}/{len(urls)} 个网页入库", state="complete")
                    clear_inputs()
                    st.rerun()

        st.divider()

        try:
            docs = get_kb_docs(st.session_state.current_kb)
        except ValueError:
            docs = []

        if docs:
            with st.expander(f"已入库 {len(docs)} 个文档", expanded=False):
                # ── 批量操作栏（在文档列表上方，设置 checkbox keys 在前，渲染在后）──
                if st.session_state.batch_delete_mode:
                    all_names = [d["name"] for d in docs]
                    selected_names = [n for n in all_names if st.session_state.get(f"batch_sel_{n}", False)]
                    all_checked = len(selected_names) == len(all_names) and len(all_names) > 0

                    c_sel_all, c_del, c_exit = st.columns([2, 2, 2])
                    with c_sel_all:
                        if st.button(
                            "取消全选" if all_checked else "全选",
                            use_container_width=True, key="toggle_all",
                        ):
                            for n in all_names:
                                st.session_state[f"batch_sel_{n}"] = not all_checked
                            st.rerun()
                    with c_del:
                        if selected_names:
                            if st.button(
                                f"删除所选 ({len(selected_names)})",
                                use_container_width=True, type="primary", key="batch_delete_btn",
                            ):
                                kb_name = st.session_state.current_kb
                                with st.status(f"批量删除 {len(selected_names)} 个文档...", expanded=True) as status:
                                    deleted = 0
                                    for doc_name in selected_names:
                                        st.write(f"  - {_truncate_name(doc_name)}")
                                        try:
                                            delete_doc_chunks(kb_name, doc_name)
                                            remove_doc(kb_name, doc_name)
                                            audit_log("doc.delete", kb_name=kb_name, details={"doc_name": doc_name})
                                            deleted += 1
                                        except Exception as e:
                                            status.error(f"    删除失败: {e}")
                                    rebuild_bm25(kb_name)
                                    status.update(label=f"已删除 {deleted} 个文档", state="complete")
                                for n in all_names:
                                    st.session_state.pop(f"batch_sel_{n}", None)
                                st.session_state.batch_delete_mode = False
                                audit_log("doc.delete.batch", kb_name=kb_name, details={"doc_names": selected_names, "count": deleted})
                                st.rerun()
                        else:
                            st.caption("勾选文档后出现删除按钮")
                    with c_exit:
                        if st.button("退出批量模式", use_container_width=True, key="toggle_batch"):
                            for n in all_names:
                                st.session_state.pop(f"batch_sel_{n}", None)
                            st.session_state.batch_delete_mode = False
                            st.rerun()
                else:
                    if st.button("批量选择", use_container_width=False, key="toggle_batch"):
                        st.session_state.batch_delete_mode = True
                        st.rerun()

                # ── 文档列表 ──────────────────────────────────
                for doc in docs:
                    dtype = doc.get("type", ".pdf")
                    type_label = SUPPORTED_TYPES.get(dtype, dtype)
                    doc_name = doc["name"]

                    if st.session_state.batch_delete_mode:
                        c_chk, c_info = st.columns([0.5, 6.5])
                        with c_chk:
                            st.checkbox(
                                "", key=f"batch_sel_{doc_name}",
                                label_visibility="collapsed",
                            )
                        with c_info:
                            doc_display = _truncate_name(doc_name)
                            st.markdown(
                                f'<div class="doc-item">'
                                f'<span title="{_html.escape(doc_name)}">{_html.escape(doc_display)}</span>'
                                f'<span style="color:#888;font-size:0.75rem;">{type_label} · {doc["pages"]}页 · {doc["chunks"]}片段</span>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )
                    else:
                        c_a, c_b = st.columns([5, 1])
                        with c_a:
                            doc_display = _truncate_name(doc_name)
                            st.markdown(
                                f'<div class="doc-item">'
                                f'<span title="{_html.escape(doc_name)}">{_html.escape(doc_display)}</span>'
                                f'<span style="color:#888;font-size:0.75rem;">{type_label} · {doc["pages"]}页 · {doc["chunks"]}片段</span>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )
                        with c_b:
                            if st.button("✕", key=f"del_{doc_name}", help=f"移除 {doc_name}"):
                                delete_doc_chunks(st.session_state.current_kb, doc_name)
                                remove_doc(st.session_state.current_kb, doc_name)
                                rebuild_bm25(st.session_state.current_kb)
                                audit_log("doc.delete", kb_name=st.session_state.current_kb,
                                           details={"doc_name": doc_name})
                                st.rerun()
        else:
            st.caption("暂无文档，请上传文件或添加链接")

        # ── Session 统计面板 ──────────────────────────────────────
        st.divider()
        st.markdown("## 📊 用量统计")
        tracker: TokenTracker = st.session_state.token_tracker
        s = tracker.summary
        col1, col2 = st.columns(2)
        with col1:
            st.metric("总 tokens", f"{s['total_tokens']:,}")
        with col2:
            st.metric("估算费用", format_cost(s["estimated_cost"]))
        if s["total_tokens"] > 0:
            with st.expander("详情"):
                st.caption(f"嵌入: {s['embedding_tokens']:,} tokens")
                st.caption(f"生成输入: {s['generation_input']:,} tokens")
                st.caption(f"生成输出: {s['generation_output']:,} tokens")

        # 嵌入缓存统计
        st.divider()
        st.markdown("## 💾 嵌入缓存")
        from embedding_cache import get_stats as get_cache_stats, cache_hit_rate
        cs = get_cache_stats()
        cc1, cc2 = st.columns(2)
        with cc1:
            st.metric("命中率", f"{cache_hit_rate():.0%}")
        with cc2:
            st.metric("已缓存", f"{cs['total_cached']:,} 条")
        with st.expander("详情"):
            st.caption(f"命中: {cs['hits']:,} 次")
            st.caption(f"未命中: {cs['misses']:,} 次")
            est_saved = cs['hits'] * 0.0002  # 约 ¥0.0002/条
            st.caption(f"估算节省: ¥{est_saved:.4f}")

        # 审计日志查看器
        st.divider()
        st.markdown("## 📝 审计日志")
        with st.expander("操作记录", expanded=False):
            event_filter = st.multiselect(
                "事件类型",
                options=["kb.create", "kb.delete", "doc.upload", "doc.upload.batch", "doc.url", "doc.url.batch", "doc.delete", "doc.delete.batch", "query", "query.stream"],
                default=[],
                key="audit_event_filter",
                label_visibility="collapsed",
            )
            event_type_str = event_filter[0] if len(event_filter) == 1 else ""
            events = audit_get_events(event_type=event_type_str, limit=30)
            if events:
                rows = []
                for e in events:
                    d = e["details"]
                    summary = ""
                    if e["event_type"].startswith("doc."):
                        summary = d.get("doc_name", "")
                    elif e["event_type"].startswith("query"):
                        summary = d.get("query", "")[:60]
                    rows.append({
                        "时间": e["timestamp"][:19],
                        "事件": e["event_type"],
                        "知识库": e["kb_name"],
                        "详情": summary,
                    })
                st.dataframe(rows, use_container_width=True, hide_index=True,
                             column_config={
                                 "时间": st.column_config.TextColumn(width="small"),
                                 "事件": st.column_config.TextColumn(width="small"),
                                 "知识库": st.column_config.TextColumn(width="small"),
                                 "详情": st.column_config.TextColumn(width="medium"),
                             })
                # 导出
                import json as _json
                export_json = _json.dumps(events, ensure_ascii=False, indent=2, default=str)
                st.download_button(
                    "导出 JSON",
                    data=export_json,
                    file_name=f"audit-{__import__('datetime').datetime.now().strftime('%Y%m%d-%H%M%S')}.json",
                    mime="application/json",
                )
                total = audit_get_stats().get("total", 0)
                st.caption(f"共 {total} 条记录，显示最近 30 条")
            else:
                st.caption("暂无操作记录")

    else:
        st.info("请先创建知识库")


# ── 主区域 ────────────────────────────────────────────────────────
if st.session_state.current_kb:
    st.markdown(
        f'<div style="display:flex; align-items:baseline; gap:0.6rem; margin-bottom:0.6rem;">'
        f'<span style="font-size:1.35rem; font-weight:700; color:#111827;">RAG 知识库问答</span>'
        f'<span class="kb-badge">{st.session_state.current_kb}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        '<div style="margin-bottom:0.6rem;">'
        '<span style="font-size:1.35rem; font-weight:700; color:#111827;">RAG 知识库问答</span>'
        '</div>',
        unsafe_allow_html=True,
    )

if not st.session_state.current_kb:
    st.info("请先在左侧创建或选择一个知识库")
elif not get_kb_docs(st.session_state.current_kb):
    st.info("请先在左侧上传文档或添加网页链接")
else:
    # ── 对话线程渲染 ────────────────────────────────────────────
    conv = st.session_state.conversation
    turn_usages = st.session_state.turn_usage

    for idx, turn in enumerate(conv):
        # 问题气泡
        st.markdown(
            f'<div class="q-bubble">🙋 {turn["question"]}</div>',
            unsafe_allow_html=True,
        )
        # 回答
        st.markdown(
            f'<div class="a-block">{turn["answer"]}</div>',
            unsafe_allow_html=True,
        )

        # 操作栏
        with st.container():
            c_tok, c_copy, c_spacer = st.columns([2.5, 0.5, 7])

            # Token 用量
            with c_tok:
                if idx < len(turn_usages):
                    gen_in, gen_out = turn_usages[idx]
                    st.markdown(
                        f'<span style="font-size:0.78rem;color:#888;white-space:nowrap;">'
                        f'📊 {gen_in}+{gen_out} tokens</span>',
                        unsafe_allow_html=True,
                    )

            # 复制按钮 — 用 iframe 组件（不被 Streamlit 消毒），内含按钮 + 隐藏文本
            with c_copy:
                safe_text = _html.escape(turn["answer"])
                st.components.v1.html(
                    f"""<html><body style="margin:0;overflow:hidden;font-family:sans-serif;">
                    <div id="t" style="display:none;">{safe_text}</div>
                    <button onclick="
                        var el=document.getElementById('t');
                        var ta=document.createElement('textarea');
                        ta.value=el.textContent;
                        ta.style.position='fixed';ta.style.left='-9999px';
                        document.body.appendChild(ta);ta.select();
                        document.execCommand('copy');document.body.removeChild(ta);
                        this.textContent='✓';
                        var s=this;setTimeout(function(){{s.textContent='📋';}},1200);
                    " style="
                        border:none;background:transparent;cursor:pointer;
                        font-size:0.85rem;padding:0;line-height:1;color:#888;
                    " title="复制回答">📋</button>
                    </body></html>""",
                    height=28,
                    width=36,
                )

        # 参考来源
        refs = turn.get("references", [])
        if refs:
            with st.expander(f"📖 参考来源 ({len(refs)} 条)"):
                for i, d in enumerate(refs, start=1):
                    badges = ""
                    if d.get("has_table"):
                        badges += '<span style="background:#fef3c7;color:#92400e;border-radius:4px;padding:1px 6px;font-size:0.72rem;margin-left:4px;">📊表格</span>'
                    if d.get("is_scanned"):
                        badges += '<span style="background:#fee2e2;color:#991b1b;border-radius:4px;padding:1px 6px;font-size:0.72rem;margin-left:4px;">🖼图片型</span>'
                    st.markdown(
                        f'<div class="source-item">'
                        f'<span class="source-label">[{i}] {d["source"]}</span>'
                        f'<span class="source-page">第{d["page"]}页</span>'
                        f'{badges}'
                        f'<div style="color:#555;margin-top:0.3rem;font-size:0.84rem;">'
                        f'{d["text"][:280]}{"..." if len(d["text"]) > 280 else ""}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

    # ── 底部操作栏 ──────────────────────────────────────────
    if conv:
        c_b1, c_b2, c_b3 = st.columns([2, 2, 4])
        with c_b1:
            if st.button("清空对话", use_container_width=True):
                st.session_state.conversation = create_conversation()
                st.session_state.turn_usage = []
                st.rerun()
        with c_b2:
            md_content = _export_conversation_md()
            st.download_button(
                "导出对话",
                data=md_content,
                file_name=f"rag-conversation-{__import__('datetime').datetime.now().strftime('%Y%m%d-%H%M%S')}.md",
                mime="text/markdown",
                use_container_width=True,
            )

    # ── 提问区域 (st.chat_input 原生固定在页面底部) ──────────
    query = st.chat_input("输入你的问题，可以基于上文继续追问...")

    if query:
        query = query.strip()
        if not query:
            st.warning("请输入问题")
        else:
            rate_limiter: RateLimiter = st.session_state.rate_limiter
            if not rate_limiter.check("default"):
                st.warning("请求过于频繁，请稍等后重试")
                st.stop()

            try:
                safe_query = validate_question(query)
                logger.info(f"收到提问: '{safe_query[:80]}...' (知识库: {st.session_state.current_kb})")

                kb_name = st.session_state.current_kb

                # 准备对话历史文本（供查询改写使用）
                from conversation import get_history, format_for_prompt
                hist_text = ""
                if st.session_state.enable_rewrite and st.session_state.conversation:
                    hist_text = format_for_prompt(get_history(st.session_state.conversation, 5))

                results = retrieve(
                    safe_query, kb_name,
                    top_k=st.session_state.retrieval_top_k,
                    rrf_k=st.session_state.retrieval_rrf_k,
                    vector_weight=st.session_state.retrieval_vector_weight,
                    bm25_weight=st.session_state.retrieval_bm25_weight,
                    token_tracker=st.session_state.token_tracker,
                    rewrite=st.session_state.enable_rewrite,
                    history_text=hist_text,
                    rerank=st.session_state.enable_rerank,
                )

                # 在生成之前先展示问题（生成成功/失败都不丢问题）
                st.markdown(
                    f'<div class="q-bubble">🙋 {safe_query}</div>',
                    unsafe_allow_html=True,
                )

                if not results:
                    st.warning("未找到相关内容，请尝试换个问法")
                else:
                    # 流式生成（无 spinner，保证增量渲染）
                    answer_placeholder = st.empty()
                    answer_text = ""
                    for chunk in generate_stream(
                        safe_query, results,
                        conversation=st.session_state.conversation,
                        model=st.session_state.llm_model,
                    ):
                        answer_text += chunk
                        answer_placeholder.markdown(
                            f'<div class="a-block">{answer_text}<span style="animation:blink 1s step-end infinite;">▊</span></div>',
                            unsafe_allow_html=True,
                        )

                    gen_input = len(safe_query) + sum(len(r["text"]) for r in results)
                    gen_output = len(answer_text)
                    est_input = max(1, int(gen_input * 0.4))
                    est_output = max(1, int(gen_output * 0.4))

                    st.session_state.token_tracker.record_generation(est_input, est_output)
                    st.session_state.turn_usage.append((est_input, est_output))

                    audit_log("query", kb_name=kb_name, details={
                        "query": safe_query[:200],
                        "result_count": len(results),
                        "top_k": st.session_state.retrieval_top_k,
                        "rrf_k": st.session_state.retrieval_rrf_k,
                        "vector_weight": st.session_state.retrieval_vector_weight,
                        "bm25_weight": st.session_state.retrieval_bm25_weight,
                        "rewrite": st.session_state.enable_rewrite,
                        "rerank": st.session_state.enable_rerank,
                    })

                    add_turn(
                        st.session_state.conversation,
                        safe_query,
                        answer_text,
                        results,
                    )
                    st.session_state.answer_counter += 1

                    st.rerun()

            except ValueError as e:
                st.error(str(e))
            except RuntimeError as e:
                logger.error(f"问答失败: {e}")
                st.error(f"处理失败: {e}")
