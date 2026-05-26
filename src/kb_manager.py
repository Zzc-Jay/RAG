from __future__ import annotations
import json
import os
import shutil
from datetime import date

from config import DATA_DIR, BM25_DIR, CHROMA_DIR, REGISTRY_PATH
from audit import log_event


def _load_registry() -> dict:
    if not os.path.exists(REGISTRY_PATH):
        return {}
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_registry(registry: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
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
    for base in (CHROMA_DIR, BM25_DIR):
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
    for base in (CHROMA_DIR, BM25_DIR):
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
