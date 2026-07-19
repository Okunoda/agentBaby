"""长期记忆(Long-term Memory) 向量存储：Milvus。

每条记忆字段：
  content          记忆内容
  gen_path         产生路径(上下文快照) —— 用于冲突消解
  evidence         证据链
  mem_type         记忆类型: 偏好 / 事实 / 约束
  user_id          用户ID
  timestamp        产生时间戳(秒)
  last_access_time 最近访问时间(秒)
  access_count     访问次数
  weight           重要性权重

去重：入库时向量相似度 > DEDUP_SIM(0.95) 视为重复，不新增，仅更新
last_access_time 与 access_count。
"""
from __future__ import annotations

import time
import uuid
import threading
import concurrent.futures

from pymilvus import MilvusClient, DataType

from ..config import settings
from . import embedding

_client: MilvusClient | None = None
_ready = False
_lock = threading.Lock()

COLL = settings.MILVUS_COLLECTION


def _gen_id() -> int:
    return uuid.uuid4().int % (2 ** 63)


def get_client() -> MilvusClient:
    global _client, _ready
    if _client is None:
        with _lock:
            if _client is None:
                _client = MilvusClient(uri=settings.MILVUS_URI)
    if not _ready:
        _ensure_collection(_client)
    return _client


def _ensure_collection(client: MilvusClient) -> None:
    global _ready
    with _lock:
        if _ready:
            return
        if not client.has_collection(COLL):
            schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("id", DataType.INT64, is_primary=True)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=settings.EMBED_DIM)
            schema.add_field("content", DataType.VARCHAR, max_length=4000)
            schema.add_field("gen_path", DataType.VARCHAR, max_length=4000)
            schema.add_field("evidence", DataType.VARCHAR, max_length=4000)
            schema.add_field("mem_type", DataType.VARCHAR, max_length=16)
            schema.add_field("user_id", DataType.VARCHAR, max_length=64)
            schema.add_field("timestamp", DataType.INT64)
            schema.add_field("last_access_time", DataType.INT64)
            schema.add_field("access_count", DataType.INT64)
            schema.add_field("weight", DataType.FLOAT)

            index_params = client.prepare_index_params()
            # DashScope qwen3.7-text-embedding 返回归一化向量(norm=1)，
            # 用 IP (Inner Product) 即等价于 cosine similarity，
            # 相似度 ∈ [0,1]，同文本=1.0。
            index_params.add_index(
                field_name="vector", index_type="AUTOINDEX", metric_type="IP"
            )
            client.create_collection(COLL, schema=schema, index_params=index_params)
        client.load_collection(COLL)
        _ready = True


OUTPUT_FIELDS = [
    "id", "content", "gen_path", "evidence", "mem_type",
    "user_id", "timestamp", "last_access_time", "access_count", "weight",
]


def _search_with_filter(client, vec, flt: str, timeout_s: float = 2.0) -> list:
    """Milvus 3.0-beta 的 search+filter 偶发挂起：加一层线程级超时保护。"""
    def _do():
        return client.search(
            collection_name=COLL,
            data=[vec],
            limit=1,
            filter=flt,
            output_fields=OUTPUT_FIELDS,
            search_params={"metric_type": "IP"},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_do)
        try:
            return fut.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            return []
        except Exception:
            return []


def _query_with_filter(client, flt: str, limit: int, timeout_s: float = 5.0) -> list:
    """与 _search_with_filter 同思路 —— query+filter 偶发也会挂。"""
    def _do():
        return client.query(
            collection_name=COLL, filter=flt, output_fields=OUTPUT_FIELDS, limit=limit
        )
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_do)
        try:
            return fut.result(timeout=timeout_s) or []
        except concurrent.futures.TimeoutError:
            return []
        except Exception:
            return []


def add_memory(
    user_id: str,
    content: str,
    gen_path: str = "",
    evidence: str = "",
    mem_type: str = "偏好",
    weight: float = 1.0,
) -> dict:
    """入库一条长期记忆(带 0.95 去重)。返回 {action, id}。"""
    client = get_client()
    vec = embedding.embed_one(content)
    now = int(time.time())
    flt = f'user_id == "{user_id}"'

    hits = _search_with_filter(client, vec, flt, timeout_s=3.0)
    if hits and hits[0]:
        top = hits[0][0]
        score = float(top.get("distance", 0.0))
        ent = top.get("entity", top)
        if score >= settings.DEDUP_SIM:
            client.upsert(
                collection_name=COLL,
                data=[{
                    **{k: ent[k] for k in OUTPUT_FIELDS if k in ent},
                    "vector": vec,
                    "last_access_time": now,
                    "access_count": int(ent.get("access_count", 0)) + 1,
                }],
            )
            return {"action": "duplicate", "id": ent.get("id"), "score": round(score, 4)}

    new_id = _gen_id()
    client.insert(
        collection_name=COLL,
        data=[{
            "id": new_id,
            "vector": vec,
            "content": content[:3990],
            "gen_path": gen_path[:3990],
            "evidence": evidence[:3990],
            "mem_type": mem_type[:15],
            "user_id": user_id,
            "timestamp": now,
            "last_access_time": now,
            "access_count": 1,
            "weight": float(weight),
        }],
    )
    # 给 Milvus 几秒钟构建可搜索的索引 —— 否则紧接其后的同会话内 add 又会创建副本，
    # 因为 IP 分数对未索引的向量返回无关结果。
    deadline = time.time() + 6.0
    while time.time() < deadline:
        hits2 = _search_with_filter(client, vec, flt, timeout_s=2.0)
        if hits2 and hits2[0]:
            top = hits2[0][0]
            ent = top.get("entity", top)
            if float(top.get("distance", 0.0)) >= settings.DEDUP_SIM and ent.get("id") == new_id:
                break
        time.sleep(0.3)
    return {"action": "inserted", "id": new_id}


def search_memories(
    user_id: str, query: str, top_k: int, threshold: float
) -> list[dict]:
    """工作记忆检索：Top-k 相似且 score>threshold。命中后刷新 last_access。"""
    client = get_client()
    vec = embedding.embed_one(query)
    hits = _search_with_filter(
        client, vec, f'user_id == "{user_id}"', timeout_s=3.0
    )
    results = []
    if hits and hits[0]:
        for h in hits[0][:top_k]:
            # IP 距离 ∈ [-1,1]，对归一化向量即 cosine
            score = float(h.get("distance", 0.0))
            if score < threshold:
                continue
            ent = h.get("entity", h)
            results.append({**{k: ent.get(k) for k in OUTPUT_FIELDS}, "score": round(score, 4)})
    if results:
        _touch([r["id"] for r in results])
    return results


def snapshot_memories(
    user_id: str, top_k: int, max_chars: int, mem_types: list[str] | None = None
) -> list[dict]:
    """长期记忆快照：按 user_id(+类型) 过滤，取 last_access_time 最近的 top_k，
    累计字符数不超过 max_chars(向上取整到整条)。"""
    client = get_client()
    flt = f'user_id == "{user_id}"'
    if mem_types:
        types = ", ".join(f'"{t}"' for t in mem_types)
        flt += f" and mem_type in [{types}]"
    rows = _query_with_filter(client, flt, max(top_k * 4, 64), timeout_s=5.0)
    rows.sort(key=lambda r: r.get("last_access_time", 0), reverse=True)
    picked, total = [], 0
    for r in rows[:top_k]:
        total += len(r.get("content", ""))
        picked.append(r)
        if total >= max_chars:  # 向上取整：含使其超过阈值的这一条
            break
    return picked


def _touch(ids: list[int]) -> None:
    client = get_client()
    now = int(time.time())
    rows = client.get(collection_name=COLL, ids=ids, output_fields=OUTPUT_FIELDS + ["vector"])
    data = []
    for r in rows:
        r["last_access_time"] = now
        r["access_count"] = int(r.get("access_count", 0)) + 1
        data.append(r)
    if data:
        client.upsert(collection_name=COLL, data=data)


def list_memories(user_id: str, limit: int = 200) -> list[dict]:
    client = get_client()
    rows = _query_with_filter(
        client, f'user_id == "{user_id}"', limit, timeout_s=5.0
    )
    rows.sort(key=lambda r: r.get("last_access_time", 0), reverse=True)
    return rows


def count_memories(user_id: str) -> int:
    return len(list_memories(user_id, limit=1000))
