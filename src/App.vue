<script setup>
import { computed, defineAsyncComponent, reactive, ref } from 'vue'
import AiController from './components/ai_controller.vue'
import ScriptControlPage from './components/ScriptControlPage.vue'

const activePage = ref('chat')
const AUTH_STORAGE_KEY = 'hyperautomation:auth-session'
const AUTH_TOKEN_STORAGE_KEY = 'hyperautomation:auth-token'
const apiBase = (import.meta.env.VITE_API_BASE_URL || '').trim()
const authBase = (import.meta.env.VITE_AUTH_BASE_URL || apiBase).trim()
const AUTH_LOGIN_ENDPOINT = authBase ? `${authBase}/api/auth/login` : '/api/auth/login'
const isAuthEndpointConfigured = Boolean(authBase) || import.meta.env.DEV
const isAuthEndpointPlaceholder = /your-server\.example\.com/i.test(authBase)
const isAuthenticated = ref(
  localStorage.getItem(AUTH_STORAGE_KEY) === '1' && !!localStorage.getItem(AUTH_TOKEN_STORAGE_KEY),
)
const loginForm = reactive({ username: '', password: '' })
const authLoading = ref(false)
const authError = ref('')
const loginInfo = ref('')

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

const parseJsonResponse = async (response, requestUrl, fallbackMessage) => {
  const contentType = response.headers.get('content-type') || ''
  if (contentType.toLowerCase().includes('application/json')) {
    return response.json()
  }

  const raw = await response.text()
  const preview = raw.slice(0, 120).replace(/\s+/g, ' ').trim()
  throw new Error(
    `${fallbackMessage}：接口返回了非 JSON 内容，请检查后端地址与路由。URL=${requestUrl}${
      preview ? `，响应片段=${preview}` : ''
    }`,
  )
}

const submitLogin = async () => {
  authError.value = ''
  loginInfo.value = ''
  authLoading.value = true

  if (!isAuthEndpointConfigured) {
    authError.value = '未配置后端地址。请设置 VITE_AUTH_BASE_URL 或 VITE_API_BASE_URL 后重新构建 Android。'
    authLoading.value = false
    return
  }

  if (isAuthEndpointPlaceholder) {
    authError.value = '当前后端地址仍是占位值 your-server.example.com，请先配置真实后端地址后再登录。'
    authLoading.value = false
    return
  }

  const username = loginForm.username.trim()
  const password = loginForm.password

  try {
    const response = await fetch(AUTH_LOGIN_ENDPOINT, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ username, password }),
    })

    if (!response.ok) {
      if (response.status === 401) {
        throw new Error('账号或密码错误')
      }
      throw new Error(`HTTP ${response.status}`)
    }

    const payload = await parseJsonResponse(response, AUTH_LOGIN_ENDPOINT, '登录失败')
    const token = typeof payload?.token === 'string' ? payload.token : ''
    if (!token) {
      throw new Error('登录响应缺少 token')
    }

    localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token)
    isAuthenticated.value = true
    localStorage.setItem(AUTH_STORAGE_KEY, '1')
    loginInfo.value = typeof payload?.expiresAt === 'string' ? `登录成功，有效期至 ${payload.expiresAt}` : '登录成功'
    loginForm.password = ''
  } catch (err) {
    authError.value = err instanceof Error ? err.message : '登录失败'
  }

  authLoading.value = false
}

const logout = () => {
  isAuthenticated.value = false
  localStorage.removeItem(AUTH_STORAGE_KEY)
  localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY)
  authError.value = ''
  loginInfo.value = ''
}
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
      <p v-if="loginInfo" class="login-info">{{ loginInfo }}</p>
      <p class="login-tip">请输入后端配置的账号密码（APP_LOGIN_USERNAME / APP_LOGIN_PASSWORD）</p>
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
    </header>

    <section v-show="activePage === 'chat'" class="panel">
      <div class="panel-head">
        <h2>AI 超级中心</h2>
      </div>
      <div class="panel-body">
        <AiController />
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
        超级中心
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
