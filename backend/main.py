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
    from backend.db import Message, Session, SessionLocal, init_db
    from backend.llm import chat_completion, chat_completion_stream
else:
    from .db import Message, Session, SessionLocal, init_db
    from .llm import chat_completion, chat_completion_stream

app = FastAPI(title="DeepSeek Chat")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.on_event("startup")
def _startup() -> None:
    init_db()


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

            # 组装历史 (含本次用户消息)
            msgs = (
                db.query(Message)
                .filter(Message.session_id == session_id)
                .order_by(Message.id)
                .all()
            )
            history = [{"role": m.role, "content": m.content} for m in msgs]

            # 通知前端：用户消息已入库、会话标题
            yield _sse(
                {
                    "type": "start",
                    "user_message_id": user_msg.id,
                    "session_title": s.title,
                }
            )

            # 流式调用大模型
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            try:
                for kind, delta in chat_completion_stream(history):
                    if kind == "reasoning":
                        reasoning_parts.append(delta)
                    else:
                        content_parts.append(delta)
                    yield _sse({"type": kind, "delta": delta})
            except Exception as e:  # noqa: BLE001
                yield _sse({"type": "error", "message": f"调用大模型失败: {e}"})
                return

            # 落库助手消息
            assistant_msg = Message(
                session_id=session_id,
                role="assistant",
                content="".join(content_parts),
                reasoning="".join(reasoning_parts) or None,
            )
            db.add(assistant_msg)
            db.commit()
            db.refresh(assistant_msg)

            yield _sse(
                {"type": "done", "assistant_message_id": assistant_msg.id}
            )
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
