<template>
  <div class="modal-mask" @click.self="closeOnMask && $emit('close')">
    <div class="modal" :class="{ wide }">
      <div class="modal-head">
        <span class="modal-title">{{ title }}</span>
        <button class="icon-btn ml-auto" title="关闭" @click="$emit('close')">✕</button>
      </div>
      <div class="modal-body"><slot /></div>
      <div v-if="$slots.foot" class="modal-foot"><slot name="foot" /></div>
    </div>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  title: string
  wide?: boolean
  closeOnMask?: boolean
}>(), { closeOnMask: true })

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
