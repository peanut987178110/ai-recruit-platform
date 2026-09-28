<template>
  <button class="btn" :class="cls" :disabled="disabled || loading" @click="$emit('click', $event)">
    <span v-if="loading" class="spinner" />
    <span v-else-if="icon" class="ico">{{ icon }}</span>
    <slot />
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  variant?: 'default' | 'primary' | 'ok' | 'danger' | 'ghost'
  size?: 'sm' | 'md' | 'lg'
  icon?: string
  loading?: boolean
  disabled?: boolean
}>(), { variant: 'default', size: 'md' })

defineEmits<{ click: [e: MouseEvent] }>()

const cls = computed(() => [
  props.variant !== 'default' ? `btn-${props.variant}` : '',
  props.size !== 'md' ? `btn-${props.size}` : '',
].filter(Boolean).join(' '))
</script>
