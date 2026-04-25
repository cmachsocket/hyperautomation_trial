<script setup lang="ts">
import { computed, ref } from 'vue'

const value = ref(42)
const level = computed(() => (value.value >= 60 ? '高' : value.value >= 30 ? '中' : '低'))
</script>

<template>
  <section class="slot-content">
    <h3>Stats Widget</h3>
    <div class="mini-card">当前值：{{ value }}</div>
    <div class="mini-card">等级：{{ level }}</div>
    <div class="toolbar">
      <button @click="value += 5">+5</button>
      <button @click="value -= 5">-5</button>
    </div>
  </section>
</template>
