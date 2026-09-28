<template>
  <div>
    <div class="card">
      <div class="card-head">
        <span class="card-title">培训方案</span>
        <span class="card-desc">由岗位能力模型加内部 SOP 知识库生成，须带教人确认后才可发布</span>
        <VButton size="sm" variant="primary" class="ml-auto" icon="+" @click="createOpen = true">
          生成新方案
        </VButton>
        <VButton size="sm" icon="⟳" :loading="loading" @click="load">刷新</VButton>
      </div>

      <VState :items="rows" :loading="loading" title="还没有培训方案" icon="◈"
              desc="选择岗位后，系统会按能力项组织培训章节，并从知识库检索学习材料与出题依据。">
        <table class="table">
          <thead>
            <tr>
              <th>方案</th>
              <th style="width: 110px">岗位</th>
              <th style="width: 80px">章节</th>
              <th style="width: 80px">题目</th>
              <th style="width: 90px">提交</th>
              <th style="width: 100px">状态</th>
              <th style="width: 150px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in rows" :key="p.id">
              <td class="bold clickable" @click="$router.push(`/training/${p.id}`)">{{ p.title }}</td>
              <td class="small">{{ p.position_name }}</td>
              <td class="num">{{ p.chapter_count }}</td>
              <td class="num">{{ p.question_count }}</td>
              <td class="num">{{ p.submission_count }}</td>
              <td>
                <span class="tag" :class="p.status === '已发布' ? 'tag-ok' : 'tag-warn'">{{ p.status }}</span>
              </td>
              <td>
                <VButton size="sm" variant="primary" @click="$router.push(`/training/${p.id}`)">打开</VButton>
              </td>
            </tr>
          </tbody>
        </table>
      </VState>
    </div>

    <VModal v-if="createOpen" title="生成培训方案" @close="createOpen = false">
      <div class="field">
        <label class="field-label">选择岗位<span class="req">*</span></label>
        <select v-model.number="positionId" class="select">
          <option :value="0">请选择岗位</option>
          <option v-for="p in positions" :key="p.id" :value="p.id">
            {{ p.name }}（{{ p.business_line }}）
          </option>
        </select>
        <div class="field-hint">培训章节会与岗位能力项一一对应，无孤立章节</div>
      </div>
      <div class="field">
        <label class="field-label row" style="gap: 8px">
          <input type="checkbox" v-model="withExam" style="accent-color: var(--brand)" />
          <span>同时生成考核题库（四类题型）</span>
        </label>
      </div>
      <div class="alert alert-info">
        <span class="alert-icon">ℹ</span>
        <div>
          生成内容一律标记为<b>草稿</b>，须带教人确认后才可发布给新人。
          主观题的终评分数由带教人给出，AI 只给要点覆盖分析。
        </div>
      </div>
      <template #foot>
        <VButton @click="createOpen = false">取消</VButton>
        <VButton variant="primary" :disabled="!positionId" :loading="busy" @click="doCreate">
          生成
        </VButton>
      </template>
    </VModal>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import VButton from '../components/VButton.vue'
import VModal from '../components/VModal.vue'
import VState from '../components/VState.vue'
import { modelApi, trainingApi } from '../api'
import { toast } from '../components/toast'

const router = useRouter()
const rows = ref<any[]>([])
const positions = ref<any[]>([])
const loading = ref(false)
const createOpen = ref(false)
const busy = ref(false)
const positionId = ref(0)
const withExam = ref(true)

async function load() {
  loading.value = true
  try { rows.value = await trainingApi.plans() } catch (e) { toast.err(e) } finally { loading.value = false }
}

async function doCreate() {
  busy.value = true
  try {
    const r = await trainingApi.create({ position_id: positionId.value, with_exam: withExam.value })
    toast.ok(`已生成 ${r.outline.length} 章培训大纲`, '内容为草稿，请审核后发布')
    createOpen.value = false
    router.push(`/training/${r.plan_id}`)
  } catch (e) {
    toast.err(e, '生成失败')
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try { positions.value = (await modelApi.positions()).filter((p: any) => p.status === '在招') } catch { /* ignore */ }
  await load()
})
</script>
