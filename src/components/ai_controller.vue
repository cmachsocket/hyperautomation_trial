<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import AiChatWidget from './AiChatWidget.vue'

const apiBase = (import.meta.env.VITE_API_BASE_URL || '').trim()

const assets = ref([])
const summary = ref({ total: 0, by_type: {}, by_status: {} })
const loading = ref(false)
const error = ref('')
const syncing = ref(false)
const syncingMsg = ref('')
const actionPendingId = ref('')

const filters = ref({ type: '', status: '', owner: '', tag: '' })
const syncOptions = ref({
  idStrategy: 'name_md5',
  archiveMissing: true,
  recursive: true,
  extensions: '.js,.ts,.py,.sh',
})

const lifecyclePhaseOptions = [
  'discovery',
  'development',
  'testing',
  'staging',
  'production',
  'deprecation',
  'archived',
]

const assetsApi = computed(() => `${apiBase}/api/coe/assets`)

const parseResponse = async (response) => {
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(payload?.error || `HTTP ${response.status}`)
  }
  return payload
}

const buildFilterQuery = () => {
  const params = new URLSearchParams()
  if (filters.value.type.trim()) params.set('type', filters.value.type.trim())
  if (filters.value.status.trim()) params.set('status', filters.value.status.trim())
  if (filters.value.owner.trim()) params.set('owner', filters.value.owner.trim())
  if (filters.value.tag.trim()) params.set('tag', filters.value.tag.trim())
  return params.toString()
}

const refreshSummary = async () => {
  const response = await fetch(`${assetsApi.value}?summary=1`, { cache: 'no-store' })
  summary.value = await parseResponse(response)
}

const refreshAssets = async () => {
  const query = buildFilterQuery()
  const url = query ? `${assetsApi.value}?${query}` : assetsApi.value
  const response = await fetch(url, { cache: 'no-store' })
  const payload = await parseResponse(response)
  assets.value = Array.isArray(payload.assets) ? payload.assets : []
}

const refreshAll = async () => {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([refreshSummary(), refreshAssets()])
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载资产失败'
  } finally {
    loading.value = false
  }
}

const syncScripts = async () => {
  syncing.value = true
  syncingMsg.value = ''
  error.value = ''

  const extensionList = syncOptions.value.extensions
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)

  try {
    const response = await fetch(`${apiBase}/api/coe/assets/sync/scripts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id_strategy: syncOptions.value.idStrategy,
        archive_missing: syncOptions.value.archiveMissing,
        recursive: syncOptions.value.recursive,
        extensions: extensionList,
      }),
    })
    const payload = await parseResponse(response)
    syncingMsg.value = `同步完成: 新增 ${payload.added}, 更新 ${payload.updated}, 下线 ${payload.removed}`
    await refreshAll()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '同步脚本失败'
  } finally {
    syncing.value = false
  }
}

const advanceLifecycle = async (assetId, phase) => {
  if (!phase) {
    return
  }
  actionPendingId.value = `${assetId}:${phase}`
  error.value = ''
  try {
    const response = await fetch(`${apiBase}/api/coe/assets/${assetId}/lifecycle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phase }),
    })
    await parseResponse(response)
    await refreshAll()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '更新生命周期失败'
  } finally {
    actionPendingId.value = ''
  }
}

const advanceLifecycleNext = async (assetId) => {
  actionPendingId.value = `${assetId}:next`
  error.value = ''
  try {
    const response = await fetch(`${apiBase}/api/coe/assets/${assetId}/lifecycle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'next' }),
    })
    await parseResponse(response)
    await refreshAll()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '推进下一阶段失败'
  } finally {
    actionPendingId.value = ''
  }
}

const archiveAsset = async (assetId) => {
  actionPendingId.value = `${assetId}:archive`
  error.value = ''
  try {
    const response = await fetch(`${apiBase}/api/coe/assets/${assetId}`, {
      method: 'DELETE',
    })
    await parseResponse(response)
    await refreshAll()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '归档失败'
  } finally {
    actionPendingId.value = ''
  }
}

onMounted(() => {
  void refreshAll()
})
</script>

<template>
  <section class="center-wrap">
    <article class="center-card center-card--asset">
      <header class="center-head">
        <h3>超级中心 · 生命周期管理</h3>
        <button @click="refreshAll" :disabled="loading">{{ loading ? '刷新中...' : '刷新' }}</button>
      </header>

      <div class="summary-grid">
        <div class="summary-item">
          <span>总资产</span>
          <strong>{{ summary.total || 0 }}</strong>
        </div>
        <div class="summary-item" v-for="(count, type) in summary.by_type || {}" :key="`t-${type}`">
          <span>{{ type }}</span>
          <strong>{{ count }}</strong>
        </div>
      </div>

      <div class="filter-grid">
        <input v-model="filters.type" placeholder="type: device/script/workflow/ai_skill" />
        <input v-model="filters.status" placeholder="status: development/testing..." />
        <input v-model="filters.owner" placeholder="owner" />
        <input v-model="filters.tag" placeholder="tag" />
        <button @click="refreshAll" :disabled="loading">应用筛选</button>
      </div>

      <div class="sync-grid">
        <select v-model="syncOptions.idStrategy">
          <option value="name_md5">ID: name + md5</option>
          <option value="name_mtime">ID: name + mtime</option>
          <option value="path">ID: path</option>
        </select>
        <input v-model="syncOptions.extensions" placeholder=".js,.ts,.py,.sh" />
        <label><input v-model="syncOptions.archiveMissing" type="checkbox" />归档缺失脚本</label>
        <label><input v-model="syncOptions.recursive" type="checkbox" />递归扫描</label>
        <button @click="syncScripts" :disabled="syncing">{{ syncing ? '同步中...' : '同步脚本到资产库' }}</button>
      </div>

      <p v-if="syncingMsg" class="hint ok">{{ syncingMsg }}</p>
      <p v-if="error" class="hint err">{{ error }}</p>

      <div class="asset-list">
        <article class="asset-item" v-for="asset in assets" :key="asset.id">
          <div class="asset-item__main">
            <h4>{{ asset.name }}</h4>
            <p>ID: {{ asset.id }}</p>
            <p>类型: {{ asset.type }} | 状态: {{ asset.status }}</p>
            <p v-if="asset.script_path">路径: {{ asset.script_path }}</p>
            <p>Owner: {{ asset.metadata?.owner || '-' }}</p>
          </div>
          <div class="asset-item__actions">
            <button @click="advanceLifecycleNext(asset.id)" :disabled="actionPendingId === `${asset.id}:next`">
              {{ actionPendingId === `${asset.id}:next` ? '推进中...' : '下一阶段' }}
            </button>
            <select @change="(e) => advanceLifecycle(asset.id, e.target.value)" :disabled="!!actionPendingId">
              <option value="">推进生命周期...</option>
              <option v-for="phase in lifecyclePhaseOptions" :key="phase" :value="phase">{{ phase }}</option>
            </select>
            <button @click="archiveAsset(asset.id)" :disabled="actionPendingId === `${asset.id}:archive`">
              {{ actionPendingId === `${asset.id}:archive` ? '处理中...' : '归档' }}
            </button>
          </div>
        </article>
        <div v-if="!loading && assets.length === 0" class="empty">没有符合条件的资产</div>
      </div>
    </article>

    <article class="center-card center-card--ai">
      <header class="center-head">
        <h3>超级中心 · AI 能力入口</h3>
      </header>
      <AiChatWidget />
    </article>
  </section>
</template>

<style scoped>
.center-wrap {
  width: 100%;
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 12px;
}

@media (max-width: 980px) {
  .center-wrap {
    grid-template-columns: 1fr;
  }
}

.center-card {
  border: 1px solid #465267;
  border-radius: 10px;
  padding: 10px;
  background: #0f172a;
}

.center-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.center-head h3 {
  margin: 0;
  font-size: 15px;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(90px, 1fr));
  gap: 8px;
  margin-bottom: 10px;
}

.summary-item {
  border: 1px solid #3b4b66;
  border-radius: 8px;
  padding: 6px 8px;
  display: grid;
}

.filter-grid,
.sync-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 10px;
}

@media (max-width: 760px) {
  .filter-grid,
  .sync-grid {
    grid-template-columns: 1fr;
  }
}

.asset-list {
  display: grid;
  gap: 8px;
  max-height: 480px;
  overflow: auto;
}

.asset-item {
  border: 1px solid #3d4b61;
  border-radius: 8px;
  padding: 8px;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
}

.asset-item__main h4 {
  margin: 0 0 4px;
}

.asset-item__main p {
  margin: 2px 0;
  font-size: 12px;
}

.asset-item__actions {
  display: grid;
  gap: 6px;
  align-content: start;
}

.hint {
  margin: 6px 0;
  font-size: 12px;
}

.hint.ok {
  color: #5eead4;
}

.hint.err {
  color: #fca5a5;
}

.empty {
  border: 1px dashed #60708a;
  border-radius: 8px;
  padding: 14px;
  text-align: center;
}
</style>
