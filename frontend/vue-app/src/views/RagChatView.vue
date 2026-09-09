<template>
  <div class="rag-chat">
    <header class="chat-header">
      <div>
        <h2>问答</h2>
        <p class="subtitle">基于事件库的可溯源问答 · 每句答案带事件引用</p>
      </div>
      <div class="header-actions">
        <button class="text-btn" @click="clearChat">清空对话</button>
        <router-link to="/events">← 事件流</router-link>
      </div>
    </header>

    <div v-if="messages.length === 0" class="starter">
      <p>试试点击下方推荐问题：</p>
      <div class="suggestions">
        <button
          v-for="q in suggestedQuestions"
          :key="q"
          class="suggestion"
          @click="setQuery(q)"
        >
          {{ q }}
        </button>
      </div>
    </div>

    <div class="messages">
      <div
        v-for="(msg, idx) in messages"
        :key="idx"
        class="message"
        :class="{ self: msg.role === 'user', error: msg.error }"
      >
        <div class="bubble">
          <p v-if="msg.role === 'user'">{{ msg.text }}</p>
          <div v-else class="answer">
            <p v-for="(sentence, sidx) in msg.sentences" :key="sidx">
              {{ sentence.text }}
              <button
                v-for="cid in sentence.citations"
                :key="cid"
                class="citation"
                @click="showEvent(cid)"
              >
                事件#{{ cid }}
              </button>
            </p>
            <div v-if="msg.refusal" class="refusal-badge">信息不足 · 已拒答</div>
            <div v-if="msg.citations_valid === false" class="warn">引用校验失败</div>
          </div>
        </div>
      </div>
      <div v-if="streaming" class="message">
        <div class="bubble">正在生成…</div>
      </div>
    </div>

    <div class="input-bar">
      <input
        v-model="query"
        placeholder="输入问题，例如：政务数据共享条例的主要内容是什么？"
        @keydown.enter="ask"
      />
      <button :disabled="streaming || !query.trim()" @click="ask">发送</button>
    </div>

    <div v-if="selectedEvent" class="modal-overlay" @click.self="closeEvent">
      <div class="modal">
        <div class="modal-header">
          <h3>事件 #{{ selectedEvent.id }} 详情</h3>
          <button class="close-btn" @click="closeEvent">×</button>
        </div>
        <p class="modal-summary">{{ selectedEvent.merged_summary }}</p>
        <ul class="modal-reports">
          <li v-for="r in selectedEvent.reports" :key="r.id">
            <span class="modal-site">{{ r.source_site }}</span>
            <a :href="r.original_url" target="_blank">{{ r.title }}</a>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { API_BASE } from '../api.js'

const messages = ref([])
const query = ref('')
const streaming = ref(false)
const selectedEvent = ref(null)
const suggestedQuestions = [
  '政务数据共享条例的主要内容是什么？',
  '习近平主席祝贺李在明当选韩国总统时说了什么？',
  '全国人大常委会启动节约能源法执法检查，主要检查哪些方面？',
  '太阳系外宜居星球有哪些？',
]

function setQuestion(q) {
  query.value = q
  ask()
}

function setQuery(q) {
  query.value = q
}

async function showEvent(eventId) {
  try {
    const res = await fetch(`${API_BASE}/events/${eventId}`)
    if (!res.ok) throw new Error('not found')
    selectedEvent.value = await res.json()
  } catch (e) {
    selectedEvent.value = { id: eventId, merged_summary: '（加载事件详情失败）', reports: [] }
  }
}

function closeEvent() {
  selectedEvent.value = null
}

function clearChat() {
  messages.value = []
}

function parseSentence(text) {
  const regex = /\[事件#(\d+)\]/g
  const parts = []
  let lastIndex = 0
  let match
  const citations = []
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index))
    }
    citations.push(parseInt(match[1], 10))
    lastIndex = regex.lastIndex
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex))
  }
  return { text: parts.join('').trim(), citations }
}

async function ask() {
  const q = query.value.trim()
  if (!q || streaming.value) return

  messages.value.push({ role: 'user', text: q })
  query.value = ''
  streaming.value = true

  const userId = localStorage.getItem('user_id') || undefined
  const url = userId
    ? `${API_BASE}/rag/ask?user_id=${encodeURIComponent(userId)}`
    : `${API_BASE}/rag/ask`

  const answerMsg = { role: 'assistant', sentences: [], refusal: false, citations_valid: true }
  messages.value.push(answerMsg)

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: q }),
    })

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let eof = false

    while (!eof) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const events = buffer.split('\n\n')
      buffer = events.pop() || ''

      for (const evt of events) {
        if (!evt.trim()) continue
        const lines = evt.split('\n')
        let eventName = ''
        const dataLines = []
        for (const line of lines) {
          if (line.startsWith('event: ')) eventName = line.slice(7)
          else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
        }
        const data = dataLines.join('\n')

        if (eventName === 'eof') {
          eof = true
          break
        } else if (eventName === 'token') {
          const sentence = parseSentence(data)
          if (sentence.text) {
            answerMsg.sentences.push(sentence)
          }
        } else if (eventName === 'done') {
          try {
            const done = JSON.parse(data)
            answerMsg.refusal = done.refusal || false
            answerMsg.citations_valid = done.citations_valid !== false
            if (done.error) {
              answerMsg.error = true
              answerMsg.sentences.push({ text: '生成失败', citations: [] })
            }
          } catch {}
        }
      }
    }

    // Process any remaining buffer content
    if (buffer.trim()) {
      const lines = buffer.split('\n')
      let eventName = ''
      const dataLines = []
      for (const line of lines) {
        if (line.startsWith('event: ')) eventName = line.slice(7)
        else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
      }
      if (eventName === 'token') {
        const data = dataLines.join('\n')
        const sentence = parseSentence(data)
        if (sentence.text) {
          answerMsg.sentences.push(sentence)
        }
      } else if (eventName === 'done') {
        try {
          const data = dataLines.join('\n')
          const done = JSON.parse(data)
          answerMsg.refusal = done.refusal || false
          answerMsg.citations_valid = done.citations_valid !== false
        } catch {}
      }
    }
  } catch (e) {
    answerMsg.error = true
    answerMsg.sentences = [{ text: '请求失败：' + e.message, citations: [] }]
  } finally {
    streaming.value = false
  }
}
</script>

<style scoped>
.rag-chat {
  max-width: 720px;
  margin: 0 auto;
  padding: clamp(32px, 6vh, 56px) clamp(20px, 6vw, 48px) 120px;
}
.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 24px;
  border-bottom: 1px solid var(--line);
  padding-bottom: 24px;
}
.chat-header h2 {
  margin: 0 0 6px;
  font-family: var(--serif);
  font-size: clamp(1.6rem, 4vw, 2.2rem);
  line-height: 1.15;
  color: var(--ink-strong);
}
.subtitle {
  margin: 0;
  color: var(--muted);
  font-size: 14px;
}
.header-actions {
  display: flex;
  gap: 16px;
  align-items: center;
  flex-shrink: 0;
  margin-top: 6px;
}
.text-btn {
  background: none;
  border: none;
  color: var(--muted);
  cursor: pointer;
  font-size: 14px;
  transition: color 0.15s;
}
.text-btn:hover {
  color: var(--ink-strong);
}
.header-actions a {
  color: var(--ink-strong);
  text-decoration: none;
  font-size: 14px;
  border-bottom: 1px solid transparent;
  transition: border-color 0.15s;
}
.header-actions a:hover {
  border-color: var(--ink-strong);
}
.starter {
  text-align: center;
  padding: 32px 0;
}
.starter p {
  color: var(--muted);
  margin-bottom: 14px;
  font-size: 14px;
}
.suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: center;
}
.suggestion {
  padding: 9px 16px;
  border: 1px solid var(--line-strong);
  background: var(--surface);
  cursor: pointer;
  font-size: 14px;
  color: var(--ink-soft);
  transition: all 0.15s;
  border-radius: var(--radius-sm);
}
.suggestion:hover {
  border-color: var(--ink-strong);
  color: var(--ink-strong);
  box-shadow: var(--shadow-sm);
}
.messages {
  margin-bottom: 16px;
}
.message {
  margin-bottom: 16px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}
.message.self {
  align-items: flex-end;
}
.bubble {
  max-width: 82%;
  padding: 8px 12px;
  background: var(--surface);
  color: var(--ink);
  line-height: 1.7;
  font-size: 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  border-top-left-radius: var(--radius-sm);
  box-shadow: var(--shadow-sm);
}
.message.self .bubble {
  background: #e8eef5;
  color: #1e3a5f;
  border-color: #cdd9e6;
  padding: 4px 10px;
  font-size: 13px;
  line-height: 1.5;
  border-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-sm);
  box-shadow: var(--shadow-sm);
}
.message.error .bubble {
  background: #fef2f2;
  color: #dc2626;
  border-color: #fecaca;
}
.answer p {
  margin: 0 0 8px;
}
.answer p:last-child {
  margin-bottom: 0;
}
.citation {
  background: var(--ink-strong);
  color: #fff;
  border: none;
  padding: 1px 8px;
  font-size: 11.5px;
  cursor: pointer;
  margin-left: 4px;
  letter-spacing: 0.02em;
  transition: background 0.15s;
  border-radius: 6px;
}
.citation:hover {
  background: var(--ink-strong-hover);
}
.refusal-badge {
  display: inline-block;
  margin-top: 8px;
  padding: 2px 10px;
  font-size: 12px;
  background: #fef2f2;
  color: #991b1b;
  border: 1px solid #fecaca;
}
.warn {
  font-size: 12px;
  color: #dc2626;
  margin-top: 6px;
}
.input-bar {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  width: min(720px, calc(100vw - 32px));
  box-sizing: border-box;
  display: flex;
  gap: 8px;
  padding: 10px;
  background: var(--surface);
  border: 1px solid var(--line-strong);
  box-shadow: var(--shadow-lg);
  border-radius: var(--radius-lg);
}
.input-bar input {
  flex: 1;
  padding: 10px 12px;
  border: none;
  background: transparent;
  font-size: 15px;
  color: var(--ink);
}
.input-bar input:focus {
  outline: none;
}
.input-bar button {
  padding: 10px 22px;
  border: none;
  background: var(--ink-strong);
  color: #fff;
  cursor: pointer;
  font-size: 14px;
  transition: background 0.15s;
  border-radius: var(--radius-sm);
}
.input-bar button:hover:not(:disabled) {
  background: var(--ink-strong-hover);
}
.input-bar button:disabled {
  background: var(--faint);
  cursor: not-allowed;
}
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(9, 9, 11, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 20px;
  z-index: 100;
}
.modal {
  background: var(--surface);
  border: 1px solid var(--line);
  max-width: 560px;
  width: 100%;
  max-height: 80vh;
  overflow-y: auto;
  padding: 24px;
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
}
.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.modal-header h3 {
  margin: 0;
  font-family: var(--serif);
  font-size: 18px;
}
.close-btn {
  background: none;
  border: 1px solid var(--line-strong);
  width: 28px;
  height: 28px;
  font-size: 16px;
  cursor: pointer;
  color: var(--muted);
  transition: all 0.15s;
  border-radius: 6px;
}
.close-btn:hover {
  color: var(--ink-strong);
  border-color: var(--ink-strong);
}
.modal-summary {
  line-height: 1.8;
  margin-bottom: 16px;
}
.modal-reports {
  list-style: none;
  padding: 0;
  margin: 0;
}
.modal-reports li {
  margin-bottom: 10px;
  font-size: 13.5px;
  line-height: 1.6;
}
.modal-site {
  display: inline-block;
  min-width: 88px;
  margin-right: 10px;
  font-size: 12px;
  color: var(--muted);
  font-weight: 600;
}
.modal-reports a {
  color: var(--ink-strong);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: border-color 0.15s;
}
.modal-reports a:hover {
  border-color: var(--ink-strong);
}
</style>