<script setup>
import { onMounted, onUnmounted, ref } from 'vue'

const apiBase = import.meta.env.VITE_WS_SERVER_URL || 'http://localhost:8081'
const scripts = ref([])
const loading = ref(false)
const error = ref('')
const pendingActionId = ref('')
let timer

const fetchScripts = async () => {
  loading.value = true
  error.value = ''

  try {
    const response = await fetch(`${apiBase}/api/scripts`)
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const result = await response.json()
    scripts.value = result.scripts || []
  } catch (err) {
    error.value = err instanceof Error ? err.message : '获取脚本列表失败'
  } finally {
    loading.value = false
  }
}

const controlScript = async (id, action) => {
  pendingActionId.value = `${action}:${id}`
  error.value = ''

  try {
    const response = await fetch(`${apiBase}/api/scripts/${action}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ id }),
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    await fetchScripts()
  } catch (err) {
    error.value = err instanceof Error ? err.message : `${action === 'start' ? '启动' : '关闭'}脚本失败`
  } finally {
    pendingActionId.value = ''
  }
}

onMounted(async () => {
  await fetchScripts()
  timer = setInterval(fetchScripts, 2000)
})

onUnmounted(() => {
  clearInterval(timer)
})
</script>

<template>
  <section class="slot-content">
    <h3>Script Control Page</h3>

    <div class="toolbar">
      <button @click="fetchScripts" :disabled="loading">
        {{ loading ? '刷新中...' : '刷新列表' }}
      </button>
    </div>

    <div v-if="error" class="mini-card">请求失败：{{ error }}</div>

    <div v-else-if="!scripts.length" class="mini-card">暂无脚本。</div>

    <div v-else>
      <div v-for="item in scripts" :key="item.id" class="mini-card script-row">
        <div class="script-main">
          <div><strong>{{ item.name }}</strong></div>
          <div>id: {{ item.id }}</div>
          <div>状态：{{ item.running ? '运行中' : '空闲' }}</div>
          <div>PID：{{ item.pid ?? '-' }}</div>
          <div>最近启动：{{ item.lastStartedAt || '-' }}</div>
          <div>最近退出码：{{ item.lastExitCode ?? '-' }}</div>
        </div>

        <div class="toolbar vertical-actions">
          <button
            @click="controlScript(item.id, 'start')"
            :disabled="item.running || pendingActionId === `start:${item.id}`"
          >
            {{ pendingActionId === `start:${item.id}` ? '启动中...' : item.running ? '运行中' : '启动' }}
          </button>
          <button
            @click="controlScript(item.id, 'stop')"
            :disabled="!item.running || pendingActionId === `stop:${item.id}`"
          >
            {{ pendingActionId === `stop:${item.id}` ? '关闭中...' : '关闭' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.script-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
}

.script-main {
  display: grid;
  gap: 2px;
}

.vertical-actions {
  flex-direction: column;
}
</style>
