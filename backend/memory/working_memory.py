"""工作记忆(Working Memory)：每次提问时从长期记忆检索 Top-3 相关片段并注入当前请求。"""
from __future__ import annotations

from ..config import settings
from . import milvus_store


def retrieve(user_id: str, question: str) -> list[dict]:
    """对用户问题向量化，检索 Top-K 且相似度 > 阈值 的长期记忆。"""
    return milvus_store.search_memories(
        user_id=user_id,
        query=question,
        top_k=settings.WORKING_MEM_TOPK,
        threshold=settings.WORKING_MEM_SIM,
    )


def format_block(memories: list[dict]) -> str:
    """注入格式：相关记忆：1. [内容]（产生于：[路径]）"""
    if not memories:
        return ""
    lines = ["【相关记忆(工作记忆)】"]
    for i, m in enumerate(memories, 1):
        path = m.get("gen_path") or "无特定语境"
        lines.append(f"{i}. {m.get('content','')}（产生于：{path}）")
    return "\n".join(lines)
