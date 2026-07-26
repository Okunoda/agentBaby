# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目性质

「志愿领航·高报师」——高考志愿填报的对话系统。后端 FastAPI + DeepSeek（OpenAI 兼容），对话存 MySQL，长期记忆存 Milvus。**核心特色是三层记忆 + 双管道的上下文管理架构**（详见 `backend/memory/`）。

## 常用命令

### 环境（uv 管理依赖）
```bash
# 首次：自动装 .venv + 按 uv.lock 装依赖
uv sync

# 后续运行任何命令都建议加 uv run，会自动激活 venv
uv run python -m uvicorn backend.main:app --host 0.0.0.0 --port 8010

# 加新依赖
uv add <pkg>

# 升某个依赖
uv add <pkg>==<ver>

# 升所有依赖到 lock 允许的最新版
uv lock --upgrade

# 删 venv 重建
uv sync --reinstall
```

### 启动依赖（外部服务）
```bash
# 容器合集：mysql + milvus(etcd/minio/standalone)
# 见 docker-compose.yml 顶部注释了解踩过的坑
docker compose -p agentbaby -f docker-compose.yml create   # 创建（不启动）
docker compose -p agentbaby -f docker-compose.yml start    # 手动启动

# 不用 uv 的话也可以直接用 docker，端口/卷/etc. 同 compose 文件
```

### 启动应用
```bash
# 推荐用 uv run（端口 8010，因为 Attu 占 8000）
uv run python -m uvicorn backend.main:app --host 0.0.0.0 --port 8010
# 或 PyCharm 直接运行 backend/main.py（IDE 自己配 venv 指向 .venv）
```
浏览器打开 http://localhost:8010。

### Embedding 检索 demo
```bash
uv run python backend/demo_embedding_search.py
```
插入 4 句中文偏好（"我喜欢编程/我不喜欢计算机/我不喜欢编程/我不喜欢数学"）到临时 collection `demo_embedding_test`，展示 IP=cosine 检索效果，**运行结束会自动 drop，不污染业务 collection**。首次启动会自动下载百炼 embedding 模型。

## 代码结构（核心）

```
backend/
  config.py           所有 .env 参数，含三层记忆阈值
  db.py               SQLAlchemy：sessions/messages/session_memory/auto_memory/memory_conflict/dream_state
  llm.py              DeepSeek 客户端（流式 + JSON 解析兜底）+ 高报师 SYSTEM_PROMPT
  memory/
    embedding.py      DashScope OpenAI 兼容 embedding + token 估算（CJK≈1, 其它≈1/4）
    milvus_store.py   长期记忆 CRUD；0.95 去重；带线程级超时保护（Milvus search/query 偶发挂起）
    long_term.py      长期记忆快照注入，按 mem_type 分组，超 STALE_DAYS 附时效提醒
    working_memory.py 每轮按问题检索 Top-3 并组装注入块；附带"渐进式澄清"冲突卡片查询
    session_memory.py 会话内 6 区段摘要 + 压缩块生成
    auto_memory.py    每轮 LLM 提取碎片记忆 → explicit 直入 Milvus；model 提取的入 MySQL 暂存
    auto_dream.py     后台线程巡检：pending 条数 ≥ N 或用户空闲 ≥ M 分钟 → 合并去重 + 冲突检测
    orchestrator.py   组装每轮 LLM 上下文（详见下文）
  main.py             FastAPI：SSE 流式 chat + 记忆管理 API + 静态资源托管

frontend/             index.html / style.css / app.js（原生 JS，无构建步骤）
```

## 三层记忆上下文组装（orchestrator.build_messages）

每轮发模型前按下顺序拼装：

1. **长期记忆快照**（system）—— 仅当新会话或距上次活跃 > `CACHE_INVALID_MINUTES`(60min) 注入；按 `last_access_time` 取 Top-K（默认 20），累计 ≤ `SNAPSHOT_MAX_CHARS`(4000)；超 `STALE_DAYS`(3) 附 "⚠️ x 天前" 提醒。
2. **工作记忆**（system）—— 每次都对当前问题做向量检索 Top-`WORKING_MEM_TOPK`(3)，相似度 > `WORKING_MEM_SIM`(0.55)。命中后刷 `last_access_time` 与 `access_count`。命中条若涉及 open 冲突 → 流式 SSE 先发 `clarifications` 卡片，**暂停 LLM 调用**，等用户 `/choose` 后再续。
3. **历史消息** —— 若 token 估算 > `COMPRESS_RATIO`(0.7) × `CONTEXT_LIMIT_TOKENS`(16k)，或旧会话重连（≥ `SESSION_MEM_MIN_TURNS` 轮且缓存失效），用 `<session_memory>` 块替换 `RECENT_TURNS_KEEP`(3) 轮之前的所有消息。
4. **system prompt**（高报师人设 + 记忆使用规则）—— 由 `llm.chat_completion_stream` 在最前面补。

每轮 LLM 流结束后，后台线程 (`schedule_after_turn`) 异步触发：
- `auto_memory.extract_and_store`（情绪写入 `auto_memory.source="emotion"`，显式记忆直入 Milvus，其他 pending）
- 若满足 `SESSION_MEM_MIN_TURNS`(3) 或 `SESSION_MEM_MIN_TOKENS`(15k)，增量更新 6 区段摘要

## 双管道（Auto Memory / Auto Dream）

| | 触发 | 写什么 | 备注 |
|--|--|--|--|
| Auto Memory | 每轮结束 | explicit → Milvus；model 提取 → `auto_memory` status=pending | 同时记录情绪（仅作 bad case） |
| Auto Dream | 后台线程每 `DREAM_INTERVAL_SEC`(30s) 巡检：pending≥`DREAM_MIN_PENDING`(3) 或用户空闲 ≥ `DREAM_IDLE_MINUTES`(2) | 合并碎片入 Milvus（再经 0.95 去重）；冲突写入 `memory_conflict` status=open | 冲突**不自动覆盖**，等用户选 |

冲突解决：`POST /api/memory/conflicts/{id}/choose {choice: "existing"|"new"}` → 后端真删 loser 的 Milvus id，再校验 winner 仍在。

## 几个非显而易见的注意点

- **`backend/main.py` 头部双 import 兜底**（`if __package__ in (None, "")`）：脚本运行与模块运行都支持。改 import 时两边都要改。
- **Milvus search/query 偶发挂起**：`milvus_store._search_with_filter` / `_query_with_filter` 都用 `ThreadPoolExecutor(max_workers=1)` + `concurrent.futures.TimeoutError` 兜底（默认 2s / 5s）。**不要直接调 `client.search`**，要过这两个 wrapper。
- **入库后立刻再查可能漏数据**：`add_memory` 末尾有一段 6s 内轮询 `search` 等索引可见的代码，避免紧接同会话 add 又因 IP 距离异常创建副本。
- **Embedding 模型**：当前用百炼 DashScope（`EMBED_MODEL=qwen3.7-text-embedding`，1024 维）。其返回归一化向量 → IP = cosine ∈ [0,1]。`README.md` 写的 bge-small-zh 已废弃，**别再回退**。
- **token 估算是粗的**：`embedding.estimate_tokens` CJK 计 1、其他计 1/4。仅用于压缩触发判断，不要拿来做精确截断。
- **渐进式澄清卡片**：`main.py` 中 `chat_stream` 在 `build_messages` 之后立即查 `working_memory.open_conflicts_for`，命中就 **不发 LLM** 直接返回 `awaiting_clarification=True`。前端 `chooseConflict` 调成功后会以 `pendingResendContent`（`send()` 入口保存的）自动续接那条问题。

## 关键 API（节选）

| 方法 | 路径 | 用途 |
|--|--|--|
| GET/POST/PATCH/DELETE | `/api/sessions[/{id}]` | 会话 CRUD |
| GET | `/api/sessions/{id}/messages` | 历史消息 |
| POST | `/api/sessions/{id}/chat/stream` | **流式对话（SSE）**——主流程 |
| GET | `/api/memory/long-term` | 长期记忆列表 |
| GET | `/api/memory/auto` | Auto Memory 暂存区 |
| GET | `/api/memory/conflicts` | 待澄清冲突 |
| POST | `/api/memory/conflicts/{id}/choose` | 用户选定一侧，删另一侧 |
| POST | `/api/memory/dream` | 手动触发 Auto Dream（演示用） |
| GET | `/api/sessions/{id}/session-memory` | 会话 6 区段 |

SSE 事件类型：`start` / `reasoning` / `content` / `clarifications` / `done` / `error`。

## .env 调参清单（最常改的）

- `DEDUP_SIM`（0.95）/ `WORKING_MEM_SIM`（0.55）/ `WORKING_MEM_TOPK`（3）
- `SNAPSHOT_TOPK`（20）/ `SNAPSHOT_MAX_CHARS`（4000）/ `STALE_DAYS`（3）/ `CACHE_INVALID_MINUTES`（60）
- `SESSION_MEM_MIN_TURNS`（3）/ `SESSION_MEM_MIN_TOKENS`（15000）/ `RECENT_TURNS_KEEP`（3）/ `COMPRESS_RATIO`（0.7）/ `CONTEXT_LIMIT_TOKENS`（16000）
- `DREAM_MIN_PENDING`（3）/ `DREAM_IDLE_MINUTES`（2）/ `DREAM_INTERVAL_SEC`（30）

`.env` 默认值包含真实 API key，**`.gitignore` 已忽略 `.env`**；改完要重启 uvicorn 才生效。