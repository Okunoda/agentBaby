const $ = (sel) => document.querySelector(sel);
const sessionList = $("#session-list");
const messagesEl = $("#messages");
const chatTitle = $("#chat-title");
const input = $("#input");
const sendBtn = $("#send-btn");

let currentSessionId = null;
let sending = false;

// ---------- API ----------
const api = {
  listSessions: () => fetch("/api/sessions").then((r) => r.json()),
  createSession: () =>
    fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }).then((r) => r.json()),
  deleteSession: (id) => fetch(`/api/sessions/${id}`, { method: "DELETE" }),
  getMessages: (id) => fetch(`/api/sessions/${id}/messages`).then((r) => r.json()),
  chatStream: (id, content) =>
    fetch(`/api/sessions/${id}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }),
};

// ---------- 渲染 ----------
async function loadSessions() {
  const sessions = await api.listSessions();
  sessionList.innerHTML = "";
  sessions.forEach((s) => {
    const li = document.createElement("li");
    li.className = "session-item" + (s.id === currentSessionId ? " active" : "");
    li.dataset.id = s.id;
    li.innerHTML = `<span class="title">${escapeHtml(s.title)}</span>
                    <button class="del" title="删除">🗑</button>`;
    li.querySelector(".title").onclick = () => selectSession(s.id);
    li.querySelector(".del").onclick = (e) => {
      e.stopPropagation();
      deleteSession(s.id);
    };
    sessionList.appendChild(li);
  });
}

function renderMessages(msgs) {
  messagesEl.innerHTML = "";
  if (!msgs.length) {
    messagesEl.innerHTML = `<div class="empty-hint">开始对话吧 👇</div>`;
    return;
  }
  msgs.forEach(addMessageBubble);
  scrollToBottom();
}

function addMessageBubble(m) {
  const hint = messagesEl.querySelector(".empty-hint");
  if (hint) hint.remove();

  const wrap = document.createElement("div");
  wrap.className = `msg ${m.role}`;
  const avatar = m.role === "user" ? "🧑" : "🤖";
  const reasoningHtml = m.reasoning
    ? `<div class="reasoning">💭 ${escapeHtml(m.reasoning)}</div>`
    : "";
  wrap.innerHTML = `
    <div class="avatar">${avatar}</div>
    <div>${reasoningHtml}<div class="bubble">${escapeHtml(m.content)}</div></div>`;
  messagesEl.appendChild(wrap);
  scrollToBottom();
  return wrap;
}

// ---------- 交互 ----------
async function selectSession(id) {
  currentSessionId = id;
  chatTitle.textContent = document.querySelector(`.session-item[data-id="${id}"] .title`)?.textContent || "会话";
  await loadSessions();
  const msgs = await api.getMessages(id);
  renderMessages(msgs);
}

async function newSession() {
  const s = await api.createSession();
  await loadSessions();
  await selectSession(s.id);
}

async function deleteSession(id) {
  if (!confirm("确定删除该会话？")) return;
  await api.deleteSession(id);
  if (id === currentSessionId) {
    currentSessionId = null;
    chatTitle.textContent = "选择或新建一个会话";
    messagesEl.innerHTML = "";
  }
  await loadSessions();
}

async function send() {
  const content = input.value.trim();
  if (!content || sending) return;

  // 没有会话则自动新建
  if (!currentSessionId) {
    const s = await api.createSession();
    currentSessionId = s.id;
    await loadSessions();
    await selectSession(s.id);
  }

  sending = true;
  sendBtn.disabled = true;
  input.value = "";
  autoResize();

  addMessageBubble({ role: "user", content });

  // 助手气泡（流式填充）
  const wrap = addMessageBubble({ role: "assistant", content: "" });
  const column = wrap.children[1];          // 内容列 (含 reasoning + bubble)
  const bubbleEl = wrap.querySelector(".bubble");
  bubbleEl.classList.add("typing");
  bubbleEl.textContent = "思考中…";

  let reasoningEl = null;
  let contentText = "";
  let reasoningText = "";
  let started = false;

  const ensureReasoningEl = () => {
    if (!reasoningEl) {
      reasoningEl = document.createElement("div");
      reasoningEl.className = "reasoning";
      column.insertBefore(reasoningEl, bubbleEl);
    }
    return reasoningEl;
  };

  const handle = (data) => {
    switch (data.type) {
      case "start":
        chatTitle.textContent = data.session_title;
        break;
      case "reasoning":
        reasoningText += data.delta;
        ensureReasoningEl().textContent = "💭 " + reasoningText;
        scrollToBottom();
        break;
      case "content":
        if (!started) {
          started = true;
          bubbleEl.classList.remove("typing");
          bubbleEl.textContent = "";
        }
        contentText += data.delta;
        bubbleEl.textContent = contentText;
        scrollToBottom();
        break;
      case "done":
        break;
      case "error":
        bubbleEl.classList.remove("typing");
        bubbleEl.textContent = "❌ " + data.message;
        break;
    }
  };

  try {
    const resp = await api.chatStream(currentSessionId, content);
    if (!resp.ok || !resp.body) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const parts = buffer.split("\n\n");
      buffer = parts.pop();            // 最后一段可能不完整，留到下次
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        try {
          handle(JSON.parse(line.slice(5).trim()));
        } catch (_) {}
      }
    }
    await loadSessions();
  } catch (e) {
    bubbleEl.classList.remove("typing");
    bubbleEl.textContent = "❌ " + e.message;
  } finally {
    sending = false;
    sendBtn.disabled = false;
    input.focus();
  }
}

// ---------- 工具 ----------
function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}
function escapeHtml(str) {
  return (str ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function autoResize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
}

// ---------- 事件 ----------
$("#new-session-btn").onclick = newSession;
sendBtn.onclick = send;
input.addEventListener("input", autoResize);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});

// 初始化
loadSessions();
