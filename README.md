# DeepSeek Chat

一个带 Web UI 的对话应用：左侧会话列表，右侧一问一答对话页面，底部输入框。
后端用 FastAPI，通过 OpenAI 兼容格式调用 DeepSeek，会话与历史存入 MySQL。

## 结构
```
backend/    FastAPI 后端 (config / db / llm / main)
frontend/   Web UI (index.html / style.css / app.js)
requirements.txt
.env        配置 (DeepSeek key / MySQL / 端口)
```

## 前置条件
- Python 3.10+
- MySQL 已运行 (docker: root/root, 端口 3306) —— 数据库 `chat_app` 会自动创建

## 运行
```bash
# 1. 创建虚拟环境并安装依赖
python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash
# source .venv/bin/activate        # Linux/Mac/WSL
pip install -r requirements.txt

# 2. 启动服务
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

# 3. 浏览器打开
http://localhost:8000
```

## 数据表
- `sessions(id, title, created_at, updated_at)`
- `messages(id, session_id, role, content, reasoning, created_at)`

## 配置
所有配置在 `.env` 中：DeepSeek API key、base_url、模型名 `deepseek-v4-flash`、MySQL 连接。
