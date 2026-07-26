"""数据库：SQLAlchemy 模型 + 初始化。

表结构:
- sessions:        会话 (id, user_id, title, last_active_at, created_at, updated_at)
- messages:        消息 (id, session_id, role, content, reasoning, created_at)
- session_memory:  会话记忆 (6 区段结构化摘要, 每会话一条)
- auto_memory:     Auto Memory 待整合暂存区 (碎片记忆 + 情绪)
- memory_conflict: Auto Dream 发现的记忆冲突 / 需澄清待办
- dream_state:     每个用户上次 dream 时间

注: 长期记忆(Long-term Memory)本体存于 Milvus 向量库，此处只存关系型的会话/暂存/冲突数据。
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    create_engine,
    String,
    Text,
    Integer,
    BigInteger,
    DateTime,
    ForeignKey,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)

from .config import settings


class Base(DeclarativeBase):
    pass


def gen_uuid() -> str:
    return uuid.uuid4().hex


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True, default=settings.DEFAULT_USER_ID)
    title: Mapped[str] = mapped_column(String(255), default="新会话")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    last_active_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user / assistant / system
    content: Mapped[str] = mapped_column(Text)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped["Session"] = relationship(back_populates="messages")


class SessionMemory(Base):
    """会话记忆：6 区段结构化状态摘要 (每会话一条)。"""

    __tablename__ = "session_memory"

    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )
    student_profile: Mapped[str] = mapped_column(Text, default="")        # 学生画像
    assumptions: Mapped[str] = mapped_column(Text, default="")            # 临时假设
    hard_constraints: Mapped[str] = mapped_column(Text, default="")       # 硬约束
    task_stage: Mapped[str] = mapped_column(Text, default="初筛")          # 当前任务阶段
    recommendation_snapshot: Mapped[str] = mapped_column(Text, default="")  # 推荐快照
    pending_questions: Mapped[str] = mapped_column(Text, default="")      # 待确认事项
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    SECTIONS = [
        ("student_profile", "学生画像"),
        ("assumptions", "临时假设"),
        ("hard_constraints", "硬约束"),
        ("task_stage", "当前任务阶段"),
        ("recommendation_snapshot", "推荐快照"),
        ("pending_questions", "待确认事项"),
    ]


class AutoMemory(Base):
    """Auto Memory 暂存区：模型每轮提取的碎片记忆与情绪。"""

    __tablename__ = "auto_memory"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    brief: Mapped[str] = mapped_column(Text)                    # 记忆内容简介
    mem_type: Mapped[str] = mapped_column(String(16))           # 偏好 / 事实 / 约束
    related_context: Mapped[str] = mapped_column(Text, default="")  # 关联上下文(产生路径)
    evidence: Mapped[str] = mapped_column(Text, default="")     # 证据链
    source: Mapped[str] = mapped_column(String(16), default="model")  # explicit / model
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    #   pending(待整合) / merged(已整合入长期) / direct(显式已直接入库) / discarded
    emotion: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 情绪(bad case)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MemoryConflict(Base):
    """Auto Dream 发现的冲突 / 需澄清待办 (必须由用户选出"主要记忆"，舍弃另一条)。"""

    __tablename__ = "memory_conflict"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(Text)      # 冲突描述
    # 两侧文本(冗余存档；milvus id 优先以 chosen 字段为准)
    memory_existing: Mapped[str] = mapped_column(Text, default="")
    memory_existing_milvus_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    memory_new: Mapped[str] = mapped_column(Text, default="")
    memory_new_milvus_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open / chosen
    # 用户最终选定的"主要" Milvus id；舍弃另一条
    chosen_memory_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DreamState(Base):
    __tablename__ = "dream_state"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_dream_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


engine = create_engine(settings.db_url, pool_pre_ping=True, pool_recycle=1800)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ensure_database() -> None:
    tmp_engine = create_engine(settings.db_url_no_db)
    with tmp_engine.connect() as conn:
        conn.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{settings.MYSQL_DB}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
        conn.commit()
    tmp_engine.dispose()


def init_db() -> None:
    ensure_database()
    Base.metadata.create_all(bind=engine)
