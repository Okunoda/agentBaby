const $ = (sel) => document.querySelector(sel);
const sessionList = $("#session-list");
const messagesEl = $("#messages");
const chatTitle = $("#chat-title");
const input = $("#input");
const sendBtn = $("#send-btn");

let currentSessionId = null;
let sending = false;
let lastMemoryDebug = null;
let activeMemTab = "working";

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
  refreshMemory();
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
        if (data.memory) {
          lastMemoryDebug = data.memory;
          renderWorking();
        }
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
    // 每轮结束后刷新记忆面板（Auto Memory / Session Memory 为异步，稍后再刷一次）
    refreshMemory();
    setTimeout(refreshMemory, 4000);
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

// ================= 记忆面板 =================
const memPanel = $("#memory-panel");
const esc = escapeHtml;

document.querySelectorAll(".mem-tab").forEach((tab) => {
  tab.onclick = () => {
    activeMemTab = tab.dataset.tab;
    document.querySelectorAll(".mem-tab").forEach((t) => t.classList.toggle("active", t === tab));
    document.querySelectorAll(".mem-view").forEach((v) =>
      v.classList.toggle("active", v.id === "view-" + activeMemTab)
    );
    refreshMemory();
  };
});

$("#toggle-memory").onclick = () => memPanel.classList.toggle("hidden");
$("#refresh-memory").onclick = refreshMemory;
$("#dream-btn").onclick = async () => {
  const btn = $("#dream-btn");
  btn.disabled = true;
  btn.textContent = "整合中…";
  try {
    const r = await fetch("/api/memory/dream", { method: "POST" }).then((r) => r.json());
    btn.textContent = r.ran
      ? `✅ 整合${r.consolidated}条 / 冲突${r.conflicts}`
      : "无待整合记忆";
  } catch (e) {
    btn.textContent = "❌ 失败";
  }
  setTimeout(() => {
    btn.disabled = false;
    btn.textContent = "💤 触发 Auto Dream 整合";
    refreshMemory();
  }, 2500);
};

function refreshMemory() {
  if (memPanel.classList.contains("hidden")) return;
  if (activeMemTab === "working") renderWorking();
  else if (activeMemTab === "session") loadSessionMemory();
  else if (activeMemTab === "longterm") loadLongTerm();
  else if (activeMemTab === "pipeline") loadPipeline();
}

function renderWorking() {
  const el = $("#view-working");
  const d = lastMemoryDebug;
  if (!d) {
    el.innerHTML = `<div class="mem-empty">发送一条消息后，这里显示本轮为模型注入了哪些记忆。</div>`;
    return;
  }
  el.innerHTML = `
    <div class="mem-debug">
      距上次活跃：<b>${d.gap_min}</b> 分钟 ｜ 历史轮次：<b>${d.turns_before}</b><br/>
      注入长期记忆快照：<b>${d.inject_snapshot ? "是" : "否"}</b>
      ｜ 旧会话重连：<b>${d.reconnect ? "是" : "否"}</b><br/>
      上下文估算 token：<b>${d.tokens}</b> ｜ 已压缩历史：<b>${d.compressed ? "是" : "否"}</b><br/>
      工作记忆命中(Top-3, 阈值0.7)：<b>${d.working_memory_hits}</b> 条
    </div>
    <div class="mem-empty" style="text-align:left">
      说明：新会话/缓存失效(>60min) 会注入「长期记忆快照」；每轮按问题检索注入「工作记忆」；
      旧会话重连或上下文超 70% 时，最近3轮之前的消息被压缩为 &lt;session_memory&gt;。
    </div>`;
}

async function loadSessionMemory() {
  const el = $("#view-session");
  if (!currentSessionId) {
    el.innerHTML = `<div class="mem-empty">请先选择一个会话。</div>`;
    return;
  }
  const d = await fetch(`/api/sessions/${currentSessionId}/session-memory`).then((r) => r.json());
  const has = d.sections.some((s) => s.value);
  if (!has) {
    el.innerHTML = `<div class="mem-empty">尚未生成会话记忆。<br/>（>3 轮 或 >15k token 后异步生成）</div>`;
    return;
  }
  el.innerHTML = d.sections
    .map(
      (s) => `<div class="mem-section"><h4>${esc(s.label)}</h4><p>${esc(s.value || "—")}</p></div>`
    )
    .join("");
}

async function loadLongTerm() {
  const el = $("#view-longterm");
  const d = await fetch("/api/memory/long-term").then((r) => r.json());
  if (!d.memories.length) {
    el.innerHTML = `<div class="mem-empty">长期记忆为空。<br/>显式说“记住…”会直接入库；其余经 Auto Dream 整合入库。</div>`;
    return;
  }
  el.innerHTML =
    `<div class="mem-debug">共 <b>${d.count}</b> 条长期记忆（用户 ${esc(d.user_id)}）</div>` +
    d.memories
      .map(
        (m) => `<div class="mem-card">
          <span class="tag ${esc(m.mem_type)}">${esc(m.mem_type)}</span>${esc(m.content)}
          ${m.gen_path ? `<div class="path">产生于：${esc(m.gen_path)}</div>` : ""}
          <div class="meta">权重 ${m.weight} ｜ 访问 ${m.access_count} 次 ｜ ${m.age_days} 天前</div>
        </div>`
      )
      .join("");
}

async function loadPipeline() {
  const el = $("#view-pipeline");
  const [auto, conflicts] = await Promise.all([
    fetch("/api/memory/auto").then((r) => r.json()),
    fetch("/api/memory/conflicts").then((r) => r.json()),
  ]);
  let html = "";
  if (conflicts.length) {
    html += `<div class="mem-section"><h4>⚠️ 待澄清冲突</h4></div>`;
    html += conflicts
      .map(
        (c) => `<div class="mem-card conflict-card">
          ${esc(c.description)}
          <div class="path">已有：${esc(c.memory_existing)}｜新：${esc(c.memory_new)}</div>
          <button onclick="resolveConflict(${c.id})">标记已澄清</button>
        </div>`
      )
      .join("");
  }
  html += `<div class="mem-section"><h4>Auto Memory 暂存区</h4></div>`;
  if (!auto.length) {
    html += `<div class="mem-empty">暂无提取记录。</div>`;
  } else {
    html += auto
      .map(
        (m) => `<div class="mem-card">
          <span class="tag ${esc(m.mem_type)}">${esc(m.mem_type)}</span>${esc(m.brief)}
          <span class="status-pill ${esc(m.status)}">${esc(m.status)}</span>
          ${m.source === "explicit" ? '<span class="status-pill direct">显式</span>' : ""}
          ${m.emotion && m.emotion !== "平静" ? `<span class="status-pill pending">情绪:${esc(m.emotion)}</span>` : ""}
          ${m.related_context ? `<div class="path">语境：${esc(m.related_context)}</div>` : ""}
        </div>`
      )
      .join("");
  }
  el.innerHTML = html;
}

async function resolveConflict(id) {
  await fetch(`/api/memory/conflicts/${id}/resolve`, { method: "POST" });
  loadPipeline();
}
window.resolveConflict = resolveConflict;
