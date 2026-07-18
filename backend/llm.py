"""DeepSeek LLM 客户端 (OpenAI 兼容格式)。"""
from openai import OpenAI

from .config import settings

client = OpenAI(
    api_key=settings.DEEPSEEK_API_KEY,
    base_url=settings.DEEPSEEK_BASE_URL,
)

SYSTEM_PROMPT = "You are a helpful assistant."


def chat_completion(history: list[dict]) -> dict:
    """调用 DeepSeek，返回 {"content": str, "reasoning": str|None}。

    history: [{"role": "user"/"assistant"/"system", "content": "..."}]
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    resp = client.chat.completions.create(
        model=settings.DEEPSEEK_MODEL,
        messages=messages,
        stream=False,
    )
    msg = resp.choices[0].message
    content = msg.content or ""
    # DeepSeek 思维链字段 (若模型返回)
    reasoning = getattr(msg, "reasoning_content", None) or getattr(
        msg, "reasoning", None
    )
    return {"content": content, "reasoning": reasoning}


def chat_completion_stream(history: list[dict]):
    """流式调用 DeepSeek，逐块产出 (kind, delta)。

    kind: "reasoning" (思维链) 或 "content" (正文)
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    stream = client.chat.completions.create(
        model=settings.DEEPSEEK_MODEL,
        messages=messages,
        stream=True,
    )
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        reasoning = getattr(delta, "reasoning_content", None) or getattr(
            delta, "reasoning", None
        )
        if reasoning:
            yield "reasoning", reasoning
        if delta.content:
            yield "content", delta.content
