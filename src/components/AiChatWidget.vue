<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

const aiBase = (import.meta.env.VITE_AI_BASE_URL || import.meta.env.VITE_API_BASE_URL || '').trim()
const AI_ENDPOINT = `${aiBase}/api/ai/chat`

const history = ref<ChatMessage[]>([])
const input = ref('')
const loading = ref(false)
const errorMsg = ref('')
const scrollEl = ref<HTMLElement | null>(null)

let streamingIdx = -1

function scrollBottom() {
  nextTick(() => {
    if (scrollEl.value) {
      scrollEl.value.scrollTop = scrollEl.value.scrollHeight
    }
  })
}

async function sendMessage() {
  const text = input.value.trim()
  if (!text || loading.value) return

  errorMsg.value = ''
  input.value = ''
  loading.value = true

  history.value.push({ role: 'user', content: text })
  history.value.push({ role: 'assistant', content: '' })
  streamingIdx = history.value.length - 1
  scrollBottom()

  try {
    const res = await fetch(AI_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: text,
        history: history.value.slice(0, -2).map((m) => ({ role: m.role, content: m.content })),
      }),
    })

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`)
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let eventBuf = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      eventBuf += decoder.decode(value, { stream: true })
      const parts = eventBuf.split('\n\n')
      eventBuf = parts.pop()

      for (const part of parts) {
        const eventLine = part.match(/^event:\s*(.+)$/m)?.[1]?.trim()
        const dataLine = part.match(/^data:\s*(.+)$/m)?.[1]?.trim()
        if (!eventLine || !dataLine) continue

        let payload: Record<string, unknown>
        try {
          payload = JSON.parse(dataLine)
        } catch {
          continue
        }

        if (eventLine === 'token') {
          history.value[streamingIdx].content += String(payload.text ?? '')
          scrollBottom()
        } else if (eventLine === 'tool_start') {
          history.value[streamingIdx].content += `\n[工具调用: ${String(payload.name ?? 'unknown')}]`
          scrollBottom()
        } else if (eventLine === 'error') {
          errorMsg.value = String(payload.message ?? '未知错误')
        }
      }
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : ''
    if (message.includes('HTTP 401')) {
      errorMsg.value = '未登录或登录已过期（HTTP 401）。请重新登录后再试。'
    } else if (message.includes('HTTP 502')) {
      errorMsg.value = 'AI 服务不可用（HTTP 502）。请先启动主服务：npm run ws:server 或 npm run servers:start'
    } else {
      errorMsg.value = message || '连接失败'
    }
    if (history.value[streamingIdx]?.content === '') {
      history.value.splice(streamingIdx, 1)
    }
  } finally {
    loading.value = false
    streamingIdx = -1
    scrollBottom()
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendMessage()
  }
}

onMounted(scrollBottom)
</script>

<template>
  <div class="ai-chat">
    <div class="ai-chat__header">
      <span class="ai-chat__title">AI 助手</span>
      <span class="ai-chat__sub">读全项目，写 scripts/widgets</span>
    </div>

    <div ref="scrollEl" class="ai-chat__messages-wrap">
      <ul class="ai-chat__messages" aria-label="对话列表">
        <li v-if="history.length === 0" class="ai-chat__empty">
          向 AI 提问，例如：“读取 server/ai_controller.py”
        </li>

        <li
          v-for="(msg, i) in history"
          :key="i"
          :class="['ai-chat__item', `ai-chat__item--${msg.role}`]"
        >
          <span class="ai-chat__role">{{ msg.role === 'user' ? '你' : 'AI' }}</span>
          <pre class="ai-chat__text">{{ msg.content }}<span v-if="loading && i === history.length - 1 && msg.role === 'assistant'" class="ai-chat__cursor">▌</span></pre>
        </li>
      </ul>
    </div>

    <div v-if="errorMsg" class="ai-chat__error">{{ errorMsg }}</div>

    <div class="ai-chat__input-row">
      <textarea
        v-model="input"
        class="ai-chat__input"
        placeholder="输入消息，Enter 发送，Shift+Enter 换行"
        rows="2"
        :disabled="loading"
        @keydown="onKeydown"
      />
      <button class="ai-chat__send" :disabled="loading || !input.trim()" @click="sendMessage">
        {{ loading ? '…' : '发送' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.ai-chat {
  display: flex;
  flex-direction: column;
  height: 420px;
  max-height: 420px;
  background: #1a1a2e;
  color: #e2e8f0;
  border-radius: 8px;
  overflow: hidden;
}

.ai-chat__header {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 10px 14px;
  background: #16213e;
  border-bottom: 1px solid #0f3460;
}

.ai-chat__title { font-size: 15px; font-weight: 600; }
.ai-chat__sub { font-size: 11px; color: #718096; }

.ai-chat__messages-wrap {
  flex: 1;
  overflow-y: auto;
  padding: 12px 14px;
  min-height: 0;
}

.ai-chat__messages {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.ai-chat__empty {
  color: #4a5568;
  text-align: center;
  margin-top: 40px;
}

.ai-chat__item {
  display: flex;
  flex-direction: column;
  max-width: 88%;
}

.ai-chat__item--user { align-self: flex-end; align-items: flex-end; }
.ai-chat__item--assistant { align-self: flex-start; align-items: flex-start; }

.ai-chat__role {
  font-size: 11px;
  color: #718096;
  margin-bottom: 2px;
}

.ai-chat__text {
  margin: 0;
  padding: 8px 12px;
  border-radius: 8px;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.55;
  font-family: inherit;
}

.ai-chat__item--user .ai-chat__text {
  background: #0f3460;
  color: #e2e8f0;
}

.ai-chat__item--assistant .ai-chat__text {
  background: #16213e;
  color: #cbd5e0;
}

.ai-chat__cursor {
  display: inline-block;
  animation: blink 0.8s step-start infinite;
}

@keyframes blink {
  50% { opacity: 0; }
}

.ai-chat__error {
  margin: 0 14px 6px;
  padding: 6px 10px;
  border-radius: 6px;
  background: #742a2a;
  color: #feb2b2;
  font-size: 13px;
}

.ai-chat__input-row {
  display: flex;
  gap: 8px;
  padding: 10px 14px;
  border-top: 1px solid #0f3460;
  background: #16213e;
}

.ai-chat__input {
  flex: 1;
  resize: none;
  background: #1a1a2e;
  border: 1px solid #0f3460;
  border-radius: 6px;
  color: #e2e8f0;
  padding: 6px 10px;
  font-family: inherit;
  outline: none;
}

.ai-chat__input:focus { border-color: #4299e1; }
.ai-chat__input:disabled { opacity: 0.5; }

.ai-chat__send {
  align-self: flex-end;
  padding: 7px 16px;
  border: none;
  border-radius: 6px;
  background: #4299e1;
  color: #fff;
  cursor: pointer;
}

.ai-chat__send:hover:not(:disabled) { background: #3182ce; }
.ai-chat__send:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
