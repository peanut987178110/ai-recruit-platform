<template>
  <div>
    <div class="card">
      <div class="card-head">
        <span class="card-title">岗位能力模型</span>
        <span class="card-desc">三个业务模块共用同一份数据：筛选用它打分，面试用它出题，培训用它设考核项</span>
        <VButton size="sm" variant="primary" class="ml-auto" icon="+" @click="createOpen = true">
          新建岗位
        </VButton>
      </div>

      <VState :items="rows" :loading="loading" title="还没有岗位能力模型" icon="◆"
              desc="能力模型是平台底座，定义某岗位的录用标准。可以新建岗位，或从同序列模板派生后调整差异项。">
        <div class="grid grid-2">
          <div v-for="m in rows" :key="m.id" class="model-card" @click="$router.push(`/model/${m.id}`)">
            <div class="row-between">
              <div class="row" style="gap: 8px">
                <span class="bold" style="font-size: 14px">{{ m.position_name }}</span>
                <span class="tag tag-gray">{{ m.version }}</span>
                <span v-if="m.active" class="tag tag-ok">已启用</span>
                <span v-else class="tag tag-gray">已停用</span>
              </div>
              <span class="tag tag-blue">{{ m.seq }}</span>
            </div>

            <div class="row mt-2" style="gap: 16px">
              <span class="tiny muted">业务线 {{ m.business_line }}</span>
              <span class="tiny muted">职级 {{ m.level_range || '—' }}</span>
              <span class="tiny muted">在招 {{ m.headcount }}</span>
            </div>

            <div class="mt-2">
              <div class="row-between mb-1">
                <span class="tiny muted">{{ m.items.length }} 个能力项 · 权重合计 {{ m.total_weight * 10 }}%</span>
                <span class="tiny" :style="{ color: m.total_weight === 10 ? 'var(--ok)' : 'var(--danger)' }">
                  {{ m.total_weight === 10 ? '✓ 校验通过' : `✕ 差值 ${(10 - m.total_weight) * 10}%` }}
                </span>
              </div>
              <div class="bar">
                <div class="bar-fill" :class="m.total_weight === 10 ? 'ok' : 'danger'"
                     :style="{ width: Math.min(100, m.total_weight * 10) + '%' }" />
              </div>
            </div>

            <div class="row wrap mt-2" style="gap: 5px">
              <span v-for="it in m.items.slice(0, 4)" :key="it.id" class="tag tag-gray tiny">
                {{ it.name.slice(0, 14) }}<template v-if="it.is_veto"> · 否决</template>
              </span>
              <span v-if="m.items.length > 4" class="tiny muted">+{{ m.items.length - 4 }}</span>
            </div>

            <div class="row-between mt-2">
              <span v-if="m.veto_count" class="tiny muted">否决项 {{ m.veto_count }}/3</span>
              <span v-else class="tiny muted">未设否决项</span>
              <span class="tiny muted">{{ fmtDate(m.updated_at) }}</span>
            </div>
          </div>
        </div>
      </VState>
    </div>

    <VModal v-if="createOpen" title="新建岗位与能力模型" wide @close="createOpen = false">
      <div class="grid grid-2">
        <div class="field">
          <label class="field-label">岗位名称<span class="req">*</span></label>
          <input v-model="form.name" class="input" placeholder="例如：后端开发工程师" maxlength="50" />
          <div class="field-hint">同一业务线下不可重名</div>
        </div>
        <div class="field">
          <label class="field-label">岗位序列<span class="req">*</span></label>
          <select v-model="form.seq" class="select">
            <option v-for="s in ['技术', '产品', '运营', '职能']" :key="s">{{ s }}</option>
          </select>
        </div>
        <div class="field">
          <label class="field-label">业务线</label>
          <select v-model="form.business_line" class="select">
            <option v-for="b in lines" :key="b.name" :value="b.name">{{ b.name }}</option>
          </select>
          <div class="field-hint">决定哪条业务线的用人经理能看到该岗位的候选人</div>
        </div>
        <div class="field">
          <label class="field-label">职级区间</label>
          <input v-model="form.level_range" class="input" placeholder="例如：P6-P7" />
        </div>
        <div class="field">
          <label class="field-label">在招人数</label>
          <input v-model.number="form.headcount" type="number" min="1" class="input" />
        </div>
        <div class="field">
          <label class="field-label">从模板派生</label>
          <select v-model.number="form.template_position_id" class="select">
            <option :value="0">不派生，创建空白模型</option>
            <option v-for="m in sameP" :key="m.position_id" :value="m.position_id">
              {{ m.position_name }}（{{ m.items.length }} 项）
            </option>
          </select>
          <div class="field-hint">派生后能力项与权重完整继承，修改不回写原模板</div>
        </div>
      </div>

      <div class="field">
        <label class="field-label">职位描述（可选，用于 AI 提炼能力项）</label>
        <textarea v-model="form.jd_text" class="textarea" rows="4"
                  placeholder="粘贴 JD 内容，创建后可在编辑器中一键提炼能力项候选" />
      </div>

      <template #foot>
        <VButton @click="createOpen = false">取消</VButton>
        <VButton variant="primary" :disabled="!form.name" :loading="busy" @click="doCreate">
          创建
        </VButton>
      </template>
    </VModal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import VButton from '../components/VButton.vue'
import VModal from '../components/VModal.vue'
import VState from '../components/VState.vue'
import { lineApi, modelApi } from '../api'
import { fmtDate } from '../api/labels'
import { toast } from '../components/toast'

const router = useRouter()
const rows = ref<any[]>([])
const loading = ref(false)
const createOpen = ref(false)
const busy = ref(false)

const form = ref({
  name: '', seq: '技术', business_line: '通用', level_range: '',
  headcount: 1, jd_text: '', template_position_id: 0,
})

const sameP = computed(() => rows.value.filter((m) => m.seq === form.value.seq && m.active))

async function load() {
  loading.value = true
  try { rows.value = await modelApi.list() } catch (e) { toast.err(e) } finally { loading.value = false }
}

async function doCreate() {
  busy.value = true
  try {
    const m = await modelApi.create({ ...form.value, template_position_id: form.value.template_position_id || null })
    toast.ok(`已创建岗位「${m.position_name}」`, m.active ? '' : '模型默认停用，需在编辑器中调整权重至 100% 后显式启用')
    createOpen.value = false
    router.push(`/model/${m.id}`)
  } catch (e) {
    toast.err(e)
  } finally {
    busy.value = false
  }
}

// 岗位业务线必须来自字典：自由输入一旦与账号的业务线字面不一致，用人经理就看不到这些候选人
const lines = ref<{ name: string }[]>([])
onMounted(async () => {
  try { lines.value = await lineApi.list() } catch { lines.value = [] }
  if (lines.value.length && !lines.value.some((l) => l.name === form.value.business_line)) {
    form.value.business_line = lines.value[0].name
  }
  await load()
})
</script>

<style scoped>
.model-card {
  padding: 14px 15px; border: 1px solid var(--border);
  border-radius: var(--radius-lg); cursor: pointer; transition: all 0.13s;
  background: var(--bg-0);
}
.model-card:hover { border-color: var(--brand); background: var(--bg-2); transform: translateY(-1px); }
</style>
