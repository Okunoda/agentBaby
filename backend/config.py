"""应用配置：从环境变量 / .env 读取。

设计原则：
- **不允许默认值**。所有配置必须由环境变量显式提供，缺失则在
  `require_settings()` 中汇总报错并阻止启动，避免在部署时
  因漏配默认值而出现"看似正常、实则错配"的事故。
- 所有环境变量集中在 `backend/config.py` 一个文件，业务代码禁止再
  直接调用 `os.getenv`，统一通过 `settings.X` 访问。
"""
from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()


class MissingConfigError(RuntimeError):
    """启动时发现缺失/为空的环境变量。"""


# ---------- 字段读取（不带默认值） ----------
def _get(name: str) -> str:
    """读取必填字符串；缺失或空串都视为未配置。"""
    val = os.getenv(name)
    if val is None or val.strip() == "":
        raise MissingConfigError(f"环境变量 {name} 未设置")
    return val


def _get_int(name: str) -> int:
    return int(_get(name))


def _get_float(name: str) -> float:
    return float(_get(name))


# ---------- 必填项白名单 ----------
# (env 名, 类型, 必填?, 用途说明)
_REQUIRED = [
    # DeepSeek / OpenAI 兼容接口
    ("DEEPSEEK_API_KEY", str, True, "DeepSeek API Key"),
    ("DEEPSEEK_BASE_URL", str, True, "DeepSeek OpenAI 兼容 base URL"),
    ("DEEPSEEK_MODEL", str, True, "DeepSeek 模型名"),

    # MySQL
    ("MYSQL_HOST", str, True, "MySQL 主机"),
    ("MYSQL_PORT", int, True, "MySQL 端口"),
    ("MYSQL_USER", str, True, "MySQL 用户名"),
    ("MYSQL_PASSWORD", str, True, "MySQL 密码"),
    ("MYSQL_DB", str, True, "MySQL 数据库名"),

    # 服务
    ("APP_HOST", str, True, "应用绑定 host"),
    ("APP_PORT", int, True, "应用端口"),

    # Milvus
    ("MILVUS_URI", str, True, "Milvus 连接 URI"),
    ("MILVUS_COLLECTION", str, True, "Milvus 集合名"),

    # DashScope Embedding
    ("DASHSCOPE_API_KEY", str, True, "百炼 DashScope API Key"),
    ("DASHSCOPE_BASE_URL", str, True, "百炼 DashScope OpenAI 兼容 base URL"),
    ("EMBED_MODEL", str, True, "Embedding 模型名"),
    ("EMBED_DIM", int, True, "Embedding 向量维度"),

    # 业务默认 / 阈值
    ("DEFAULT_USER_ID", str, True, "默认用户 ID"),
    ("DEDUP_SIM", float, True, "长期记忆入库去重相似度阈值"),
    ("WORKING_MEM_SIM", float, True, "工作记忆检索相似度阈值"),
    ("WORKING_MEM_TOPK", int, True, "工作记忆 Top-K"),
    ("SNAPSHOT_TOPK", int, True, "长期记忆快照条数"),
    ("SNAPSHOT_MAX_CHARS", int, True, "长期记忆快照字符上限"),
    ("MILVUS_FIELD_MAX_LEN", int, True, "Milvus VARCHAR 字段最大长度(覆盖 content/gen_path/evidence)"),
    ("STALE_DAYS", int, True, "长期记忆时效提醒阈值(天)"),
    ("CACHE_INVALID_MINUTES", int, True, "长期记忆快照缓存失效时间(分钟)"),
    ("SESSION_MEM_MIN_TURNS", int, True, "会话记忆触发阈值-轮次"),
    ("SESSION_MEM_MIN_TOKENS", int, True, "会话记忆触发阈值-token"),
    ("RECENT_TURNS_KEEP", int, True, "压缩时保留的最近轮次"),
    ("CONTEXT_LIMIT_TOKENS", int, True, "上下文总 token 上限(估算)"),
    ("COMPRESS_RATIO", float, True, "压缩触发比例(上下文使用率)"),
    ("DREAM_MIN_PENDING", int, True, "Auto Dream 触发阈值-暂存条数"),
    ("DREAM_IDLE_MINUTES", int, True, "Auto Dream 触发阈值-用户空闲分钟"),
    ("DREAM_INTERVAL_SEC", int, True, "Auto Dream 后台巡检间隔秒"),

    # Prompt 截断长度(给到 LLM 的输入上限)
    ("AUTO_MEMORY_PROMPT_MAX_CHARS", int, True, "Auto Memory 提取 prompt 截断长度"),
    ("SESSION_MEMORY_PROMPT_MAX_CHARS", int, True, "会话记忆生成 prompt 截断长度"),
]


def require_settings() -> None:
    """校验所有必填环境变量；任意一项缺失/为空都抛 MissingConfigError。"""
    missing: list[str] = []
    for name, typ, required, desc in _REQUIRED:
        val = os.getenv(name)
        if required and (val is None or val.strip() == ""):
            missing.append(f"  - {name}  ({desc})")
        elif val is not None:
            try:
                if typ is int:
                    int(val)
                elif typ is float:
                    float(val)
                elif typ is str:
                    pass
                else:
                    raise TypeError(f"unsupported type {typ}")
            except (ValueError, TypeError) as e:
                missing.append(f"  - {name}  (类型错误：期望 {typ.__name__}, 实际值 {val!r}: {e})")
    if missing:
        body = "\n".join(missing)
        raise MissingConfigError(
            "下列环境变量缺失或非法，请在 .env 中补齐后重启：\n" + body
        )


class Settings:
    """应用配置。所有字段均来自环境变量；实例化前必须先调用 `require_settings()`。"""

    # DeepSeek / OpenAI 兼容接口
    DEEPSEEK_API_KEY: str = _get("DEEPSEEK_API_KEY")
    DEEPSEEK_BASE_URL: str = _get("DEEPSEEK_BASE_URL")
    DEEPSEEK_MODEL: str = _get("DEEPSEEK_MODEL")

    # MySQL
    MYSQL_HOST: str = _get("MYSQL_HOST")
    MYSQL_PORT: int = _get_int("MYSQL_PORT")
    MYSQL_USER: str = _get("MYSQL_USER")
    MYSQL_PASSWORD: str = _get("MYSQL_PASSWORD")
    MYSQL_DB: str = _get("MYSQL_DB")

    # 服务
    APP_HOST: str = _get("APP_HOST")
    APP_PORT: int = _get_int("APP_PORT")

    # Milvus 向量库
    MILVUS_URI: str = _get("MILVUS_URI")
    MILVUS_COLLECTION: str = _get("MILVUS_COLLECTION")
    MILVUS_FIELD_MAX_LEN: int = _get_int("MILVUS_FIELD_MAX_LEN")

    # DashScope Embedding
    DASHSCOPE_API_KEY: str = _get("DASHSCOPE_API_KEY")
    DASHSCOPE_BASE_URL: str = _get("DASHSCOPE_BASE_URL")
    EMBED_MODEL: str = _get("EMBED_MODEL")
    EMBED_DIM: int = _get_int("EMBED_DIM")

    # 业务默认 / 阈值
    DEFAULT_USER_ID: str = _get("DEFAULT_USER_ID")
    DEDUP_SIM: float = _get_float("DEDUP_SIM")
    WORKING_MEM_SIM: float = _get_float("WORKING_MEM_SIM")
    WORKING_MEM_TOPK: int = _get_int("WORKING_MEM_TOPK")
    SNAPSHOT_TOPK: int = _get_int("SNAPSHOT_TOPK")
    SNAPSHOT_MAX_CHARS: int = _get_int("SNAPSHOT_MAX_CHARS")
    STALE_DAYS: int = _get_int("STALE_DAYS")
    CACHE_INVALID_MINUTES: int = _get_int("CACHE_INVALID_MINUTES")

    SESSION_MEM_MIN_TURNS: int = _get_int("SESSION_MEM_MIN_TURNS")
    SESSION_MEM_MIN_TOKENS: int = _get_int("SESSION_MEM_MIN_TOKENS")
    RECENT_TURNS_KEEP: int = _get_int("RECENT_TURNS_KEEP")
    CONTEXT_LIMIT_TOKENS: int = _get_int("CONTEXT_LIMIT_TOKENS")
    COMPRESS_RATIO: float = _get_float("COMPRESS_RATIO")

    DREAM_MIN_PENDING: int = _get_int("DREAM_MIN_PENDING")
    DREAM_IDLE_MINUTES: int = _get_int("DREAM_IDLE_MINUTES")
    DREAM_INTERVAL_SEC: int = _get_int("DREAM_INTERVAL_SEC")


    # Prompt 截断长度
    AUTO_MEMORY_PROMPT_MAX_CHARS: int = _get_int("AUTO_MEMORY_PROMPT_MAX_CHARS")
    SESSION_MEMORY_PROMPT_MAX_CHARS: int = _get_int("SESSION_MEMORY_PROMPT_MAX_CHARS")

    @property
    def db_url_no_db(self) -> str:
        """不带数据库名的连接串，用于首次创建 database。"""
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/?charset=utf8mb4"
        )

    @property
    def db_url(self) -> str:
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DB}?charset=utf8mb4"
        )


settings = Settings()