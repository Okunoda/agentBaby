"""记忆编排器：组装每次请求的上下文，并在每轮结束后触发记忆生成。

上下文组装顺序：
  [system: 高报师提示词]  (由 llm 层负责)
  + [system: 长期记忆快照]   —— 新会话 或 距上次活跃 > 60min(缓存失效) 时注入
  + [system: 相关记忆(工作记忆)] —— 每次提问按问题检索 Top-3
  + 历史消息 (若旧会话重连 或 上下文使用率>70% → 最近3轮之前压缩为 <session_memory>)
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

from ..config import settings
from ..db import Message, Session, SessionLocal
from ..llm import SYSTEM_PROMPT
from . import embedding, long_term, session_memory, working_memory, auto_memory


def _turns(msgs: list) -> int:
    return sum(1 for m in msgs if m.role == "user")


def _recent_cutoff(msgs: list, keep_turns: int) -> int:
    """返回索引：保留最近 keep_turns 轮(以 user 消息为界)，返回其起始下标。"""
    user_idx = [i for i, m in enumerate(msgs) if m.role == "user"]
    if len(user_idx) <= keep_turns:
        return 0
    return user_idx[-keep_turns]


def build_messages(db, session: Session) -> tuple[list[dict], dict]:
    """组装发送给 LLM 的消息列表(不含高报师 system，由 llm 层补)。

    要求：调用前当前用户消息已入库。session.last_active_at 仍为上一轮的时间。
    """
    user_id = session.user_id
    all_msgs = list(session.messages)  # 已按 id 排序，含本轮 user 消息
    prev_active = session.last_active_at or session.created_at
    gap_min = (datetime.utcnow() - prev_active).total_seconds() / 60.0

    turns_before = _turns(all_msgs) - 1  # 不含本轮
    cache_invalid = gap_min > settings.CACHE_INVALID_MINUTES
    is_new = turns_before <= 0
    inject_snapshot = is_new or cache_invalid
    reconnect = cache_invalid and turns_before > settings.SESSION_MEM_MIN_TURNS

    leading: list[dict] = []

    # 1) 长期记忆快照
    if inject_snapshot:
        snap = long_term.build_snapshot(user_id)
        if snap:
            leading.append({"role": "system", "content": snap})

    # 2) 工作记忆：对本轮问题检索 Top-3
    current_q = all_msgs[-1].content if all_msgs else ""
    wm = working_memory.retrieve(user_id, current_q)
    wm_block = working_memory.format_block(wm)
    if wm_block:
        leading.append({"role": "system", "content": wm_block})

    # 3) 历史(是否压缩)
    hist_msgs = [{"role": m.role, "content": m.content} for m in all_msgs]
    tokens = embedding.messages_tokens(hist_msgs)
    over_limit = tokens > settings.COMPRESS_RATIO * settings.CONTEXT_LIMIT_TOKENS

    debug = {
        "gap_min": round(gap_min, 1), "turns_before": turns_before,
        "inject_snapshot": inject_snapshot, "reconnect": reconnect,
        "tokens": tokens, "compressed": False, "working_memory_hits": len(wm),
        "working_memory_payload": [
            {"id": m.get("id"), "content": m.get("content"),
             "gen_path": m.get("gen_path"), "mem_type": m.get("mem_type"),
             "score": m.get("score")}
            for m in wm
        ],
    }

    if (reconnect or over_limit) and turns_before > settings.RECENT_TURNS_KEEP:
        # 需要压缩：确保有会话记忆(没有则同步生成)
        sm = session_memory.get(db, session.id)
        if not sm or not any(session_memory.to_dict(sm).values()):
            older_for_summary = [
                {"role": m.role, "content": m.content} for m in all_msgs[:-1]
            ]
            session_memory.generate_and_save(session.id, older_for_summary)
            sm = session_memory.get(db, session.id)
        cutoff = _recent_cutoff(all_msgs, settings.RECENT_TURNS_KEEP)
        block = session_memory.format_block(sm)
        compressed = ([{"role": "system", "content": block}] if block else []) + [
            {"role": m.role, "content": m.content} for m in all_msgs[cutoff:]
        ]
        history = compressed
        debug["compressed"] = True
    else:
        history = hist_msgs

    return leading + history, debug


# ---------- 每轮结束后的记忆生成(后台异步) ----------
def _bg_after_turn(session_id: str, user_id: str, user_text: str, assistant_text: str):
    conversation = f"user: {user_text}\nassistant: {assistant_text}"
    # Auto Memory：实时提取
    try:
        auto_memory.extract_and_store(user_id, session_id, conversation)
    except Exception as e:  # noqa: BLE001
        print(f"[auto_memory] error: {e}")

    # Session Memory：满足条件则更新
    try:
        with SessionLocal() as db:
            s = db.get(Session, session_id)
            if not s:
                return
            msgs = [{"role": m.role, "content": m.content} for m in s.messages]
            turns = sum(1 for m in msgs if m["role"] == "user")
            tokens = embedding.messages_tokens(msgs)
            if session_memory.should_update(turns, tokens):
                session_memory.generate_and_save(session_id, msgs)
    except Exception as e:  # noqa: BLE001
        print(f"[session_memory] error: {e}")


def schedule_after_turn(session_id: str, user_id: str, user_text: str, assistant_text: str):
    t = threading.Thread(
        target=_bg_after_turn, args=(session_id, user_id, user_text, assistant_text),
        daemon=True,
    )
    t.start()


def touch_session(db, session: Session):
    """更新会话活跃时间(在 build_messages 读取 prev_active 之后调用)。"""
    session.last_active_at = datetime.utcnow()
    db.commit()
