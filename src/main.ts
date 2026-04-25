import { createApp } from 'vue'
import './style.css'
import App from './App.vue'

const AUTH_TOKEN_STORAGE_KEY = 'hyperautomation:auth-token'
const nativeFetch = window.fetch.bind(window)

window.fetch = ((input: RequestInfo | URL, init: RequestInit = {}) => {
  const token = localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)
  if (!token) {
    return nativeFetch(input, init)
  }

  const headers = new Headers(init.headers || {})
  if (!headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  return nativeFetch(input, { ...init, headers })
}) as typeof window.fetch

createApp(App).mount('#app')
