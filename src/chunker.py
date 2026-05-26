from __future__ import annotations
from langchain_text_splitters import RecursiveCharacterTextSplitter
from config import CHUNK_SIZE, CHUNK_OVERLAP


def split_pages(
    pages: list[dict],
    source: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[dict]:
    """按页切分文本，保留页面元数据（含表格和扫描标记）。

    pages: [{"page": 1, "text": "...", "tables": [...], "table_count": int, "is_scanned": bool}, ...]
    source: 文件名（如 "架构设计.pdf"），记录到每个 chunk

    返回: [{"text": "...", "source": str, "page": int, "chunk_idx": int,
            "has_table": bool, "is_scanned": bool}, ...]
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " ", ""],
    )

    chunks: list[dict] = []
    chunk_idx = 0

    for page_info in pages:
        page_num = page_info["page"]
        page_text = page_info["text"]
        has_table = page_info.get("table_count", 0) > 0
        is_scanned = page_info.get("is_scanned", False)

        page_chunks = splitter.split_text(page_text)
        for pc in page_chunks:
            chunks.append({
                "text": pc,
                "source": source,
                "page": page_num,
                "chunk_idx": chunk_idx,
                "has_table": has_table,
                "is_scanned": is_scanned,
            })
            chunk_idx += 1

    return chunks
