<script setup>
import { computed, defineAsyncComponent, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import AiChatWidget from './components/AiChatWidget.vue'
import ScriptControlPage from './components/ScriptControlPage.vue'

const activePage = ref('chat')
const AUTH_STORAGE_KEY = 'hyperautomation:auth-session'
const DEMO_USERNAME = import.meta.env.VITE_LOGIN_USERNAME || 'admin'
const DEMO_PASSWORD = import.meta.env.VITE_LOGIN_PASSWORD || '123456'
const isAuthenticated = ref(localStorage.getItem(AUTH_STORAGE_KEY) === '1')
const loginForm = reactive({ username: '', password: '' })
const authLoading = ref(false)
const authError = ref('')
const apiBase = import.meta.env.VITE_WS_SERVER_URL || ''
const currentAppVersion = import.meta.env.VITE_APP_VERSION || 'dev-local'
const latestAppVersion = ref('')
const isVersionCheckEnabled = import.meta.env.PROD
const versionCheckIntervalMs = Number(import.meta.env.VITE_VERSION_CHECK_INTERVAL_MS || 30000)
const autoReloadOnUpdate = import.meta.env.VITE_AUTO_RELOAD_ON_UPDATE !== 'false'
const autoReloadDelayMs = Number(import.meta.env.VITE_AUTO_RELOAD_DELAY_MS || 1500)
const publishing = ref(false)
const publishInfo = ref('')
const publishError = ref('')
let versionTimer
let reloadTimer

const componentModules = import.meta.glob('./components/dynamic/**/*.vue')

const moduleMap = {
  ...componentModules,
}

const toLabel = (path) => {
  const fileName = path.split('/').pop()?.replace('.vue', '') || path
  return fileName
}

const options = computed(() => {
  const componentOptions = Object.keys(componentModules).map((path) => ({
    path,
    type: '模块',
    label: toLabel(path),
  }))

  return componentOptions
})

const hasUpdate = computed(() => {
  return isVersionCheckEnabled && !!latestAppVersion.value && latestAppVersion.value !== currentAppVersion
})

const createPanel = (id, slotName, selectedPath = '') => ({
  id,
  slotName,
  selectedPath,
})

const panels = reactive([])
let panelIndex = 1

const addPanel = () => {
  panels.push(createPanel(panelIndex, `栏位 ${panelIndex}`))
  panelIndex += 1
}

const removePanel = (panelId) => {
  const targetIndex = panels.findIndex((panel) => panel.id === panelId)
  if (targetIndex >= 0) {
    panels.splice(targetIndex, 1)
  }
}

addPanel()

const getAsyncView = (path) => {
  if (!path || !moduleMap[path]) {
    return null
  }
  return defineAsyncComponent(moduleMap[path])
}

const checkLatestVersion = async () => {
  if (!isVersionCheckEnabled) {
    return
  }

  try {
    const response = await fetch(`${apiBase}/api/app-version`, {
      method: 'GET',
      cache: 'no-store',
      headers: {
        'Cache-Control': 'no-cache',
      },
    })

    if (!response.ok) {
      return
    }

    const result = await response.json()
    const version = typeof result?.version === 'string' ? result.version.trim() : ''
    if (version) {
      latestAppVersion.value = version
    }
  } catch {
    // 忽略检测失败，避免影响页面主体功能
  }
}

const submitLogin = async () => {
  authError.value = ''
  authLoading.value = true

  const username = loginForm.username.trim()
  const password = loginForm.password

  await new Promise((resolve) => setTimeout(resolve, 250))

  if (username === DEMO_USERNAME && password === DEMO_PASSWORD) {
    isAuthenticated.value = true
    localStorage.setItem(AUTH_STORAGE_KEY, '1')
    loginForm.password = ''
  } else {
    authError.value = '账号或密码错误'
  }

  authLoading.value = false
}

const logout = () => {
  isAuthenticated.value = false
  localStorage.removeItem(AUTH_STORAGE_KEY)
  authError.value = ''
}

const reloadPage = () => {
  window.location.reload()
}

const publishVersion = async () => {
  publishing.value = true
  publishError.value = ''
  publishInfo.value = ''

  try {
    const response = await fetch(`${apiBase}/api/app-version/publish`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({}),
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const result = await response.json()
    const version = typeof result?.version === 'string' ? result.version.trim() : ''
    if (version) {
      latestAppVersion.value = version
      publishInfo.value = `已发布版本：${version}`
    } else {
      publishInfo.value = '发布已触发'
    }
  } catch (error) {
    publishError.value = error instanceof Error ? error.message : '发布失败'
  } finally {
    publishing.value = false
  }
}

watch(hasUpdate, (nextHasUpdate) => {
  if (!nextHasUpdate || !autoReloadOnUpdate || reloadTimer) {
    return
  }

  const markerKey = 'app:last-auto-reloaded-version'
  if (sessionStorage.getItem(markerKey) === latestAppVersion.value) {
    return
  }

  sessionStorage.setItem(markerKey, latestAppVersion.value)

  reloadTimer = setTimeout(() => {
    reloadPage()
  }, Number.isFinite(autoReloadDelayMs) && autoReloadDelayMs >= 0 ? autoReloadDelayMs : 1500)
})

onMounted(() => {
  if (!isVersionCheckEnabled) {
    return
  }

  void checkLatestVersion()
  versionTimer = setInterval(
    checkLatestVersion,
    Number.isFinite(versionCheckIntervalMs) && versionCheckIntervalMs > 0 ? versionCheckIntervalMs : 30000,
  )
})

onUnmounted(() => {
  if (versionTimer) {
    clearInterval(versionTimer)
  }
  if (reloadTimer) {
    clearTimeout(reloadTimer)
  }
})
</script>

<template>
  <main v-if="!isAuthenticated" class="login-shell">
    <section class="login-card">
      <div class="login-card__brand">HyperAutomation</div>
      <h1>控制台登录</h1>
      <p>登录后可使用 AI Chat、脚本控制和动态栏位。</p>

      <form class="login-form" @submit.prevent="submitLogin">
        <label>
          <span>账号</span>
          <input v-model="loginForm.username" type="text" autocomplete="username" placeholder="请输入账号" required />
        </label>

        <label>
          <span>密码</span>
          <input v-model="loginForm.password" type="password" autocomplete="current-password" placeholder="请输入密码" required />
        </label>

        <button type="submit" :disabled="authLoading">
          {{ authLoading ? '登录中...' : '登录' }}
        </button>
      </form>

      <p v-if="authError" class="login-error">{{ authError }}</p>
      <p class="login-tip">默认账号：{{ DEMO_USERNAME }}，默认密码：{{ DEMO_PASSWORD }}</p>
    </section>
  </main>

  <main v-else class="app-shell">
    <header class="header">
      <div class="header-top">
        <div>
          <h1>多栏动态加载演示（Vue + HMR）</h1>
          <p>每个栏位先是空白，选择一个组件或页面后会异步加载，并在开发模式下自动热更新。</p>
        </div>
        <button class="logout-btn" @click="logout">退出登录</button>
      </div>
      <div class="publish-toolbar">
        <button @click="publishVersion" :disabled="publishing" class="publish-btn">
          {{ publishing ? '发布中...' : '发布' }}
        </button>
        <span v-if="publishInfo" class="publish-info">{{ publishInfo }}</span>
        <span v-if="publishError" class="publish-error">发布失败：{{ publishError }}</span>
      </div>
      <div v-if="hasUpdate" class="update-banner">
        <span>
          检测到新版本（当前 {{ currentAppVersion }}，最新 {{ latestAppVersion }}）
          {{ autoReloadOnUpdate ? '，页面将自动刷新' : '' }}
        </span>
        <button @click="reloadPage">立即刷新</button>
      </div>
    </header>

    <section v-show="activePage === 'chat'" class="panel">
      <div class="panel-head">
        <h2>AI Chat</h2>
      </div>
      <div class="panel-body">
        <AiChatWidget />
      </div>
    </section>

    <section v-show="activePage === 'script'" class="panel">
      <div class="panel-head">
        <h2>Script Control</h2>
      </div>
      <div class="panel-body">
        <ScriptControlPage />
      </div>
    </section>

    <section v-show="activePage === 'dynamic'" class="panel-grid">
      <article class="panel">
        <div class="panel-head">
          <h2>栏位管理</h2>
          <button @click="addPanel">增加栏位</button>
        </div>
        <div class="panel-body">
          当前栏位数：{{ panels.length }}
        </div>
      </article>

      <article v-for="panel in panels" :key="panel.id" class="panel">
        <div class="panel-head">
          <h2>{{ panel.slotName }}</h2>
          <div>
            <select v-model="panel.selectedPath">
              <option value="">保持空白</option>
              <option v-for="item in options" :key="item.path" :value="item.path">
                {{ item.type }} · {{ item.label }}
              </option>
            </select>
            <button @click="removePanel(panel.id)">删除栏位</button>
          </div>
        </div>

        <div class="panel-body">
          <component :is="getAsyncView(panel.selectedPath)" v-if="panel.selectedPath" />
          <div v-else class="placeholder">空白栏位</div>
        </div>
      </article>
    </section>

    <footer class="bottom-nav">
      <button :class="['bottom-nav__btn', { 'is-active': activePage === 'chat' }]" @click="activePage = 'chat'">
        AI Chat
      </button>
      <button :class="['bottom-nav__btn', { 'is-active': activePage === 'script' }]" @click="activePage = 'script'">
        Script Control
      </button>
      <button :class="['bottom-nav__btn', { 'is-active': activePage === 'dynamic' }]" @click="activePage = 'dynamic'">
        动态栏位
      </button>
    </footer>
  </main>
</template>
