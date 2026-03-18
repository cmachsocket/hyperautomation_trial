<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'

const apiBase = import.meta.env.VITE_WS_SERVER_URL || ''
const data = ref({ size: 0, entries: [], updatedAt: '' })
const loading = ref(false)
const updatingSwitch = ref(false)
const error = ref('')
const demoSwitchId = 'device-0'
let timer

const demoSwitchState = computed(() => {
  const entry = data.value.entries.find((item) => item.id === demoSwitchId)
  return Boolean(entry?.payload?.switchOn)
})

const upsertEntryFromServer = (id, updated, updatedAt) => {
  const entries = [...data.value.entries]
  const index = entries.findIndex((item) => item.id === id)
  const next = { id, ...(updated || {}) }

  if (index >= 0) {
    entries[index] = next
  } else {
    entries.push(next)
  }

  data.value = {
    size: entries.length,
    entries,
    updatedAt: updatedAt || new Date().toISOString(),
  }
}

const fetchUpdatedMap = async () => {
  loading.value = true
  error.value = ''

  try {
    const response = await fetch(`${apiBase}/api/merged-map/${encodeURIComponent(demoSwitchId)}`)

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const item = await response.json()
    data.value = {
      size: 1,
      entries: [item],
      updatedAt: item.updatedAt || new Date().toISOString(),
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '获取数据失败'
  } finally {
    loading.value = false
  }
}

const toggleDemoSwitch = async () => {
  updatingSwitch.value = true
  error.value = ''

  try {
    const commandRequest = {
      id: demoSwitchId,
      command: 'toggle',
      source: 'frontend-widget',
      payload: {},
    }

    const response = await fetch(`${apiBase}/api/device/command`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(commandRequest),
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const result = await response.json()
    upsertEntryFromServer(result.id, result.updated, result.updatedAt)
    await fetchUpdatedMap()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '更新开关失败'
  } finally {
    updatingSwitch.value = false
  }
}

onMounted(async () => {
  await fetchUpdatedMap()
  timer = setInterval(fetchUpdatedMap, 2000)
})

onUnmounted(() => {
  clearInterval(timer)
})
</script>

<template>
  <section class="slot-content">
    <h3>Updated Map Widget</h3>
    <div class="mini-card">数据条数：{{ data.size }}</div>
    <div class="mini-card">最近更新时间：{{ data.updatedAt || '暂无' }}</div>
    <div class="mini-card">
      示例开关（{{ demoSwitchId }}）：
      <strong>{{ demoSwitchState ? 'ON' : 'OFF' }}</strong>
    </div>

    <div class="toolbar">
      <button @click="fetchUpdatedMap" :disabled="loading">
        {{ loading ? '加载中...' : '手动刷新' }}
      </button>
      <button @click="toggleDemoSwitch" :disabled="updatingSwitch">
        {{ updatingSwitch ? '提交中...' : demoSwitchState ? '关闭开关' : '打开开关' }}
      </button>
    </div>

    <div v-if="error" class="mini-card">请求失败：{{ error }}</div>

    <div v-else-if="!data.entries.length" class="mini-card">
      暂无数据。请先用测试客户端发送带 id 的 JSON。
    </div>

    <div v-else>
      <div v-for="item in data.entries" :key="item.id" class="mini-card">
        <div><strong>id:</strong> {{ item.id }}</div>
        <pre>{{ JSON.stringify(item, null, 2) }}</pre>
      </div>
    </div>
  </section>
</template>

<style scoped>
pre {
  margin: 8px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
}

button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
