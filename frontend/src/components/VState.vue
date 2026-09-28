<template>
  <div v-if="loading" class="col">
    <div
      v-for="i in rows" :key="i"
      class="skeleton"
      :style="{ height, width: widths[(i - 1) % widths.length] }"
    />
  </div>
  <div v-else-if="!items.length" class="empty">
    <div class="empty-icon">{{ icon }}</div>
    <div class="empty-title">{{ title }}</div>
    <div v-if="desc" class="empty-desc">{{ desc }}</div>
    <slot />
  </div>
  <div v-else><slot name="default" /></div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  items: unknown[]
  loading?: boolean
  title?: string
  desc?: string
  icon?: string
  rows?: number
  height?: string
  /** 骨架屏各行的宽度，循环取用 */
  widths?: string[]
}>(), {
  loading: false,
  title: '暂无数据',
  icon: '○',
  rows: 4,
  height: '52px',
  widths: () => ['100%', '88%', '94%', '72%'],
})
</script>
