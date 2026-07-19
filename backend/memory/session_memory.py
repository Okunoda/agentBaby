"""会话记忆(Session Memory)：当前会话的 6 区段结构化状态摘要。

区段(针对志愿填报裁剪):
  学生画像 / 临时假设 / 硬约束 / 当前任务阶段 / 推荐快照 / 待确认事项

- 更新触发：每轮结束后，若 (轮次 > 3) 或 (token > 15k)，异步调用大模型，
  传入 对话内容 + 已存在的 session 摘要，只更新有变化的字段。
- 压缩用途：会话上下文接近上限(70%)时，用本摘要替换"最近3轮之前"的所有消息。
"""
from __future__ import annotations

import json

from ..config import settings
from ..db import SessionMemory, SessionLocal
from ..llm import complete_json
from . import embedding

FIELDS = [f for f, _ in SessionMemory.SECTIONS]


def get(db, session_id: str) -> SessionMemory | None:
    return db.get(SessionMemory, session_id)


def to_dict(sm: SessionMemory | None) -> dict:
    if not sm:
        return {f: "" for f in FIELDS}
    return {f: getattr(sm, f) or "" for f in FIELDS}


def format_block(sm: SessionMemory | None) -> str:
    """渲染为 <session_memory> 块。空摘要返回空串。"""
    if not sm:
        return ""
    d = to_dict(sm)
    if not any(d.values()):
        return ""
    lines = ["<session_memory>", "（本会话早期消息已压缩为以下结构化状态摘要）"]
    for field, label in SessionMemory.SECTIONS:
        val = (d.get(field) or "").strip()
        if val:
            lines.append(f"[{label}] {val}")
    lines.append("</session_memory>")
    return "\n".join(lines)


def should_update(turns: int, tokens: int) -> bool:
    return turns > settings.SESSION_MEM_MIN_TURNS or tokens > settings.SESSION_MEM_MIN_TOKENS


_PROMPT = """你在维护一次「高考志愿填报」咨询会话的结构化状态摘要。请阅读【对话内容】与【已有摘要】，
生成/更新 6 个区段。只在有新信息时改动对应区段，无变化则原样保留已有内容。

区段说明：
- student_profile 学生画像：分数、全省位次、选科组合、省份等真实信息
- assumptions 临时假设：用户说的“假如我考到xx分”等假设，必须标注为【假设】，其优先级高于真实分数
- hard_constraints 硬约束：地域、预算、绝不接受的院校/专业（不可丢失）
- task_stage 当前任务阶段：初筛 / 精准匹配 / 风险评估 / 方案讨论 其一
- recommendation_snapshot 推荐快照：当前候选院校/专业列表及简短理由
- pending_questions 待确认事项：你已提问但用户尚未回复的问题

【已有摘要】
{existing}

【对话内容】
{conversation}

请只输出 JSON（键为上述英文字段名，值为中文字符串，无内容用空字符串）：
{{"student_profile":"","assumptions":"","hard_constraints":"","task_stage":"","recommendation_snapshot":"","pending_questions":""}}"""


def generate_and_save(session_id: str, messages: list[dict]) -> dict | None:
    """调用大模型生成 6 区段并合并保存(只更新有变化/非空的字段)。"""
    with SessionLocal() as db:
        sm = db.get(SessionMemory, session_id)
        existing = json.dumps(to_dict(sm), ensure_ascii=False, indent=2)
        conversation = "\n".join(
            f"{m['role']}: {m['content']}" for m in messages if m.get("role") in ("user", "assistant")
        )
        prompt = _PROMPT.format(existing=existing, conversation=conversation[:12000])
        data = complete_json(prompt)
        if not isinstance(data, dict):
            return None

        if not sm:
            sm = SessionMemory(session_id=session_id)
            db.add(sm)
        changed = {}
        for f in FIELDS:
            new_val = (data.get(f) or "").strip()
            if new_val and new_val != (getattr(sm, f) or "").strip():
                setattr(sm, f, new_val)
                changed[f] = new_val
        db.commit()
        return changed
