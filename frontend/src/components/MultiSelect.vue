<template>
  <div ref="root" class="msel" :class="{ open: open, disabled }">
    <!-- 已选标签 + 触发器 -->
    <div class="msel-box" :class="{ focused: open }" @click="toggle">
      <template v-if="selectedItems.length">
        <span v-for="it in visibleTags" :key="it[valueKey]" class="msel-tag">
          <span class="truncate" style="max-width: 150px">{{ it[labelKey] }}</span>
          <span v-if="it[subKey]" class="msel-tag-sub">{{ it[subKey] }}</span>
          <button class="msel-tag-x" type="button" @click.stop="unselect(it[valueKey])">✕</button>
        </span>
        <span v-if="hiddenCount" class="msel-more">+{{ hiddenCount }}</span>
      </template>
      <span v-else class="msel-placeholder">{{ placeholder }}</span>

      <button
        v-if="selected.length && !disabled" type="button"
        class="msel-clear" title="清空" @click.stop="clear"
      >✕</button>
      <span class="msel-caret">{{ open ? '▲' : '▼' }}</span>
    </div>

    <!-- 下拉面板 -->
    <div v-if="open" class="msel-panel">
      <div class="msel-search">
        <span class="msel-search-icon">⌕</span>
        <input
          ref="searchEl" v-model="query" class="msel-input"
          :placeholder="searchPlaceholder" @keydown="onKeydown"
        />
        <button v-if="query" type="button" class="msel-clear-sm" @click="query = ''">✕</button>
      </div>

      <div class="msel-actions">
        <button type="button" class="msel-link" @click="selectAllFiltered">
          全选{{ query ? '搜索结果' : '' }}（{{ filtered.length }}）
        </button>
        <button v-if="selected.length" type="button" class="msel-link" @click="clear">
          清空已选
        </button>
        <span class="ml-auto tiny muted">已选 {{ selected.length }}</span>
      </div>

      <div ref="listEl" class="msel-list">
        <div v-if="!filtered.length" class="msel-empty">
          {{ items.length ? '没有匹配的选项' : emptyText }}
        </div>
        <div
          v-for="(it, i) in filtered" :key="it[valueKey]"
          class="msel-opt" :class="{ on: isSelected(it[valueKey]), cursor: i === cursor }"
          @click="toggleItem(it[valueKey])"
          @mouseenter="cursor = i"
        >
          <span class="msel-check" :class="{ checked: isSelected(it[valueKey]) }">
            {{ isSelected(it[valueKey]) ? '✓' : '' }}
          </span>
          <span class="truncate" style="flex: 1">{{ it[labelKey] }}</span>
          <span v-if="it[subKey]" class="tiny muted nowrap">{{ it[subKey] }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 多选下拉：带搜索、全选、键盘导航。
 *
 * 为什么不用一排 checkbox：候选人/新人数量会增长，超过十几个就铺满屏幕且难找。
 * 下拉在窄空间里能承载大量选项，搜索让"找到某个人"变成一次输入。
 */
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'

const props = withDefaults(defineProps<{
  modelValue: (number | string)[]
  items: Record<string, any>[]
  labelKey?: string
  subKey?: string
  valueKey?: string
  placeholder?: string
  searchPlaceholder?: string
  emptyText?: string
  disabled?: boolean
  maxTags?: number
}>(), {
  labelKey: 'name',
  subKey: '',
  valueKey: 'id',
  placeholder: '请选择',
  searchPlaceholder: '输入关键字搜索…',
  emptyText: '暂无可选项',
  disabled: false,
  maxTags: 4,
})

const emit = defineEmits<{ 'update:modelValue': [v: (number | string)[]] }>()

const root = ref<HTMLElement | null>(null)
const searchEl = ref<HTMLInputElement | null>(null)
const listEl = ref<HTMLElement | null>(null)
const open = ref(false)
const query = ref('')
const cursor = ref(0)

const selected = computed(() => props.modelValue || [])
const isSelected = (v: any) => selected.value.includes(v as never)

const selectedItems = computed(() =>
  props.items.filter((i) => isSelected(i[props.valueKey])))
const visibleTags = computed(() => selectedItems.value.slice(0, props.maxTags))
const hiddenCount = computed(() =>
  Math.max(0, selectedItems.value.length - props.maxTags))

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return props.items
  return props.items.filter((it) =>
    String(it[props.labelKey] ?? '').toLowerCase().includes(q)
    || String(it[props.subKey] ?? '').toLowerCase().includes(q))
})

function toggle() {
  if (props.disabled) return
  open.value = !open.value
  if (open.value) {
    query.value = ''
    cursor.value = 0
    nextTick(() => searchEl.value?.focus())
  }
}

function close() { open.value = false; query.value = '' }

function toggleItem(v: any) {
  const arr = [...selected.value]
  const i = arr.indexOf(v as never)
  i >= 0 ? arr.splice(i, 1) : arr.push(v as never)
  emit('update:modelValue', arr)
}

function unselect(v: any) { toggleItem(v) }

function clear() { emit('update:modelValue', []) }

function selectAllFiltered() {
  const arr = new Set(selected.value)
  filtered.value.forEach((it) => arr.add(it[props.valueKey]))
  emit('update:modelValue', [...arr] as never[])
}

/** 键盘：上下移动、回车选中、Esc 关闭 */
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    cursor.value = Math.min(cursor.value + 1, filtered.value.length - 1)
    scrollCursorIntoView()
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    cursor.value = Math.max(cursor.value - 1, 0)
    scrollCursorIntoView()
  } else if (e.key === 'Enter') {
    e.preventDefault()
    const it = filtered.value[cursor.value]
    if (it) toggleItem(it[props.valueKey])
  } else if (e.key === 'Escape') {
    e.preventDefault()
    close()
  }
}

function scrollCursorIntoView() {
  nextTick(() => {
    const el = listEl.value?.querySelectorAll('.msel-opt')[cursor.value] as HTMLElement | undefined
    el?.scrollIntoView({ block: 'nearest' })
  })
}

function onDocClick(e: MouseEvent) {
  if (open.value && !(e.target as HTMLElement)?.closest('.msel')) close()
}

/** Esc 是全局生效的：打开状态下不论焦点在哪都能关掉，
 *  只绑在搜索框上会在用户点过选项后失效。 */
function onDocKey(e: KeyboardEvent) {
  if (open.value && e.key === 'Escape') {
    e.preventDefault()
    close()
  }
}

onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onDocKey)
})
onUnmounted(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onDocKey)
})
</script>

<style scoped>
.msel { position: relative; width: 100%; }
.msel.disabled { opacity: 0.55; pointer-events: none; }

.msel-box {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  min-height: 38px; padding: 5px 32px 5px 10px;
  background: var(--bg-0); border: 1px solid var(--border-strong);
  border-radius: var(--radius); cursor: pointer; transition: border-color 0.12s;
}
.msel-box:hover { border-color: var(--text-2); }
.msel-box.focused {
  border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-dim);
}
.msel-placeholder { color: var(--text-2); font-size: 13px; padding: 3px 0; }

.msel-tag {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 4px 2px 8px; border-radius: 5px;
  background: var(--brand-dim); border: 1px solid var(--ai-border);
  color: var(--brand-text); font-size: 12px; max-width: 220px;
}
.msel-tag-sub { font-size: 10.5px; opacity: 0.7; }
.msel-tag-x {
  background: none; border: none; color: inherit; cursor: pointer;
  font-size: 10px; padding: 0 3px; opacity: 0.6; font-family: inherit;
}
.msel-tag-x:hover { opacity: 1; }
.msel-more {
  font-size: 11.5px; color: var(--text-2); padding: 2px 4px;
}

.msel-clear, .msel-clear-sm {
  background: none; border: none; color: var(--text-2);
  cursor: pointer; font-size: 11px; font-family: inherit; padding: 2px 4px;
  border-radius: 4px;
}
.msel-clear:hover, .msel-clear-sm:hover { color: var(--text-0); background: var(--bg-hover); }
.msel-clear { position: absolute; right: 26px; top: 50%; transform: translateY(-50%); }
.msel-caret {
  position: absolute; right: 10px; top: 50%; transform: translateY(-50%);
  font-size: 9px; color: var(--text-2); pointer-events: none;
}

.msel-panel {
  position: absolute; top: calc(100% + 5px); left: 0; right: 0;
  z-index: 60; background: var(--bg-1);
  border: 1px solid var(--border-strong); border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg); overflow: hidden;
}

.msel-search {
  display: flex; align-items: center; gap: 7px;
  padding: 8px 10px; border-bottom: 1px solid var(--border);
}
.msel-search-icon { color: var(--text-2); font-size: 14px; }
.msel-input {
  flex: 1; background: none; border: none; outline: none;
  color: var(--text-0); font-size: 13px; font-family: inherit;
}
.msel-input::placeholder { color: var(--text-2); }

.msel-actions {
  display: flex; align-items: center; gap: 12px;
  padding: 6px 10px; border-bottom: 1px solid var(--border);
  background: var(--bg-2);
}
.msel-link {
  background: none; border: none; color: var(--brand-text);
  font-size: 12px; cursor: pointer; font-family: inherit; padding: 0;
}
.msel-link:hover { text-decoration: underline; }

.msel-list { max-height: 240px; overflow-y: auto; padding: 4px; }
.msel-opt {
  display: flex; align-items: center; gap: 9px;
  padding: 7px 9px; border-radius: var(--radius);
  cursor: pointer; font-size: 13px; transition: background 0.1s;
}
.msel-opt:hover, .msel-opt.cursor { background: var(--bg-hover); }
.msel-opt.on { color: var(--brand-text); }

.msel-check {
  width: 16px; height: 16px; flex-shrink: 0; border-radius: 4px;
  border: 1px solid var(--border-strong);
  display: flex; align-items: center; justify-content: center;
  font-size: 10px; color: #fff;
}
.msel-check.checked { background: var(--brand); border-color: var(--brand); }

.msel-empty {
  padding: 20px 12px; text-align: center;
  color: var(--text-2); font-size: 12.5px;
}
</style>
