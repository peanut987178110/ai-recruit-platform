<template>
  <VModal title="批量导入简历" wide @close="$emit('close')">
    <div class="field">
      <label class="field-label">目标岗位<span class="req">*</span></label>
      <select v-model="positionId" class="select">
        <option :value="0">请选择岗位</option>
        <option v-for="p in positions" :key="p.id" :value="p.id">
          {{ p.name }}（{{ p.business_line }}）
        </option>
      </select>
    </div>

    <div class="field">
      <label class="field-label">简历文件</label>
      <div
        class="drop" :class="{ over: dragging }"
        @dragover.prevent="dragging = true"
        @dragleave="dragging = false"
        @drop.prevent="onDrop"
        @click="pick"
      >
        <input ref="fileEl" type="file" multiple hidden
               accept=".pdf,.docx,.doc,.txt,.md,.csv,.xlsx,.jpg,.jpeg,.png" @change="onPick" />
        <div class="drop-icon">⇪</div>
        <div class="drop-title">点击选择，或把文件拖到这里</div>
        <div class="drop-desc">
          支持 PDF / Word / 图片 / 纯文本，单文件不超过 20MB，单次最多 500 份
        </div>
      </div>
    </div>

    <div v-if="files.length" class="field">
      <label class="field-label">已选 {{ files.length }} 个文件</label>
      <div class="file-list">
        <div v-for="(f, i) in files.slice(0, 30)" :key="i" class="file-row">
          <span class="truncate">{{ f.name }}</span>
          <span class="tiny muted nowrap">{{ sizeText(f.size) }}</span>
        </div>
        <div v-if="files.length > 30" class="tiny muted center">…还有 {{ files.length - 30 }} 个</div>
      </div>
    </div>

    <div v-if="progress" class="alert alert-info mt-2">
      <span class="alert-icon">ℹ</span>
      <div>
        <div>{{ progress }}</div>
        <div class="bar mt-1" style="max-width: 320px">
          <div class="bar-fill" :style="{ width: '100%', animation: 'pulse 1.4s infinite' }" />
        </div>
      </div>
    </div>

    <div v-if="failed.length" class="mt-2">
      <div class="alert alert-warn mb-2">
        <span class="alert-icon">⚠</span>
        <div>{{ failed.length }} 份文件未能入库，原因见下表。</div>
      </div>
      <table class="table">
        <thead><tr><th>文件</th><th>原因</th></tr></thead>
        <tbody>
          <tr v-for="(f, i) in failed" :key="i">
            <td class="truncate" style="max-width: 260px">{{ f.file }}</td>
            <td class="small">{{ f.reason }}</td>
          </tr>
        </tbody>
      </table>
      <VButton size="sm" class="mt-1" icon="↓" @click="downloadFailed">下载失败清单</VButton>
    </div>

    <template #foot>
      <VButton @click="$emit('close')">{{ done ? '关闭' : '取消' }}</VButton>
      <VButton v-if="!done" variant="primary" :disabled="!files.length || !positionId" :loading="busy" @click="upload">
        开始导入（{{ files.length }} 份）
      </VButton>
    </template>
  </VModal>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import VButton from '../../components/VButton.vue'
import VModal from '../../components/VModal.vue'
import { candApi } from '../../api'
import { toast } from '../../components/toast'

const props = defineProps<{ positions: any[] }>()
const emit = defineEmits<{ close: []; done: [] }>()

const fileEl = ref<HTMLInputElement | null>(null)
const files = ref<File[]>([])
const positionId = ref(0)
const dragging = ref(false)
const busy = ref(false)
const progress = ref('')
const failed = ref<{ file: string; reason: string }[]>([])
const done = ref(false)

function pick() { fileEl.value?.click() }

function onPick(e: Event) {
  const t = e.target as HTMLInputElement
  if (t.files) files.value = [...files.value, ...Array.from(t.files)]
}

function onDrop(e: DragEvent) {
  dragging.value = false
  if (e.dataTransfer?.files) files.value = [...files.value, ...Array.from(e.dataTransfer.files)]
}

function sizeText(b: number) {
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`
  return `${(b / 1024 / 1024).toFixed(1)} MB`
}

async function upload() {
  busy.value = true
  failed.value = []
  progress.value = '正在上传并解析…'
  try {
    // 两个端点用的字段名不同：单个是 file，批量是 files。
    // 之前无论单文件还是多文件都塞 files，单文件走单文件端点时必然 422。
    const isSingle = files.value.length === 1

    const fd = new FormData()
    fd.append('position_id', String(positionId.value))
    if (isSingle) {
      fd.append('file', files.value[0])
    } else {
      files.value.forEach((f) => fd.append('files', f))
    }

    if (isSingle) {
      const r = await candApi.importOne(fd)
      if (r.ok) {
        toast.ok('已接收，正在后台解析打分',
                 r.multi_resume_detected ? `检测到 ${r.multi_resume_detected} 份简历，已拆分` : '')
      } else {
        failed.value = [{ file: files.value[0].name, reason: r.message || '导入失败' }]
      }
    } else {
      const r = await candApi.importBatch(fd)
      failed.value = r.failed || []
      toast.ok(`已接收 ${r.accepted} 份，后台异步处理中`)
      if (r.failed?.length) toast.warn(`有 ${r.failed.length} 份未能入库`)
    }
    done.value = true
    progress.value = ''
    emit('done')
  } catch (e) {
    toast.err(e, '导入失败')
  } finally {
    busy.value = false
    progress.value = ''
  }
}

function downloadFailed() {
  const csv = '﻿文件,原因\n' + failed.value
    .map((f) => `"${f.file}","${f.reason}"`).join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = '导入失败清单.csv'
  a.click()
  URL.revokeObjectURL(a.href)
}
</script>

<style scoped>
.drop {
  border: 1.5px dashed var(--border-strong); border-radius: var(--radius-lg);
  padding: 30px 20px; text-align: center; cursor: pointer;
  transition: all 0.15s; background: var(--bg-0);
}
.drop:hover, .drop.over { border-color: var(--brand); background: var(--ai-bg); }
.drop-icon { font-size: 26px; color: var(--text-2); margin-bottom: 7px; }
.drop-title { font-size: 13.5px; font-weight: 550; margin-bottom: 4px; }
.drop-desc { font-size: 12px; color: var(--text-2); }
.file-list { max-height: 190px; overflow-y: auto; border: 1px solid var(--border); border-radius: var(--radius); }
.file-row {
  display: flex; justify-content: space-between; gap: 10px;
  padding: 6px 11px; font-size: 12.5px; border-bottom: 1px solid var(--border);
}
.file-row:last-child { border-bottom: none; }
</style>
