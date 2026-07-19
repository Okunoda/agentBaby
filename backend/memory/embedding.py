"""Embedding 服务：调用百炼(DashScope) OpenAI 兼容接口的文本嵌入模型，进程内单例。

默认: qwen3.7-text-embedding，维度 1024。
"""
from __future__ import annotations

import threading

from openai import OpenAI

from ..config import settings

_client: OpenAI | None = None
_lock = threading.Lock()


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = OpenAI(
                    api_key=settings.DASHSCOPE_API_KEY,
                    base_url=settings.DASHSCOPE_BASE_URL,
                )
    return _client


def _embed_via_api(texts: list[str]) -> list[list[float]]:
    client = _get_client()
    # 百炼 OpenAI 兼容 /embeddings 端点 (text-embedding-v4 / qwen3-embedding 等)
    # 按官方文档：input 传字符串列表，model 用 "text-embedding-v4"；若你的接入名是
    # 自定义别名，可用 settings.EMBED_MODEL。
    resp = client.embeddings.create(model=settings.EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return _embed_via_api(texts)


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：CJK 字符按 1 计，其余按 1/4 计。"""
    if not text:
        return 0
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    other = len(text) - cjk
    return int(cjk + other / 4)


def messages_tokens(messages: list[dict]) -> int:
    return sum(estimate_tokens(m.get("content", "")) for m in messages)
