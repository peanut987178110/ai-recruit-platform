<template>
  <div class="toast-wrap">
    <transition-group name="toast">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="`toast-${t.kind}`">
        <span class="toast-ico">{{ ICON[t.kind] }}</span>
        <div class="toast-body">
          <div class="toast-text">{{ t.text }}</div>
          <div v-if="t.detail" class="toast-detail">{{ t.detail }}</div>
        </div>
      </div>
    </transition-group>
  </div>
</template>

<script setup lang="ts">
import { toasts } from './toast'

const ICON = { info: 'ℹ', ok: '✓', warn: '⚠', danger: '✕' }
</script>

<style scoped>
.toast-wrap {
  position: fixed; top: 16px; right: 16px; z-index: 900;
  display: flex; flex-direction: column; gap: 9px; max-width: 400px;
}
.toast {
  display: flex; gap: 10px; padding: 11px 14px;
  background: var(--bg-1); border: 1px solid var(--border-strong);
  border-left: 3px solid var(--brand);
  border-radius: var(--radius); box-shadow: var(--shadow-lg);
  font-size: 13px; align-items: flex-start;
}
.toast-ok { border-left-color: var(--ok); }
.toast-warn { border-left-color: var(--warn); }
.toast-danger { border-left-color: var(--danger); }
.toast-ico { flex-shrink: 0; margin-top: 1px; }
.toast-ok .toast-ico { color: var(--ok); }
.toast-warn .toast-ico { color: var(--warn); }
.toast-danger .toast-ico { color: var(--danger); }
.toast-info .toast-ico { color: var(--brand); }
.toast-text { line-height: 1.6; }
.toast-detail { font-size: 11.5px; color: var(--text-2); margin-top: 3px; line-height: 1.6; }

.toast-enter-active, .toast-leave-active { transition: all 0.24s cubic-bezier(0.22, 1, 0.36, 1); }
.toast-enter-from { opacity: 0; transform: translateX(24px); }
.toast-leave-to { opacity: 0; transform: translateX(24px); }
</style>
