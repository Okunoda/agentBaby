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

// "渐进式澄清" 用户提出的、上次因被打断而未真正交给模型的问题
// (在 send() 入口保存；chooseConflict 调成功后会自动以这条内容续接)
let pendingResendContent = null;

// ---------- API ---------- (always attach a final .catch so promise rejections don't bubble)
const safeJson = (r) => {
  if (!r.ok) throw new Error(`HTTP ${r.status} ${r.statusText}`);
  return r.json();
};

const api = {
  listSessions: () => fetch("/api/sessions").then(safeJson).catch((e) => {
    console.warn("listSessions failed:", e); return [];
  }),
  createSession: () =>
    fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }).then(safeJson).catch((e) => { console.warn("createSession failed:", e); return null; }),
  deleteSession: (id) =>
    fetch(`/api/sessions/${id}`, { method: "DELETE" }).catch((e) => console.warn("deleteSession:", e)),
  getMessages: (id) =>
    fetch(`/api/sessions/${id}/messages`).then(safeJson).catch((e) => {
      console.warn("getMessages failed:", e); return [];
    }),
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

async function send(explicitContent) {
  // 如果提供了 explicitContent（续接路径），用它；否则读输入框
  const content = (explicitContent || input.value || "").trim();
  if (!content || sending) return;

  // 暂存以便后续 chooseConflict 自动续接；正常用户手输也覆盖进去
  pendingResendContent = content;

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

  // 助手气泡(流式填充)；后续可能被卡片替换/移除
  const wrap = addMessageBubble({ role: "assistant", content: "" });
  const column = wrap.children[1];
  const bubbleEl = wrap.querySelector(".bubble");
  bubbleEl.classList.add("typing");
  bubbleEl.textContent = "思考中…";

  // 集中管理占位气泡:任何分支结束时都调用一次
  let placeholderDone = false;
  let awaitingClarification = false;
  const finishPlaceholder = (mode /* "content" | "card" | "error" | "empty" */) => {
    if (placeholderDone) return;
    placeholderDone = true;
    bubbleEl.classList.remove("typing");
    try {
      if (mode === "card") {
        // 用一段简短系统消息替代占位,再让本轮永久安静
        bubbleEl.textContent = "⏸️ 已暂停 —— 请见上方澄清卡片.";
        bubbleEl.style.fontStyle = "italic";
        bubbleEl.style.color = "var(--muted)";
      } else if (mode === "error") {
        // error 分支单独设置文案
      } else if (mode === "empty") {
        // 内容为空(澄清/提前结束)就直接删除占位
        wrap.remove();
      } else if (mode === "content" && !bubbleEl.textContent.trim()) {
        wrap.remove();
      }
    } catch (_) {}
  };

  let reasoningEl = null;
  let contentText = "";
  let reasoningText = "";
  let started = false;

  const ensureReasoningEl = () => {
    if (!reasoningEl) {
      reasoningEl = document.createElement("div");
      reasoningEl.className = "reasoning";
      try { column.insertBefore(reasoningEl, bubbleEl); } catch (_) {}
    }
    return reasoningEl;
  };

  const handle = (data) => {
    if (!data || typeof data.type !== "string") return;
    try {
      switch (data.type) {
        case "start":
          chatTitle.textContent = data.session_title;
          if (data.memory) {
            lastMemoryDebug = data.memory;
            renderWorking();
          }
          break;
        case "clarifications":
          {
            const items = Array.isArray(data.items) ? data.items : [];
            const card = document.createElement("div");
            card.className = "msg assistant";
            card.innerHTML = `<div class="avatar">⚠️</div>
              <div>${items.length ? renderClarificationCards(items) : "<i>暂无澄清项</i>"}</div>`;
            messagesEl.appendChild(card);
            scrollToBottom();
            awaitingClarification = true;
            finishPlaceholder("card");
          }
          break;
        case "reasoning":
          if (placeholderDone && started) return;  // 已落幕,不再触碰 DOM
          reasoningText += data.delta;
          ensureReasoningEl().textContent = "💭 " + reasoningText;
          scrollToBottom();
          break;
        case "content":
          if (!started) {
            started = true;
            try { bubbleEl.classList.remove("typing"); } catch (_) {}
            bubbleEl.textContent = "";
          }
          contentText += data.delta;
          bubbleEl.textContent = contentText;
          scrollToBottom();
          break;
        case "done":
          if (data.awaiting_clarification) {
            refreshMemory();
            // finishPlaceholder("card") 已在 clarifications 分支做
          } else {
            finishPlaceholder("content");
          }
          break;
        case "error":
          try { bubbleEl.classList.remove("typing"); } catch (_) {}
          bubbleEl.textContent = "❌ " + (data.message || "未知错误");
          finishPlaceholder("error");
          break;
      }
    } catch (e) {
      console.error("SSE handler error:", e, data);
      try { finishPlaceholder("error"); } catch (_) {}
    }
  };

  try {
    const resp = await api.chatStream(currentSessionId, content);
    if (!resp.ok || !resp.body) {
      let err = {};
      try { err = await resp.json(); } catch (_) {}
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const parts = buffer.split("\n\n");
        buffer = parts.pop();
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data:")) continue;
          try {
            handle(JSON.parse(line.slice(5).trim()));
          } catch (e) {
            console.warn("SSE parse error:", e, line);
          }
        }
      }
    } catch (e) {
      console.warn("SSE stream error:", e);
      throw e;  // 落到下面外层 catch → 占位被 finishPlaceholder 清理
    }
    try { await loadSessions(); } catch (_) {}
  } catch (e) {
    console.warn("send() failed:", e);
    try { bubbleEl.classList.remove("typing"); } catch (_) {}
    bubbleEl.textContent = "❌ " + (e.message || String(e));
  } finally {
    // 万一 SSE 没有收到 done/error 就断流,确保占位气泡不会永久停留在"思考中…"
    if (!placeholderDone) finishPlaceholder(awaitingClarification ? "card" : "empty");
    sending = false;
    sendBtn.disabled = false;
    input.focus();
    // 每轮结束后刷新记忆面板(Auto Memory / Session Memory 为异步,稍后再刷一次)
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

// 在最顶层加一个通用兜底:任何未被捕获的 promise 拒绝都打日志
// (React DevTools 扩展的 "shortcuts" 报错就是它触发 unhandledrejection 后引起的)
window.addEventListener("unhandledrejection", (e) => {
  console.warn("[unhandled rejection]", e.reason);
  e.preventDefault();
});
window.addEventListener("error", (e) => {
  // React DevTools content script 在非 React 页面里读取 undefined.shortcuts —— 吞掉这个特定错误
  if (e && e.message && e.message.includes("shortcuts")) {
    e.preventDefault();
    return false;
  }
}, true);

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
    const r = await fetch("/api/memory/dream", { method: "POST" })
      .then((resp) => {
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json();
      });
    btn.textContent = r.ran
      ? `✅ 整合${r.consolidated}条 / 冲突${r.conflicts}`
      : "无待整合记忆";
  } catch (e) {
    console.warn("dream failed:", e);
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
    el.innerHTML = `<div class="mem-empty">发送一条消息后,这里显示本轮为模型注入了哪些记忆.</div>`;
    return;
  }
  el.innerHTML = `
    <div class="mem-debug">
      距上次活跃:<b>${d.gap_min}</b> 分钟 ｜ 历史轮次:<b>${d.turns_before}</b><br/>
      注入长期记忆快照:<b>${d.inject_snapshot ? "是" : "否"}</b>
      ｜ 旧会话重连:<b>${d.reconnect ? "是" : "否"}</b><br/>
      上下文估算 token:<b>${d.tokens}</b> ｜ 已压缩历史:<b>${d.compressed ? "是" : "否"}</b><br/>
      工作记忆命中(Top-3, 阈值0.7):<b>${d.working_memory_hits}</b> 条
    </div>
    <div class="mem-empty" style="text-align:left">
      说明:新会话/缓存失效(>60min) 会注入「长期记忆快照」；每轮按问题检索注入「工作记忆」；
      旧会话重连或上下文超 70% 时,最近3轮之前的消息被压缩为 &lt;session_memory&gt;.
    </div>`;
}

async function safeFetchJson(url) {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return await r.json();
  } catch (e) {
    console.warn("fetch failed:", url, e);
    return null;
  }
}

async function loadSessionMemory() {
  const el = $("#view-session");
  if (!currentSessionId) {
    el.innerHTML = `<div class="mem-empty">请先选择一个会话.</div>`;
    return;
  }
  const d = await safeFetchJson(`/api/sessions/${currentSessionId}/session-memory`);
  if (!d) { el.innerHTML = `<div class="mem-empty">加载失败.</div>`; return; }
  const has = (d.sections || []).some((s) => s.value);
  if (!has) {
    el.innerHTML = `<div class="mem-empty">尚未生成会话记忆.<br/>(>3 轮 或 >15k token 后异步生成)</div>`;
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
  const d = await safeFetchJson("/api/memory/long-term");
  if (!d || !d.memories || !d.memories.length) {
    el.innerHTML = `<div class="mem-empty">长期记忆为空.<br/>显式说"记住…"会直接入库；其余经 Auto Dream 整合入库.</div>`;
    return;
  }
  el.innerHTML =
    `<div class="mem-debug">共 <b>${d.count}</b> 条长期记忆(用户 ${esc(d.user_id)})</div>` +
    d.memories
      .map(
        (m) => `<div class="mem-card">
          <span class="tag ${esc(m.mem_type)}">${esc(m.mem_type)}</span>${esc(m.content)}
          ${m.gen_path ? `<div class="path">产生于:${esc(m.gen_path)}</div>` : ""}
          <div class="meta">权重 ${m.weight} ｜ 访问 ${m.access_count} 次 ｜ ${m.age_days} 天前</div>
        </div>`
      )
      .join("");
}

async function loadPipeline() {
  const el = $("#view-pipeline");
  const auto = (await safeFetchJson("/api/memory/auto")) || [];
  const conflicts = (await safeFetchJson("/api/memory/conflicts")) || [];
  let html = "";
  if (conflicts.length) {
    html += `<div class="mem-section"><h4>⚠️ 待澄清冲突</h4></div>`;
    html += conflicts
      .map(
        (c) => `<div class="mem-card conflict-card">
          ${esc(c.description)}
          <div class="path">已有:${esc(c.memory_existing)}｜新:${esc(c.memory_new)}</div>
          <button onclick="resolveConflict(${c.id})">标记已澄清</button>
        </div>`
      )
      .join("");
  }
  html += `<div class="mem-section"><h4>Auto Memory 暂存区</h4></div>`;
  if (!auto.length) {
    html += `<div class="mem-empty">暂无提取记录.</div>`;
  } else {
    html += auto
      .map(
        (m) => `<div class="mem-card">
          <span class="tag ${esc(m.mem_type)}">${esc(m.mem_type)}</span>${esc(m.brief)}
          <span class="status-pill ${esc(m.status)}">${esc(m.status)}</span>
          ${m.source === "explicit" ? '<span class="status-pill direct">显式</span>' : ""}
          ${m.emotion && m.emotion !== "平静" ? `<span class="status-pill pending">情绪:${esc(m.emotion)}</span>` : ""}
          ${m.related_context ? `<div class="path">语境:${esc(m.related_context)}</div>` : ""}
        </div>`
      )
      .join("");
  }
  el.innerHTML = html;
}

async function resolveConflict(id) { /* 兼容旧调用 — 已被下面的 chooseConflict 取代 */ }
window.resolveConflict = resolveConflict;

async function chooseConflict(cid, choice) {
  try {
    // 1) 找出原卡片 DOM，让它视觉上"已选定"
    const card = document.querySelector(`.conflict-card[data-cid="${cid}"]`);
    if (card) {
      // 禁用所有按钮 + 显示已选
      card.querySelectorAll("button").forEach((b) => {
        b.disabled = true;
        b.style.opacity = "0.4";
        b.style.cursor = "not-allowed";
      });
      const badge = document.createElement("div");
      badge.className = "mem-meta";
      badge.style.marginTop = "8px";
      badge.style.color = choice === "existing" ? "#2563eb" : "#1e874b";
      badge.innerHTML = `✅ 已采纳「${choice === "existing" ? "已有" : "新"}」,正在以保留的记忆继续回答…`;
      card.appendChild(badge);
    }

    // 2) 调后端 /choose (真正删除 loser)
    const r = await fetch(`/api/memory/conflicts/${cid}/choose`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ choice }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    console.log("choose result:", data);

    // 2.5) 把验证结果显示在卡片 (让用户确知 loser 已删)
    if (card && data.post_state) {
      const verdict = document.createElement("div");
      verdict.className = "mem-meta";
      verdict.style.marginTop = "6px";
      verdict.style.fontSize = "12px";
      const ok = data.post_state.loser_in_milvus === false;
      verdict.style.color = ok ? "#1e874b" : "#c0392b";
      verdict.textContent = ok
        ? (data.delete_result && data.delete_result.verified
            ? "✓ 验证：被舍弃的记忆已从 Milvus 永久删除，保留方仍在。"
            : "✓ 被舍弃方本不在 Milvus 中。")
        : "⚠️ 校验：被舍弃方仍在 Milvus，请查看服务端日志。";
      card.appendChild(verdict);
    }

    // 3) 同步刷新记忆面板(状态已 chosen,卡片会消失)
    refreshMemory();

    // 4) 自动以"被打断的那条用户问题"继续走流程
    //    pendingResendContent 在首次 send() 入口时已保存 → 即使输入框已被清空也能续上
    const tail = pendingResendContent;
    // 清空,防止下一次手输入时重复触发
    pendingResendContent = null;
    if (tail) {
      // 短暂延时让用户先看到"已采纳"标记再开始打字机
      setTimeout(() => { send(tail); }, 350);
    }
  } catch (e) {
    console.warn("chooseConflict failed:", e);
    alert("澄清请求失败: " + e.message);
  }
}
window.chooseConflict = chooseConflict;

function renderClarificationCards(items) {
  const cards = items
    .map((c) => {
      return `<div class="mem-card conflict-card" data-cid="${c.id}">
        <div><b>⚠️ 渐进式澄清</b></div>
        <div class="meta" style="margin:4px 0 8px">${esc(c.description)}</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button onclick="chooseConflict(${c.id},'existing')" style="border-color:#2563eb">
            采纳「已有」<br><span style="color:#64748b;font-size:12px">${esc(c.memory_existing || "—")}</span>
          </button>
          <button onclick="chooseConflict(${c.id},'new')" style="border-color:#1e874b">
            采纳「新」<br><span style="color:#64748b;font-size:12px">${esc(c.memory_new || "—")}</span>
          </button>
        </div>
      </div>`;
    })
    .join("");
  return cards;
}
