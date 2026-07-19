"""DeepSeek LLM 客户端 (OpenAI 兼容格式) + 高报师系统提示词。"""
import json

from openai import OpenAI

from .config import settings

client = OpenAI(
    api_key=settings.DEEPSEEK_API_KEY,
    base_url=settings.DEEPSEEK_BASE_URL,
)

# ============ 高报师 System Prompt ============
SYSTEM_PROMPT = """你是「志愿领航」——一位资深的高考志愿填报规划师（高报师）。

【你的身份与目标】
你专注帮助中国高考考生及家长，基于分数、位次、选科、省份、兴趣与职业规划，做出科学的
志愿填报决策。你熟悉平行志愿规则、冲稳保策略、专业级差、招生章程、就业前景与转专业政策。

【工作方式】
1. 先了解学生画像：高考分数、全省位次、选科组合、所在省份、意向地域/城市、预算、
   身体或单科限制、以及职业倾向。信息不全时主动、逐项追问，一次别问太多。
2. 采用「冲—稳—保」梯度策略给出院校+专业组合建议，并说明每档的录取概率与理由。
3. 客观中立：不夸大、不承诺录取；涉及分数线、位次等硬数据时，说明是参考并提示以官方为准。
4. 尊重学生的偏好与硬约束（绝不推荐其明确拒绝的地域/院校/专业）。
5. 语气专业、耐心、有温度，考生常有焦虑情绪，注意安抚与鼓励。

【记忆使用规则】
- 你会在上下文中看到「长期记忆快照」「相关记忆(工作记忆)」「会话状态摘要(session_memory)」，
  它们记录了该用户过往的偏好、事实与约束，请自然地加以利用，让建议更贴合个人情况。
- 若某条记忆带有「⚠️ 已是 x 天前」的时效提醒，你在依据它给建议前，必须先向用户确认该情况是否仍然成立。
- 「临时假设」（如“假如我考到650分”）优先级高于真实分数，但要明确这是假设推演。
- 记忆是背景参考，不要机械复述；当记忆之间或与用户新表述冲突时，主动向用户澄清，不要擅自采信。
"""


def chat_completion(history: list[dict], system: str | None = None) -> dict:
    """非流式调用，返回 {"content": str, "reasoning": str|None}。"""
    messages = [{"role": "system", "content": system or SYSTEM_PROMPT}] + history
    resp = client.chat.completions.create(
        model=settings.DEEPSEEK_MODEL, messages=messages, stream=False
    )
    msg = resp.choices[0].message
    reasoning = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
    return {"content": msg.content or "", "reasoning": reasoning}


def chat_completion_stream(history: list[dict], system: str | None = None):
    """流式调用，逐块产出 (kind, delta)。kind: reasoning / content。"""
    messages = [{"role": "system", "content": system or SYSTEM_PROMPT}] + history
    stream = client.chat.completions.create(
        model=settings.DEEPSEEK_MODEL, messages=messages, stream=True
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


def complete_text(prompt: str, system: str = "You are a helpful assistant.") -> str:
    """一次性文本补全 (供记忆管道内部使用)。"""
    resp = client.chat.completions.create(
        model=settings.DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        stream=False,
    )
    return resp.choices[0].message.content or ""


def complete_json(prompt: str, system: str = "You output only valid JSON.") -> dict | list | None:
    """要求模型返回 JSON，稳健解析。失败返回 None。"""
    raw = complete_text(prompt, system=system)
    return _extract_json(raw)


def _extract_json(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        # 去掉 ```json ... ``` 包裹
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    # 直接尝试
    try:
        return json.loads(text)
    except Exception:
        pass
    # 截取第一个 { 或 [ 到最后一个 } 或 ]
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = text.find(open_c), text.rfind(close_c)
        if i != -1 and j != -1 and j > i:
            try:
                return json.loads(text[i : j + 1])
            except Exception:
                continue
    return None
