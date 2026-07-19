"""长期记忆(Long-term Memory) 快照注入。

- 新会话/缓存失效(超过60min)：按 user_id(+类型) 取 last_access 最近的 top-k，
  控制在约 4000 字符，形成结构化长记忆快照。
- 快照中若某条记忆 last_access_time 距今超过 N 天，附时效提醒。
"""
from __future__ import annotations

import math
import time

from ..config import settings
from . import milvus_store

DAY = 86400


def build_snapshot(user_id: str) -> str:
    """构造结构化长期记忆快照文本（含时效提醒）。无记忆则返回空串。"""
    rows = milvus_store.snapshot_memories(
        user_id=user_id,
        top_k=settings.SNAPSHOT_TOPK,
        max_chars=settings.SNAPSHOT_MAX_CHARS,
    )
    if not rows:
        return ""

    now = int(time.time())
    # 按类型分组
    groups: dict[str, list[str]] = {"偏好": [], "事实": [], "约束": []}
    for r in rows:
        line = r.get("content", "")
        last = r.get("last_access_time", now)
        age_days = (now - last) / DAY
        if age_days > settings.STALE_DAYS:
            line += f"（⚠️ 这条记忆已经是 {math.ceil(age_days)} 天前的内容，请确认是否仍符合当前情况）"
        groups.setdefault(r.get("mem_type", "偏好"), []).append(line)

    parts = ["【长期记忆快照】(基于该用户历史，供参考)"]
    labels = {"偏好": "偏好", "事实": "事实", "约束": "硬约束"}
    for t, items in groups.items():
        if items:
            parts.append(f"· {labels.get(t, t)}：")
            parts.extend(f"    - {x}" for x in items)
    return "\n".join(parts)
