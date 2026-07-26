"""工作记忆(Working Memory)：每次提问时从长期记忆检索 Top-3 相关片段并注入当前请求。"""
from __future__ import annotations

from sqlalchemy import select

from ..config import settings
from ..db import MemoryConflict, SessionLocal
from . import milvus_store


def retrieve(user_id: str, question: str) -> list[dict]:
    """对用户问题向量化，检索 Top-K 且相似度 > 阈值 的长期记忆。"""
    return milvus_store.search_memories(
        user_id=user_id,
        query=question,
        top_k=settings.WORKING_MEM_TOPK,
        threshold=settings.WORKING_MEM_SIM,
    )


def open_conflicts_for(user_id: str, milvus_ids: list[int]) -> list[dict]:
    """返回与这些 milvus_id 关联的、status=open 的冲突，必要时过滤为另一侧文本 ≠ 已被删除的 id。"""
    if not milvus_ids:
        return []
    with SessionLocal() as db:
        rows = db.scalars(
            select(MemoryConflict).where(
                MemoryConflict.user_id == user_id,
                MemoryConflict.status == "open",
                (MemoryConflict.memory_existing_milvus_id.in_(milvus_ids))
                | (MemoryConflict.memory_new_milvus_id.in_(milvus_ids)),
            )
        ).all()
        # 另一侧的 id 现在在 Milvus 中可能已经不存在(被选后删掉了)，
        # 但 open 状态的 conflict 不会被 /choose 影响另一端的显示。
        return [{
            "id": r.id,
            "description": r.description,
            "memory_existing": r.memory_existing,
            "memory_existing_id": r.memory_existing_milvus_id,
            "memory_new": r.memory_new,
            "memory_new_id": r.memory_new_milvus_id,
        } for r in rows]


def format_block(memories: list[dict]) -> str:
    """注入格式：相关记忆：1. [内容]（产生于：[路径]）"""
    if not memories:
        return ""
    lines = ["【相关记忆(工作记忆)】"]
    for i, m in enumerate(memories, 1):
        path = m.get("gen_path") or "无特定语境"
        lines.append(f"{i}. {m.get('content','')}（产生于：{path}）")
    return "\n".join(lines)
