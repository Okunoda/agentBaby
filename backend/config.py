"""应用配置：从环境变量 / .env 读取。"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # DeepSeek / OpenAI 兼容接口
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    # MySQL
    MYSQL_HOST: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER: str = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "root")
    MYSQL_DB: str = os.getenv("MYSQL_DB", "chat_app")

    # 服务
    APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT: int = int(os.getenv("APP_PORT", "8010"))

    # ---------- Milvus 向量库 ----------
    MILVUS_URI: str = os.getenv("MILVUS_URI", "http://127.0.0.1:19530")
    MILVUS_COLLECTION: str = os.getenv("MILVUS_COLLECTION", "long_term_memory")
    # 百炼 (DashScope) OpenAI 兼容 embedding
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "sk-63f33bef884d4067a0e428702ea4786d")
    DASHSCOPE_BASE_URL: str = os.getenv(
        "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    EMBED_MODEL: str = os.getenv("EMBED_MODEL", "qwen3.7-text-embedding")
    EMBED_DIM: int = int(os.getenv("EMBED_DIM", "1024"))

    # ---------- 记忆管理参数 ----------
    DEFAULT_USER_ID: str = os.getenv("DEFAULT_USER_ID", "student_001")
    # 去重 / 检索相似度阈值
    DEDUP_SIM: float = float(os.getenv("DEDUP_SIM", "0.95"))          # >0.95 视为重复
    WORKING_MEM_SIM: float = float(os.getenv("WORKING_MEM_SIM", "0.55"))  # 工作记忆检索阈值
    WORKING_MEM_TOPK: int = int(os.getenv("WORKING_MEM_TOPK", "3"))   # 工作记忆 Top-3
    # 长期记忆快照
    SNAPSHOT_TOPK: int = int(os.getenv("SNAPSHOT_TOPK", "20"))
    SNAPSHOT_MAX_CHARS: int = int(os.getenv("SNAPSHOT_MAX_CHARS", "4000"))
    STALE_DAYS: int = int(os.getenv("STALE_DAYS", "3"))              # 超过 N 天提醒确认
    CACHE_INVALID_MINUTES: int = int(os.getenv("CACHE_INVALID_MINUTES", "60"))  # 60min 缓存失效
    # 会话记忆
    SESSION_MEM_MIN_TURNS: int = int(os.getenv("SESSION_MEM_MIN_TURNS", "3"))   # >3 轮
    SESSION_MEM_MIN_TOKENS: int = int(os.getenv("SESSION_MEM_MIN_TOKENS", "15000"))  # >15k token
    RECENT_TURNS_KEEP: int = int(os.getenv("RECENT_TURNS_KEEP", "3"))           # 保留最近 3 轮
    CONTEXT_LIMIT_TOKENS: int = int(os.getenv("CONTEXT_LIMIT_TOKENS", "16000")) # 上下文上限(演示值)
    COMPRESS_RATIO: float = float(os.getenv("COMPRESS_RATIO", "0.7"))           # 70% 使用率触发压缩
    # Auto Dream
    DREAM_MIN_PENDING: int = int(os.getenv("DREAM_MIN_PENDING", "3"))  # 待整合记忆超过 N 条
    DREAM_IDLE_MINUTES: int = int(os.getenv("DREAM_IDLE_MINUTES", "2"))  # 用户非活跃 M 分钟
    DREAM_INTERVAL_SEC: int = int(os.getenv("DREAM_INTERVAL_SEC", "30"))  # 后台巡检间隔

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
