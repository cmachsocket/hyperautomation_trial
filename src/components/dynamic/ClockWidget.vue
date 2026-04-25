<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

const now = ref(new Date())
let timer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  timer = setInterval(() => {
    now.value = new Date()
  }, 1000)
})

onUnmounted(() => {
  if (timer) {
    clearInterval(timer)
  }
})
</script>

<template>
  <section class="slot-content">
    <h3>Clock Widget</h3>
    <div class="mini-card">当前时间：{{ now.toLocaleTimeString() }}</div>
    <div class="mini-card">当前日期：{{ now.toLocaleDateString() }}</div>
  </section>
</template>
