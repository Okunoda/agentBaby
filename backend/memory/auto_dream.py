"""Auto Dream（异步整合管道）。

用户离线/非活跃时，由后台巡检触发(此处以进程内线程 + 时间/条数触发模拟消息队列)：
- 读取该用户近期 auto_memory(pending) 与已有长期记忆；
- 大模型进行去重/合并(基于证据链)，并检查冲突；
- 冲突不自动覆盖：生成"需澄清"待办(MemoryConflict)，等待用户确认；
- 整合出的凝练记忆写入长期记忆(入库时再经 0.95 去重)。
触发条件：pending 条数 > N，或 用户非活跃超过 M 分钟且有 pending。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..config import settings
from ..db import AutoMemory, DreamState, Session, SessionLocal
from ..llm import complete_json
from . import milvus_store

_worker_started = False

_PROMPT = """你是高考志愿系统的「记忆整合(Auto Dream)」模块。请整合该用户新积累的碎片记忆，
并与其已有长期记忆比对，去重/合并出更凝练的长期记忆，同时找出**冲突**。

原则：
- 合并语义重复或互补的碎片，保留最具体、证据最充分的表述，并综合其产生路径。
- 冲突(如过去"不学医" vs 新的"对临床医学感兴趣")**不要自动覆盖**，而是列入 conflicts，
  交由用户澄清。
- 只输出真正值得长期保存的用户偏好/事实/约束。

【已有长期记忆】
{existing}

【新碎片记忆(待整合)】
{fragments}

只输出 JSON：
{{"consolidated":[{{"content":"凝练后的记忆","type":"偏好|事实|约束","gen_path":"综合产生路径","evidence":"证据"}}],
"conflicts":[{{"description":"冲突/需澄清描述","existing":"已有记忆全文","new":"新记忆全文","existing_id":"已有记忆的id(从【已有长期记忆】列表中抄, 没有可填 null)","new_id":null}}]}}
注意：
- 冲突的 existing_id / new_id 尽可能填值，方便用户做"渐进式澄清"时定位到具体那一条。
- 若 new 端是新记忆(尚未入库)，new_id 留 null。"""


def should_dream(db, user_id: str) -> bool:
    pending = db.scalar(
        select(func.count()).select_from(AutoMemory).where(
            AutoMemory.user_id == user_id,
            AutoMemory.status == "pending",
            AutoMemory.source == "model",
        )
    )
    if not pending:
        return False
    if pending >= settings.DREAM_MIN_PENDING:
        return True
    # 用户非活跃判定：该用户所有会话的 last_active 均超过 M 分钟
    last_active = db.scalar(
        select(func.max(Session.last_active_at)).where(Session.user_id == user_id)
    )
    if last_active is None:
        return True
    idle = datetime.utcnow() - last_active > timedelta(minutes=settings.DREAM_IDLE_MINUTES)
    return idle


def run_dream(user_id: str, force: bool = False) -> dict:
    """执行一次整合。返回统计。"""
    with SessionLocal() as db:
        if not force and not should_dream(db, user_id):
            return {"ran": False, "reason": "not triggered"}

        rows = db.scalars(
            select(AutoMemory).where(
                AutoMemory.user_id == user_id,
                AutoMemory.status == "pending",
                AutoMemory.source == "model",
            )
        ).all()
        if not rows:
            return {"ran": False, "reason": "no pending"}

        fragments = "\n".join(
            f"- [{r.mem_type}] {r.brief}（语境：{r.related_context}；证据：{r.evidence}）"
            for r in rows
        )
        existing_rows = milvus_store.list_memories(user_id, limit=100)
        existing = "\n".join(
            f"- id={r.get('id')}[{r.get('mem_type')}] {r.get('content')}"
            for r in existing_rows
        ) or "（暂无）"

        data = complete_json(_PROMPT.format(existing=existing, fragments=fragments))
        consolidated, conflicts = [], []
        if isinstance(data, dict):
            consolidated = data.get("consolidated") or []
            conflicts = data.get("conflicts") or []

        # 写入凝练记忆(再经 0.95 去重)
        added = 0
        for c in consolidated:
            content = (c.get("content") or "").strip()
            if not content:
                continue
            milvus_store.add_memory(
                user_id=user_id, content=content,
                gen_path=(c.get("gen_path") or "").strip(),
                evidence=(c.get("evidence") or "").strip(),
                mem_type=(c.get("type") or "偏好").strip(), weight=2.0,
            )
            added += 1

        # 冲突 → 需澄清待办：模型若给出 existing_id / new_id 则精确对齐到 Milvus 记录；
        # 否则退化成按文本匹配最近一条候选
        from ..db import MemoryConflict
        conf_added = 0
        new_id_to_text = {(r.get("content", "") or "").strip(): r.get("id") for r in existing_rows}
        # 查找 pending 新碎片中的内容(模型标注的 "new") 以关联 milvus id
        pending_content_to_id = {(r.brief or "").strip(): None for r in rows}

        for cf in conflicts:
            desc = (cf.get("description") or "").strip()
            if not desc:
                continue
            existing_text = (cf.get("existing") or "").strip()
            new_text = (cf.get("new") or "").strip()

            existing_mid = (
                cf.get("existing_id")
                or new_id_to_text.get(existing_text)
                or _search_one(user_id, existing_text)
            )
            new_mid = cf.get("new_id") or _search_one(user_id, new_text) if new_text else None

            # 防止两侧 id 重复填了空 / 同号
            if existing_mid == new_mid:
                new_mid = None

            db.add(MemoryConflict(
                user_id=user_id, description=desc,
                memory_existing=existing_text,
                memory_existing_milvus_id=existing_mid,
                memory_new=new_text,
                memory_new_milvus_id=new_mid,
                status="open",
            ))
            conf_added += 1

        # 标记暂存已整合
        for r in rows:
            r.status = "merged"

        ds = db.get(DreamState, user_id)
        if not ds:
            ds = DreamState(user_id=user_id)
            db.add(ds)
        ds.last_dream_at = datetime.utcnow()
        db.commit()

        return {"ran": True, "processed": len(rows), "consolidated": added,
                "conflicts": conf_added}


def _search_one(user_id: str, text: str):
    """在长期记忆中找一条最相似的 milvus id，用于冲突定位。"""
    if not text:
        return None
    hits = milvus_store.search_memories(user_id, text, top_k=1, threshold=0.5)
    if hits:
        return int(hits[0]["id"])
    return None


# ---------- 后台巡检(模拟消息队列 + 分布式并发控制) ----------
def _loop():
    while True:
        try:
            with SessionLocal() as db:
                user_ids = db.scalars(
                    select(AutoMemory.user_id).where(
                        AutoMemory.status == "pending", AutoMemory.source == "model"
                    ).distinct()
                ).all()
            for uid in user_ids:
                try:
                    run_dream(uid)
                except Exception as e:  # noqa: BLE001
                    print(f"[auto_dream] user={uid} error: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"[auto_dream] loop error: {e}")
        time.sleep(settings.DREAM_INTERVAL_SEC)


def start_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    t = threading.Thread(target=_loop, daemon=True, name="auto-dream")
    t.start()
