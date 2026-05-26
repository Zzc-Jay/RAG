from __future__ import annotations
import json
import os
import shutil
from datetime import date

from config import DATA_DIR
from audit import log_event


def _get_user_data_dir() -> str:
    from auth import get_current_user_id
    uid = get_current_user_id()
    d = os.path.join(DATA_DIR, "users", uid)
    os.makedirs(d, exist_ok=True)
    return d


def _get_registry_path() -> str:
    return os.path.join(_get_user_data_dir(), "kb_registry.json")


def _get_chroma_dir() -> str:
    d = os.path.join(_get_user_data_dir(), "chroma_db")
    os.makedirs(d, exist_ok=True)
    return d


def _get_bm25_dir() -> str:
    d = os.path.join(_get_user_data_dir(), "bm25")
    os.makedirs(d, exist_ok=True)
    return d


def get_chroma_dir_for_kb() -> str:
    """供 embedder.py 获取用户隔离的 ChromaDB 根目录。"""
    return _get_chroma_dir()


def get_bm25_dir_for_kb() -> str:
    """供 bm25_index.py 获取用户隔离的 BM25 根目录。"""
    return _get_bm25_dir()


def _load_registry() -> dict:
    path = _get_registry_path()
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_registry(registry: dict) -> None:
    path = _get_registry_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)


def create_kb(name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("知识库名称不能为空")
    registry = _load_registry()
    if name in registry:
        raise ValueError(f"知识库 '{name}' 已存在")
    registry[name] = {"created": str(date.today()), "docs": []}
    _save_registry(registry)
    log_event("kb.create", kb_name=name)


def delete_kb(name: str) -> None:
    registry = _load_registry()
    if name not in registry:
        raise ValueError(f"知识库 '{name}' 不存在")
    del registry[name]
    _save_registry(registry)
    log_event("kb.delete", kb_name=name)
    chroma_dir = _get_chroma_dir()
    bm25_dir = _get_bm25_dir()
    for base in (chroma_dir, bm25_dir):
        target = os.path.join(base, name)
        if os.path.exists(target):
            shutil.rmtree(target)


def list_kbs() -> list[str]:
    return list(_load_registry().keys())


def rename_kb(old_name: str, new_name: str) -> None:
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("新名称不能为空")
    registry = _load_registry()
    if old_name not in registry:
        raise ValueError(f"知识库 '{old_name}' 不存在")
    if new_name in registry:
        raise ValueError(f"知识库 '{new_name}' 已存在")
    registry[new_name] = registry.pop(old_name)
    _save_registry(registry)
    chroma_dir = _get_chroma_dir()
    bm25_dir = _get_bm25_dir()
    for base in (chroma_dir, bm25_dir):
        old_dir = os.path.join(base, old_name)
        new_dir = os.path.join(base, new_name)
        if os.path.exists(old_dir):
            os.rename(old_dir, new_dir)


def add_doc(kb_name: str, doc_name: str, pages: int, chunks: int, doc_type: str = ".pdf") -> None:
    registry = _load_registry()
    if kb_name not in registry:
        raise ValueError(f"知识库 '{kb_name}' 不存在")
    kb = registry[kb_name]
    kb.setdefault("docs", [])
    kb["docs"] = [d for d in kb["docs"] if d["name"] != doc_name]
    kb["docs"].append({"name": doc_name, "pages": pages, "chunks": chunks, "type": doc_type})
    _save_registry(registry)


def remove_doc(kb_name: str, doc_name: str) -> None:
    registry = _load_registry()
    if kb_name not in registry:
        raise ValueError(f"知识库 '{kb_name}' 不存在")
    kb = registry[kb_name]
    kb["docs"] = [d for d in kb.get("docs", []) if d["name"] != doc_name]
    _save_registry(registry)


def add_docs_batch(kb_name: str, doc_infos: list[dict]) -> None:
    """批量添加文档记录。一次读写 registry，比逐个 add_doc 高效。"""
    registry = _load_registry()
    if kb_name not in registry:
        raise ValueError(f"知识库 '{kb_name}' 不存在")
    kb = registry[kb_name]
    kb.setdefault("docs", [])
    for info in doc_infos:
        kb["docs"] = [d for d in kb["docs"] if d["name"] != info["name"]]
        kb["docs"].append(info)
    _save_registry(registry)


def remove_docs_batch(kb_name: str, doc_names: list[str]) -> list[str]:
    """批量移除文档。返回成功移除的文档名列表。"""
    registry = _load_registry()
    if kb_name not in registry:
        raise ValueError(f"知识库 '{kb_name}' 不存在")
    kb = registry[kb_name]
    removed = []
    names_set = set(doc_names)
    new_docs = []
    for d in kb.get("docs", []):
        if d["name"] in names_set:
            removed.append(d["name"])
        else:
            new_docs.append(d)
    kb["docs"] = new_docs
    _save_registry(registry)
    return removed


def get_kb_docs(kb_name: str) -> list[dict]:
    registry = _load_registry()
    if kb_name not in registry:
        raise ValueError(f"知识库 '{kb_name}' 不存在")
    kb = registry[kb_name]
    docs = kb.get("docs", [])
    if not docs:
        docs = kb.get("pdfs", [])
    return docs
