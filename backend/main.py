"""FastAPI 后端：会话管理 + 对话接口，历史存 MySQL。"""
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import json

# 兼容两种运行方式：
#   1) 作为模块运行:  python -m uvicorn backend.main:app   (相对导入)
#   2) 直接运行脚本:  python backend/main.py / PyCharm 运行  (绝对导入)
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from backend.db import (
        Message, Session, SessionMemory, AutoMemory, MemoryConflict, SessionLocal, init_db,
    )
    from backend.llm import chat_completion, chat_completion_stream
    from backend.config import settings
    from backend.memory import orchestrator, milvus_store, session_memory, auto_dream
else:
    from .db import (
        Message, Session, SessionMemory, AutoMemory, MemoryConflict, SessionLocal, init_db,
    )
    from .llm import chat_completion, chat_completion_stream
    from .config import settings
    from .memory import orchestrator, milvus_store, session_memory, auto_dream

app = FastAPI(title="高考志愿·高报师 (记忆增强)")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.on_event("startup")
def _startup() -> None:
    init_db()
    auto_dream.start_worker()  # 启动 Auto Dream 后台巡检


# ---------- Schemas ----------
class SessionOut(BaseModel):
    id: str
    title: str

    class Config:
        from_attributes = True


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    reasoning: str | None = None

    class Config:
        from_attributes = True


class CreateSessionIn(BaseModel):
    title: str | None = None


class RenameSessionIn(BaseModel):
    title: str


class ChatIn(BaseModel):
    content: str


class ChatOut(BaseModel):
    user_message: MessageOut
    assistant_message: MessageOut
    session_title: str


# ---------- Session CRUD ----------
@app.get("/api/sessions", response_model=list[SessionOut])
def list_sessions():
    with SessionLocal() as db:
        rows = db.query(Session).order_by(Session.updated_at.desc()).all()
        return [SessionOut.model_validate(r) for r in rows]


@app.post("/api/sessions", response_model=SessionOut)
def create_session(body: CreateSessionIn):
    with SessionLocal() as db:
        s = Session(title=body.title or "新会话")
        db.add(s)
        db.commit()
        db.refresh(s)
        return SessionOut.model_validate(s)


@app.get("/api/sessions/{session_id}/messages", response_model=list[MessageOut])
def get_messages(session_id: str):
    with SessionLocal() as db:
        s = db.get(Session, session_id)
        if not s:
            raise HTTPException(404, "会话不存在")
        return [MessageOut.model_validate(m) for m in s.messages]


@app.patch("/api/sessions/{session_id}", response_model=SessionOut)
def rename_session(session_id: str, body: RenameSessionIn):
    with SessionLocal() as db:
        s = db.get(Session, session_id)
        if not s:
            raise HTTPException(404, "会话不存在")
        s.title = body.title
        db.commit()
        db.refresh(s)
        return SessionOut.model_validate(s)


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    with SessionLocal() as db:
        s = db.get(Session, session_id)
        if not s:
            raise HTTPException(404, "会话不存在")
        db.delete(s)
        db.commit()
        return {"ok": True}


# ---------- Chat ----------
@app.post("/api/sessions/{session_id}/chat", response_model=ChatOut)
def chat(session_id: str, body: ChatIn):
    content = body.content.strip()
    if not content:
        raise HTTPException(400, "内容不能为空")

    with SessionLocal() as db:
        s = db.get(Session, session_id)
        if not s:
            raise HTTPException(404, "会话不存在")

        # 保存用户消息
        user_msg = Message(session_id=session_id, role="user", content=content)
        db.add(user_msg)
        db.flush()

        # 组装历史 (含本次用户消息)
        history = [
            {"role": m.role, "content": m.content} for m in s.messages
        ]

        # 首条消息用作会话标题
        if s.title in ("新会话", "", None):
            s.title = content[:30]

        # 调用大模型
        try:
            result = chat_completion(history)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            raise HTTPException(502, f"调用大模型失败: {e}")

        assistant_msg = Message(
            session_id=session_id,
            role="assistant",
            content=result["content"],
            reasoning=result.get("reasoning"),
        )
        db.add(assistant_msg)
        db.commit()
        db.refresh(user_msg)
        db.refresh(assistant_msg)
        db.refresh(s)

        return ChatOut(
            user_message=MessageOut.model_validate(user_msg),
            assistant_message=MessageOut.model_validate(assistant_msg),
            session_title=s.title,
        )


# ---------- Chat (SSE 流式) ----------
def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@app.post("/api/sessions/{session_id}/chat/stream")
def chat_stream(session_id: str, body: ChatIn):
    content = body.content.strip()
    if not content:
        raise HTTPException(400, "内容不能为空")

    def event_gen():
        db = SessionLocal()
        try:
            s = db.get(Session, session_id)
            if not s:
                yield _sse({"type": "error", "message": "会话不存在"})
                return

            # 保存用户消息
            user_msg = Message(session_id=session_id, role="user", content=content)
            db.add(user_msg)
            if s.title in ("新会话", "", None):
                s.title = content[:30]
            db.commit()
            db.refresh(user_msg)
            db.refresh(s)

            # === 记忆编排：组装带三层记忆的上下文 (last_active 仍为上一轮) ===
            messages, debug = orchestrator.build_messages(db, s)
            orchestrator.touch_session(db, s)  # 组装后再更新活跃时间

            # 通知前端：用户消息已入库、会话标题、本轮记忆调试信息
            yield _sse({
                "type": "start",
                "user_message_id": user_msg.id,
                "session_title": s.title,
                "memory": debug,
            })

            # 流式调用大模型
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            try:
                for kind, delta in chat_completion_stream(messages):
                    if kind == "reasoning":
                        reasoning_parts.append(delta)
                    else:
                        content_parts.append(delta)
                    yield _sse({"type": kind, "delta": delta})
            except Exception as e:  # noqa: BLE001
                yield _sse({"type": "error", "message": f"调用大模型失败: {e}"})
                return

            # 落库助手消息
            assistant_text = "".join(content_parts)
            assistant_msg = Message(
                session_id=session_id,
                role="assistant",
                content=assistant_text,
                reasoning="".join(reasoning_parts) or None,
            )
            db.add(assistant_msg)
            db.commit()
            db.refresh(assistant_msg)

            # === 每轮结束：异步触发 Auto Memory 提取 + Session Memory 更新 ===
            orchestrator.schedule_after_turn(
                session_id, s.user_id, content, assistant_text
            )

            yield _sse({"type": "done", "assistant_message_id": assistant_msg.id})
        finally:
            db.close()

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 关闭 nginx 缓冲
        },
    )


# ---------- 记忆管理 API ----------
@app.get("/api/memory/long-term")
def api_long_term(user_id: str = settings.DEFAULT_USER_ID):
    rows = milvus_store.list_memories(user_id)
    import time
    now = int(time.time())
    out = []
    for r in rows:
        last = r.get("last_access_time", now)
        out.append({
            "id": str(r.get("id")),
            "content": r.get("content"),
            "gen_path": r.get("gen_path"),
            "evidence": r.get("evidence"),
            "mem_type": r.get("mem_type"),
            "weight": r.get("weight"),
            "access_count": r.get("access_count"),
            "age_days": round((now - last) / 86400, 1),
        })
    return {"user_id": user_id, "count": len(out), "memories": out}


@app.get("/api/memory/auto")
def api_auto_memory(user_id: str = settings.DEFAULT_USER_ID):
    with SessionLocal() as db:
        rows = (
            db.query(AutoMemory)
            .filter(AutoMemory.user_id == user_id)
            .order_by(AutoMemory.id.desc())
            .limit(100)
            .all()
        )
        return [{
            "id": r.id, "brief": r.brief, "mem_type": r.mem_type,
            "related_context": r.related_context, "evidence": r.evidence,
            "source": r.source, "status": r.status, "emotion": r.emotion,
        } for r in rows]


@app.get("/api/memory/conflicts")
def api_conflicts(user_id: str = settings.DEFAULT_USER_ID):
    with SessionLocal() as db:
        rows = (
            db.query(MemoryConflict)
            .filter(MemoryConflict.user_id == user_id, MemoryConflict.status == "open")
            .order_by(MemoryConflict.id.desc())
            .all()
        )
        return [{
            "id": r.id, "description": r.description,
            "memory_existing": r.memory_existing, "memory_new": r.memory_new,
        } for r in rows]


@app.post("/api/memory/conflicts/{cid}/resolve")
def api_resolve_conflict(cid: int):
    with SessionLocal() as db:
        c = db.get(MemoryConflict, cid)
        if not c:
            raise HTTPException(404, "冲突不存在")
        c.status = "resolved"
        db.commit()
        return {"ok": True}


@app.post("/api/memory/dream")
def api_dream(user_id: str = settings.DEFAULT_USER_ID):
    """手动触发一次 Auto Dream 整合(便于演示)。"""
    return auto_dream.run_dream(user_id, force=True)


@app.get("/api/sessions/{session_id}/session-memory")
def api_session_memory(session_id: str):
    with SessionLocal() as db:
        sm = db.get(SessionMemory, session_id)
        d = session_memory.to_dict(sm)
        return {
            "session_id": session_id,
            "sections": [
                {"key": k, "label": label, "value": d.get(k, "")}
                for k, label in SessionMemory.SECTIONS
            ],
            "updated_at": sm.updated_at.isoformat() if sm else None,
        }


# ---------- 前端静态资源 ----------
@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ---------- 直接运行入口 (PyCharm / python backend/main.py) ----------
if __name__ == "__main__":
    import uvicorn

    try:
        from backend.config import settings
    except ImportError:
        from .config import settings

    uvicorn.run(app, host=settings.APP_HOST, port=settings.APP_PORT)
