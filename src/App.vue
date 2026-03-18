<script setup>
import { computed, defineAsyncComponent, reactive, ref } from 'vue'
import AiChatWidget from './components/AiChatWidget.vue'
import ScriptControlPage from './components/ScriptControlPage.vue'

const activePage = ref('chat')

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
</script>

<template>
  <main class="app-shell">
    <header class="header">
      <h1>多栏动态加载演示（Vue + HMR）</h1>
      <p>每个栏位先是空白，选择一个组件或页面后会异步加载，并在开发模式下自动热更新。</p>
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
