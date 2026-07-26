"""Auto Memory（实时提取管道）。

每轮对话结束后由模型判断是否需要提取记忆，并同时提取用户情绪(仅收集 bad case)。
- 用户显式"记住我喜欢xxx" → explicit，直接入库长期记忆。
- 模型自动提取 → 进入 auto_memory 暂存(status=pending)，待 Auto Dream 阶段
  经证据链去重/合并后再长期化。

提取项字段：记忆内容简介 / 类型 / 关联上下文(产生路径) / 证据链。
"""
from __future__ import annotations

from ..config import settings
from ..db import AutoMemory, SessionLocal
from ..llm import complete_json
from . import milvus_store

_PROMPT = """你是高考志愿咨询系统的记忆提取模块。阅读用户与助手的最新一轮对话(可含少量上文)，
判断是否有值得长期记住的用户**偏好/事实/约束**，并识别用户的情绪。

规则：
- 只提取关于"用户本人"的稳定信息(如喜好的专业/城市、分数位次、明确拒绝的选项等)，
  不要提取闲聊、常识或助手的话。
- 若用户明确说"记住/请记住…"，将该条 explicit 置为 true。
- 关联上下文：记录这条记忆产生时的语境(如“在650分/只能选软工或考古的讨论下”)。
- 证据链：引用对话中支持该记忆的原话或依据。
- 情绪(emotion)：从 [平静, 焦虑, 不满, 急切, 犹豫, 开心] 中选一个最贴切的；无明显情绪填 "平静"。

【本轮对话】
{conversation}

只输出 JSON：
{{"emotion":"平静","memories":[{{"brief":"记忆简介","type":"偏好|事实|约束","related_context":"产生路径","evidence":"证据","explicit":false}}]}}
无可提取记忆时 memories 为空数组。"""


def extract_and_store(user_id: str, session_id: str, conversation: str) -> dict:
    """同步执行的提取逻辑(由后台线程调用)。返回统计信息。"""
    data = complete_json(_PROMPT.format(conversation=conversation[:settings.AUTO_MEMORY_PROMPT_MAX_CHARS]))
    if not isinstance(data, dict):
        return {"extracted": 0}

    emotion = (data.get("emotion") or "平静").strip()
    memories = data.get("memories") or []
    inserted_direct, staged = 0, 0

    with SessionLocal() as db:
        if not memories:
            # 无记忆但记录情绪(bad case 收集，不参与整合)
            if emotion and emotion != "平静":
                db.add(AutoMemory(
                    user_id=user_id, session_id=session_id,
                    brief=f"用户情绪：{emotion}", mem_type="事实",
                    source="emotion", status="discarded", emotion=emotion,
                ))
                db.commit()
            return {"extracted": 0, "emotion": emotion}

        for m in memories:
            brief = (m.get("brief") or "").strip()
            if not brief:
                continue
            mem_type = (m.get("type") or "偏好").strip()
            ctx = (m.get("related_context") or "").strip()
            evidence = (m.get("evidence") or "").strip()
            explicit = bool(m.get("explicit"))

            if explicit:
                # 显式记忆：直接入库长期记忆
                milvus_store.add_memory(
                    user_id=user_id, content=brief, gen_path=ctx,
                    evidence=evidence, mem_type=mem_type, weight=1.5,
                )
                db.add(AutoMemory(
                    user_id=user_id, session_id=session_id, brief=brief,
                    mem_type=mem_type, related_context=ctx, evidence=evidence,
                    source="explicit", status="direct", emotion=emotion,
                ))
                inserted_direct += 1
            else:
                # 模型提取：进入待整合暂存
                db.add(AutoMemory(
                    user_id=user_id, session_id=session_id, brief=brief,
                    mem_type=mem_type, related_context=ctx, evidence=evidence,
                    source="model", status="pending", emotion=emotion,
                ))
                staged += 1
        db.commit()

    return {"extracted": len(memories), "direct": inserted_direct,
            "staged": staged, "emotion": emotion}
