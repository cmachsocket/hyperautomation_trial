<script setup>
import { ref, nextTick, onMounted } from 'vue'

// 后端聊天接口地址，可通过 Vite env 覆盖
const AI_ENDPOINT = import.meta.env.VITE_AI_ENDPOINT ?? 'http://localhost:8082/api/chat'

const history   = ref([])   // { role: 'user'|'assistant', content: string }
const input     = ref('')
const loading   = ref(false)
const errorMsg  = ref('')
const scrollEl  = ref(null)

// 当前正在流式输出的 assistant 消息索引
let streamingIdx = -1

function scrollBottom() {
  nextTick(() => {
    if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
  })
}

async function sendMessage() {
  const text = input.value.trim()
  if (!text || loading.value) return

  errorMsg.value = ''
  input.value    = ''
  loading.value  = true

  history.value.push({ role: 'user', content: text })
  history.value.push({ role: 'assistant', content: '' })
  streamingIdx = history.value.length - 1
  scrollBottom()

  try {
    const res = await fetch(AI_ENDPOINT, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: text,
        // 传除最新空消息以外的历史
        history: history.value.slice(0, -2).map((m) => ({ role: m.role, content: m.content })),
      }),
    })

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`)
    }

    const reader   = res.body.getReader()
    const decoder  = new TextDecoder()
    let   eventBuf = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      eventBuf += decoder.decode(value, { stream: true })

      // SSE 事件以 \n\n 分隔
      const parts = eventBuf.split('\n\n')
      eventBuf    = parts.pop() // 末尾不完整的留下

      for (const part of parts) {
        const eventLine = part.match(/^event:\s*(.+)$/m)?.[1]?.trim()
        const dataLine  = part.match(/^data:\s*(.+)$/m)?.[1]?.trim()
        if (!eventLine || !dataLine) continue

        let payload
        try { payload = JSON.parse(dataLine) }
        catch { continue }

        if (eventLine === 'token') {
          history.value[streamingIdx].content += payload.text
          scrollBottom()
        } else if (eventLine === 'tool_start') {
          history.value[streamingIdx].content +=
            `\n\`[工具调用: ${payload.name}]\``
          scrollBottom()
        } else if (eventLine === 'tool_end') {
          // 工具返回不再追加，保持简洁
        } else if (eventLine === 'error') {
          errorMsg.value = payload.message ?? '未知错误'
        }
      }
    }
  } catch (err) {
    errorMsg.value = err.message ?? '连接失败'
    // 移除空白的 assistant 占位
    if (history.value[streamingIdx]?.content === '') {
      history.value.splice(streamingIdx, 1)
    }
  } finally {
    loading.value  = false
    streamingIdx   = -1
    scrollBottom()
  }
}

function onKeydown(e) {
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
      <span class="ai-chat__sub">仅可访问 scripts / pages / widgets</span>
    </div>

    <div ref="scrollEl" class="ai-chat__messages">
      <div v-if="history.length === 0" class="ai-chat__empty">
        向 AI 提问，例如："列出 scripts 目录下的文件"
      </div>

      <div
        v-for="(msg, i) in history"
        :key="i"
        :class="['ai-chat__bubble', `ai-chat__bubble--${msg.role}`]"
      >
        <span class="ai-chat__role">{{ msg.role === 'user' ? '你' : 'AI' }}</span>
        <pre class="ai-chat__text">{{ msg.content }}<span v-if="loading && i === history.length - 1 && msg.role === 'assistant'" class="ai-chat__cursor">▌</span></pre>
      </div>
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
      <button
        class="ai-chat__send"
        :disabled="loading || !input.trim()"
        @click="sendMessage"
      >
        {{ loading ? '…' : '发送' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.ai-chat {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 320px;
  background: #1a1a2e;
  border-radius: 8px;
  overflow: hidden;
  font-size: 14px;
  color: #e2e8f0;
}

.ai-chat__header {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 10px 14px;
  background: #16213e;
  border-bottom: 1px solid #0f3460;
}
.ai-chat__title { font-weight: 600; font-size: 15px; }
.ai-chat__sub   { font-size: 11px; color: #718096; }

.ai-chat__messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.ai-chat__empty {
  color: #4a5568;
  text-align: center;
  margin-top: 40px;
  font-size: 13px;
}

.ai-chat__bubble {
  display: flex;
  flex-direction: column;
  max-width: 88%;
}
.ai-chat__bubble--user      { align-self: flex-end; align-items: flex-end; }
.ai-chat__bubble--assistant { align-self: flex-start; align-items: flex-start; }

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
  font-family: inherit;
  font-size: 14px;
  line-height: 1.55;
}
.ai-chat__bubble--user .ai-chat__text {
  background: #0f3460;
  color: #e2e8f0;
}
.ai-chat__bubble--assistant .ai-chat__text {
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
  background: #742a2a;
  color: #feb2b2;
  border-radius: 6px;
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
  font-size: 14px;
  font-family: inherit;
  outline: none;
  transition: border-color 0.2s;
}
.ai-chat__input:focus {
  border-color: #4299e1;
}
.ai-chat__input:disabled {
  opacity: 0.5;
}

.ai-chat__send {
  align-self: flex-end;
  padding: 7px 16px;
  background: #4299e1;
  color: #fff;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  transition: background 0.2s;
}
.ai-chat__send:hover:not(:disabled) { background: #3182ce; }
.ai-chat__send:disabled             { opacity: 0.4; cursor: not-allowed; }
</style>
