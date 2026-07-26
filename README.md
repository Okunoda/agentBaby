# 志愿领航 · 高报师（记忆增强对话系统）

一个带 Web UI 的高考志愿填报咨询应用。后端用 FastAPI，通过 OpenAI 兼容格式调用 DeepSeek，
以 **SSE 流式**返回；对话与记忆分别存入 **MySQL** 与 **Milvus** 向量库。

系统内置一套 **三层记忆 + 双管道** 的记忆管理架构，让"高报师"能跨会话记住用户偏好、
在长对话中压缩上下文、并按需检索最相关的历史记忆。

## 界面
- **左**：会话列表（新建/切换/删除）
- **中**：一问一答对话，打字机式流式输出（含思维链）
- **右**：🧠 记忆面板（本轮注入 / 会话记忆 / 长期记忆 / 记忆管道），可手动触发 Auto Dream

## 记忆架构

### 三层记忆
| 层 | 作用范围 | 存储 | 说明 |
|----|----------|------|------|
| **长期记忆** Long-term | 跨会话 | Milvus + 元数据 | 偏好/事实/约束，含`产生路径`；入库 0.95 向量去重；快照按 `last_access` Top-K、约 4000 字符注入；超 N 天附时效提醒 |
| **会话记忆** Session | 会话内 | MySQL | 6 区段结构化摘要（学生画像/临时假设/硬约束/任务阶段/推荐快照/待确认）；>3 轮或 >15k token 异步增量更新；上下文超 70% 时压缩最近3轮之前的消息为 `<session_memory>` |
| **工作记忆** Working | 当前请求 | 检索 | 每次提问向量检索 Top-3（相似度>0.7），按 `相关记忆：1.[内容]（产生于:[路径]）` 注入 |

### 双管道
- **Auto Memory（实时提取）**：每轮结束后模型判断是否提取记忆 + 识别情绪（收集 bad case）。
  显式"记住…"直接入库；模型自动提取的进入暂存区待整合。
- **Auto Dream（异步整合）**：后台巡检（用户非活跃 / 暂存超 N 条）触发，去重合并碎片记忆、
  检测冲突。**冲突不自动覆盖**，生成"需澄清"待办等用户确认。

## 结构
```
backend/
  config.py          配置（DeepSeek / MySQL / Milvus / 记忆参数）
  db.py              MySQL 模型（sessions/messages/session_memory/auto_memory/memory_conflict/dream_state）
  llm.py             高报师 System Prompt + DeepSeek 调用（流式/JSON）
  memory/
    embedding.py     fastembed 中文向量（bge-small-zh, 512维）+ token 估算
    milvus_store.py  长期记忆向量库（增/去重/检索/快照）
    long_term.py     长期记忆快照注入（含时效提醒）
    session_memory.py 会话记忆 6 区段（增量更新 + 压缩块）
    working_memory.py 工作记忆检索与注入格式
    auto_memory.py   Auto Memory 实时提取
    auto_dream.py    Auto Dream 异步整合（冲突检测 + 后台巡检）
    orchestrator.py  编排：组装上下文 + 每轮后台触发
  main.py            FastAPI 接口 + 前端托管
frontend/            index.html / style.css / app.js
```

## 前置条件
- Python 3.10+
- **MySQL**（docker: root/root, 3306）→ 库 `chat_app` 自动创建
- **Milvus v2.6.20**（docker compose: etcd + minio + milvus）
  ```bash
  # 见 docker-compose.yml（mysql + milvus 全套），合集名 agentbaby
  docker compose -p agentbaby -f docker-compose.yml create   # 创建（不启动）
  docker compose -p agentbaby -f docker-compose.yml start    # 手动启动
  ```
  集合 `long_term_memory` 首次启动自动创建（1024-dim, IP metric）。

## 运行
依赖用 [uv](https://docs.astral.sh/uv/) 管理（取代 pip + venv）：
```bash
# 首次：装 .venv + 按 uv.lock 装依赖
uv sync

# 启动服务
uv run python -m uvicorn backend.main:app --host 0.0.0.0 --port 8010
# 或 PyCharm 直接运行 backend/main.py（IDE 配 venv 指向 .venv）
```
浏览器打开 **http://localhost:8010**（注意：8000 被 Attu 占用，本应用用 8010）。

> 首次启动会自动下载中文向量模型 `BAAI/bge-small-zh-v1.5`（约 100MB）。

## 记忆管理 API
| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/api/memory/long-term` | 长期记忆列表 |
| GET  | `/api/memory/auto` | Auto Memory 暂存区 |
| GET  | `/api/memory/conflicts` | 待澄清冲突 |
| POST | `/api/memory/conflicts/{id}/resolve` | 标记冲突已澄清 |
| POST | `/api/memory/dream` | 手动触发 Auto Dream |
| GET  | `/api/sessions/{id}/session-memory` | 会话记忆 6 区段 |

## 关键参数（.env）
去重阈值 `DEDUP_SIM=0.95`、工作记忆 `WORKING_MEM_SIM=0.7`/`TOPK=3`、快照 `SNAPSHOT_MAX_CHARS=4000`、
时效 `STALE_DAYS=3`、缓存失效 `CACHE_INVALID_MINUTES=60`、会话记忆触发 `>3 轮 / >15k token`、
压缩 `COMPRESS_RATIO=0.7`、Dream 触发 `DREAM_MIN_PENDING=3 / DREAM_IDLE_MINUTES=2`。
