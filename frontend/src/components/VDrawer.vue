<template>
  <div v-if="visible" class="drawer-mask" @click.self="$emit('close')">
    <div class="drawer" :style="width ? { width } : {}">
      <div class="drawer-head">
        <span class="drawer-title">{{ title }}</span>
        <span v-if="subtitle" class="tag tag-gray">{{ subtitle }}</span>
        <button class="icon-btn ml-auto" title="关闭" @click="$emit('close')">✕</button>
      </div>
      <div class="drawer-body"><slot /></div>
      <div v-if="$slots.foot" class="drawer-foot"><slot name="foot" /></div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ visible: boolean; title: string; subtitle?: string; width?: string }>()
defineEmits<{ close: [] }>()
</script>

<style scoped>
.icon-btn {
  background: none; border: none; color: var(--text-1);
  font-size: 14px; cursor: pointer; padding: 3px 6px;
  border-radius: var(--radius); font-family: inherit; line-height: 1;
}
.icon-btn:hover { background: var(--bg-hover); color: var(--text-0); }
</style>
