<template>
  <div>
    <div class="grid grid-4 mb-3">
      <div class="stat">
        <div class="stat-label">待处理</div>
        <div class="stat-value">{{ pending }}<span class="stat-unit">份</span></div>
        <div class="stat-hint">需复核 + 待安排面试 + 解析异常</div>
      </div>
      <div class="stat">
        <div class="stat-label">今日处理</div>
        <div class="stat-value">{{ data.today?.processed ?? 0 }}<span class="stat-unit">份</span></div>
        <div class="stat-hint">近 24 小时复核动作</div>
      </div>
      <div class="stat">
        <div class="stat-label">今日到岸</div>
        <div class="stat-value">{{ data.today?.incoming ?? 0 }}<span class="stat-unit">份</span></div>
        <div class="stat-hint">ATS 推送 + 手工导入</div>
      </div>
      <div class="stat">
        <div class="stat-label">高分档</div>
        <div class="stat-value" style="color: var(--ok)">{{ data.tiers?.['高分档'] ?? 0 }}<span class="stat-unit">人</span></div>
        <div class="stat-hint">置信度与总分达标，可直接安排面试</div>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <span class="card-title">候选人队列</span>
        <div class="seg ml-auto">
          <button
            v-for="t in TABS" :key="t.key"
            class="seg-item" :class="{ active: tab === t.key }"
            @click="tab = t.key; load()"
          >
            {{ t.label }}
            <span v-if="countOf(t.key)" class="tab-count">{{ countOf(t.key) }}</span>
          </button>
        </div>
        <VButton size="sm" icon="↑" @click="showImport = true">导入简历</VButton>
        <VButton size="sm" icon="⟳" :loading="loading" @click="load">刷新</VButton>
      </div>

      <div class="row wrap mb-2">
        <input v-model="keyword" class="input" style="max-width: 220px" placeholder="搜索候选人姓名" @keyup.enter="load" />
        <select v-model="positionId" class="select" style="max-width: 210px" @change="load">
          <option :value="0">全部岗位</option>
          <option v-for="p in positions" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
        <select v-if="tab === 'all'" v-model="tier" class="select" style="max-width: 140px" @change="load">
          <option value="">全部分档</option>
          <option value="高分档">高分档</option>
          <option value="中间档">中间档</option>
          <option value="低分档">低分档</option>
        </select>
        <span class="tiny muted ml-auto">按角色隔离数据：{{ user?.role }}</span>
      </div>

      <div v-if="hint" class="alert alert-info mb-2">
        <span class="alert-icon">ℹ</span>
        <div>{{ hint }}</div>
      </div>

      <VState :items="rows" :loading="loading" :title="emptyTitle" :desc="emptyDesc" icon="▤">
        <table class="table">
          <thead>
            <tr>
              <th style="width: 30px"></th>
              <th>候选人</th>
              <th>岗位</th>
              <th style="width: 84px">总分</th>
              <th style="width: 84px">分档</th>
              <th style="width: 112px">状态</th>
              <th>风险提示</th>
              <th style="width: 150px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in rows" :key="c.id" class="clickable" @click="open(c)">
              <td>
                <span v-if="c.injection_flag" class="tag tag-danger" title="简历含疑似指令文本，已剥离并标记待审">!</span>
                <span v-else-if="c.need_confirm" class="tag tag-warn" title="存在低置信字段，需人工确认">?</span>
                <span v-else-if="c.tier === '高分档'" style="color: var(--ok)">●</span>
                <span v-else style="color: var(--text-2)">○</span>
              </td>
              <td>
                <div class="row" style="gap: 6px">
                  <span class="bold" :style="{ color: isUnnamed(c) ? 'var(--warn)' : '' }">{{ c.name }}</span>
                  <span v-if="isUnnamed(c)" class="tag tag-warn tiny" title="姓名抽取失败，需人工补录后才能推进">
                    待补录姓名
                  </span>
                </div>
                <div class="tiny muted truncate" style="max-width: 300px">{{ c.summary || c.preview }}</div>
              </td>
              <td class="small">{{ c.position_name }}</td>
              <td>
                <span class="num bold" :style="{ color: scoreColor(c.total_score) }">
                  {{ c.total_score ? num(c.total_score) : '—' }}
                </span>
              </td>
              <td><span v-if="c.tier" class="tag" :class="tierColor(c.tier)">{{ c.tier }}</span><span v-else class="muted">—</span></td>
              <td><span class="tag" :class="STATUS_TAG[c.status] || 'tag-gray'">{{ c.status }}</span></td>
              <td>
                <span v-for="t in (c.risk_tags || []).slice(0, 2)" :key="t" class="tag tag-warn tiny" style="margin-right: 3px">{{ t }}</span>
                <span v-if="!c.risk_tags?.length" class="tiny muted">—</span>
              </td>
              <td @click.stop>
                <template v-if="c.status === '待复核'">
                  <VButton size="sm" variant="ok" @click="quick(c, '采纳')">采纳</VButton>
                  <VButton size="sm" variant="danger" style="margin-left: 4px" @click="openReject(c)">否决</VButton>
                </template>
                <template v-else-if="c.status === '待定池'">
                  <VButton size="sm" @click="openRescue(c)">捞回</VButton>
                </template>
                <template v-else-if="c.status === '已归档'">
                  <span class="tiny muted">已归档</span>
                </template>
                <VButton v-else size="sm" variant="ghost" @click="open(c)">查看</VButton>
              </td>
            </tr>
          </tbody>
        </table>
      </VState>
    </div>

    <!-- 否决原因（原因必选，验收 A2-8） -->
    <VModal v-if="rejecting" title="否决候选人" @close="rejecting = null">
      <div class="alert alert-warn mb-2">
        <span class="alert-icon">⚠</span>
        <div>
          将否决 <b>1</b> 名候选人「{{ rejecting.name }}」。候选人将转入待定池并保留
          {{ rejecting.poolDays }} 天，可捞回。归档不等于拒绝，不会触发任何对外通知。
        </div>
      </div>
      <div class="field">
        <label class="field-label">否决原因<span class="req">*</span></label>
        <div class="col" style="gap: 6px">
          <label v-for="r in REJECT_REASONS" :key="r" class="radio-row">
            <input v-model="rejectReason" type="radio" :value="r" />
            <span>{{ r }}</span>
          </label>
        </div>
      </div>
      <div class="field">
        <label class="field-label">备注（可选）</label>
        <textarea v-model="rejectNote" class="textarea" rows="2" placeholder="补充说明，将写入回流样本池用于优化" />
      </div>
      <template #foot>
        <VButton @click="rejecting = null">取消</VButton>
        <VButton variant="danger" :disabled="!rejectReason" :loading="acting" @click="doReject">
          确认否决
        </VButton>
      </template>
    </VModal>

    <!-- 捞回 -->
    <VModal v-if="rescuing" title="从待定池捞回" @close="rescuing = null">
      <div class="alert alert-info mb-2">
        <span class="alert-icon">ℹ</span>
        <div>
          捞回后候选人转入复核队列，原分档 <b>{{ rescuing.tier }}</b> 会记为误杀线索，
          用于每周复盘模型是否存在漏判。
        </div>
      </div>
      <div class="field">
        <label class="field-label">捞回原因</label>
        <textarea v-model="rescueReason" class="textarea" rows="2"
                  placeholder="例如：业务方反馈该候选人项目经历匹配度高" />
      </div>
      <template #foot>
        <VButton @click="rescuing = null">取消</VButton>
        <VButton variant="primary" :loading="acting" @click="doRescue">确认捞回</VButton>
      </template>
    </VModal>

    <ImportModal v-if="showImport" :positions="positions" @close="showImport = false" @done="onImported" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import VButton from '../components/VButton.vue'
import VModal from '../components/VModal.vue'
import VState from '../components/VState.vue'
import ImportModal from './parts/ImportModal.vue'
import { candApi, modelApi } from '../api'
import { REJECT_REASONS, STATUS_TAG, num, scoreColor, tierColor } from '../api/labels'
import { toast } from '../components/toast'

const props = defineProps<{ user?: any }>()
const router = useRouter()

const TABS = [
  { key: '待复核', label: '待复核' },
  { key: '待安排面试', label: '待安排' },
  { key: '待定池', label: '待定池' },
  { key: '解析异常', label: '异常' },
  { key: 'all', label: '全部' },
]

const rows = ref<any[]>([])
const positions = ref<any[]>([])
const loading = ref(false)
const tab = ref('待复核')
const keyword = ref('')
const positionId = ref(0)
const tier = ref('')
const data = ref<any>({})
const hint = ref('')

const rejecting = ref<any>(null)
const rejectReason = ref('')
const rejectNote = ref('')
const rescuing = ref<any>(null)
const rescueReason = ref('')
const acting = ref(false)
const showImport = ref(false)
// 只在首次加载时自动切换到有数据的队列，之后尊重用户的选择
let autoSwitched = false

const pending = computed(() => {
  const c = data.value.counts || {}
  return (c['待复核'] || 0) + (c['待安排面试'] || 0) + (c['解析异常'] || 0)
})

// 空状态要告诉用户「东西在别的标签里」，否则会以为系统没数据
const emptyTitle = computed(() => {
  if (tab.value === 'all') {
    return (data.value.counts && Object.keys(data.value.counts).length)
      ? '当前筛选条件下没有候选人' : '还没有候选人'
  }
  const others = TABS.filter((t) => t.key !== tab.value && t.key !== 'all' && countOf(t.key) > 0)
  if (others.length) {
    return `「${TABS.find((t) => t.key === tab.value)?.label}」队列为空`
  }
  return '还没有候选人'
})

const emptyDesc = computed(() => {
  const others = TABS.filter((t) => t.key !== tab.value && t.key !== 'all' && countOf(t.key) > 0)
  if (others.length) {
    return `其他队列有数据：${others.map((t) => `${t.label} ${countOf(t.key)} 份`).join('、')}。`
      + '点击上方标签切换查看。'
  }
  return '导入简历后系统会自动解析、打分并分层。高分档直接转待安排面试，'
    + '中间档进入人工复核，低分档进入待定池并保留 7 天捞回窗口。'
})

/** 姓名未识别：需先补录，不能推进到面试 */
function isUnnamed(c: any) {
  return String(c.name || '').startsWith('未识别-') || !c.name || c.name === '待解析'
}

function countOf(key: string) {
  if (key === 'all') return 0
  return data.value.counts?.[key] || 0
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, unknown> = {}
    if (tab.value !== 'all') params.status = tab.value
    if (keyword.value) params.keyword = keyword.value
    if (positionId.value) params.position_id = positionId.value
    if (tier.value) params.tier = tier.value
    const d = await candApi.list(params)
    rows.value = d.items || []
    data.value = d
    updateHint()
    // 首次进入时若当前队列为空，自动切到有数据的队列，避免落在空页
    if (!rows.value.length && !autoSwitched && !keyword.value && !positionId.value) {
      const target = TABS.find((t) => t.key !== 'all' && t.key !== tab.value && countOf(t.key) > 0)
      if (target) {
        autoSwitched = true
        tab.value = target.key
        await load()
        return
      }
    }
    autoSwitched = true
  } catch (e) {
    toast.err(e, '加载候选人失败')
  } finally {
    loading.value = false
  }
}

function updateHint() {
  const s = rows.value[0]?.status
  if (s === '待复核') hint.value = '中间档候选人强制人工复核：模型对这部分样本把握最低，不允许批量一键通过。'
  else if (s === '待定池') hint.value = '待定池按剩余天数升序排列，剩余 2 天内标黄。到期自动归档，归档不等于拒绝，也不触发任何对外通知。'
  else if (s === '解析异常') hint.value = '解析失败的文件会保留原始记录，可重新上传或人工补录，补录界面只显示缺失字段。'
  else if (s === '待安排面试') hint.value = '高分档候选人已自动推进到此队列，视为「建议进入面试」，你仍可退回复核。'
  else hint.value = ''
}

async function open(c: any) {
  if (c.status === '解析异常' || c.status === '待定池' || c.status === '已归档') {
    router.push(`/candidate/${c.id}`)
    return
  }
  router.push(`/candidate/${c.id}`)
}

async function quick(c: any, action: string) {
  acting.value = true
  try {
    await candApi.review(c.id, { action })
    toast.ok(`已${action}「${c.name}」`)
    await load()
  } catch (e) {
    toast.err(e)
  } finally {
    acting.value = false
  }
}

function openReject(c: any) {
  rejecting.value = { ...c, poolDays: 7 }
  rejectReason.value = ''
  rejectNote.value = ''
}

async function doReject() {
  acting.value = true
  try {
    await candApi.review(rejecting.value.id, {
      action: '否决', reason: rejectReason.value, note: rejectNote.value,
    })
    toast.ok(`已否决「${rejecting.value.name}」，转入待定池保留 7 天`)
    rejecting.value = null
    await load()
  } catch (e) {
    toast.err(e)
  } finally {
    acting.value = false
  }
}

function openRescue(c: any) {
  rescuing.value = c
  rescueReason.value = ''
}

async function doRescue() {
  acting.value = true
  try {
    const r = await candApi.rescue(rescuing.value.id, rescueReason.value)
    toast.ok(r.message)
    rescuing.value = null
    await load()
  } catch (e) {
    toast.err(e)
  } finally {
    acting.value = false
  }
}

function onImported() {
  showImport.value = false
  load()
}

onMounted(async () => {
  try { positions.value = await modelApi.positions() } catch { /* ignore */ }
  await load()
})
</script>

<style scoped>
.tab-count {
  display: inline-block; margin-left: 5px; font-size: 10.5px;
  padding: 0 5px; border-radius: 8px; background: var(--bg-3); color: var(--text-1);
}
.seg-item.active .tab-count { background: var(--brand); color: #fff; }
.radio-row {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 9px; border-radius: var(--radius);
  border: 1px solid var(--border); cursor: pointer; font-size: 13px;
}
.radio-row:hover { background: var(--bg-2); }
.radio-row input { accent-color: var(--brand); }
</style>
