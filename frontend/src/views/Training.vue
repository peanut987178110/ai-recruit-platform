<template>
  <div>
    <!-- 新人视角 -->
    <div v-if="isNewcomer">
      <div class="card">
        <div class="card-head">
          <span class="card-title">我的培训</span>
          <span class="card-desc">学习完成后参加考核，考核通过即完成入职培训</span>
          <VButton size="sm" icon="⟳" class="ml-auto" :loading="loading" @click="load">刷新</VButton>
        </div>

        <VState :items="mine" :loading="loading" title="还没有指派给你的培训" icon="◈"
                desc="带教人会为你指派培训方案。指派后这里会显示学习大纲与考试入口。">
          <div v-for="m in mine" :key="m.assignment_id" class="my-card">
            <div class="row-between">
              <div class="row" style="gap: 9px">
                <span class="bold" style="font-size: 14px">{{ m.title }}</span>
                <span class="tag" :class="statusTag(m.status)">{{ m.status }}</span>
                <span v-if="m.overdue" class="tag tag-danger">已逾期</span>
              </div>
              <span class="tiny muted">
                限时 {{ m.exam_minutes }} 分钟 · 及格 {{ m.pass_score }} 分 ·
                {{ m.used_attempts }}/{{ m.max_attempts }} 次机会
                <template v-if="m.due_at"> · 截止 {{ fmtDate(m.due_at) }}</template>
              </span>
            </div>

            <!-- 学习大纲 -->
            <div class="outline-box">
              <div class="tiny muted mb-1">学习大纲（{{ m.outline.length }} 章）</div>
              <div v-for="(o, i) in m.outline" :key="i" class="outline-row">
                <span class="outline-idx">{{ i + 1 }}</span>
                <span class="small bold">{{ o.chapter }}</span>
                <span class="tag tag-blue tiny">{{ o.hours }} 学时</span>
                <span class="tiny muted ml-auto truncate" style="max-width: 260px">
                  {{ o.material_ref || '—' }}
                </span>
              </div>
            </div>

            <!-- 上次成绩 -->
            <div v-if="m.last_score?.objective !== null && m.last_score?.objective !== undefined" class="score-box">
              <div class="row-between">
                <span class="tiny muted">上次成绩</span>
                <span class="tiny muted">
                  {{ m.last_score.confirmed ? '带教人已确认' : '待带教人终评' }}
                </span>
              </div>
              <div class="row mt-1" style="gap: 20px">
                <div>
                  <div class="tiny muted">客观题</div>
                  <div class="bold num" style="font-size: 17px">
                    {{ m.last_score.objective }}<span class="tiny muted">/{{ m.last_score.objective_full }}</span>
                  </div>
                </div>
                <div v-if="m.last_score.final !== null && m.last_score.final !== undefined">
                  <div class="tiny muted">终评</div>
                  <div class="bold num" style="font-size: 17px; color: var(--brand-text)">
                    {{ m.last_score.final }}
                  </div>
                </div>
                <div style="flex: 1">
                  <div class="tiny muted mb-1">能力雷达</div>
                  <div v-for="(v, k) in (m.last_score.radar || {})" :key="k" class="radar-row">
                    <span class="tiny truncate" style="flex: 1">{{ k }}</span>
                    <div class="bar" style="width: 60px"><div class="bar-fill" :style="{ width: v + '%' }" /></div>
                    <span class="tiny num" style="width: 28px; text-align: right">{{ v }}</span>
                  </div>
                </div>
              </div>
            </div>

            <div class="row mt-2" style="gap: 9px">
              <VButton v-if="m.ongoing_attempt_id" variant="primary" @click="resume(m)">
                继续作答（剩 {{ fmtLeft(m.seconds_left) }}）
              </VButton>
              <VButton v-else-if="m.can_attempt" variant="primary" @click="openStart(m)">
                {{ m.used_attempts ? '重新考试' : '开始考试' }}
              </VButton>
              <span v-else class="alert alert-info" style="flex: 1; margin: 0">
                <span class="alert-icon">ℹ</span>
                <div>已用完 {{ m.max_attempts }} 次机会。如确需重考，请联系带教人。</div>
              </span>
            </div>
          </div>
        </VState>
      </div>
    </div>

    <!-- 带教人视角 -->
    <div v-else>
      <div class="tabs">
        <button class="tab" :class="{ on: tab === 'plans' }" @click="tab = 'plans'">培训方案</button>
        <button class="tab" :class="{ on: tab === 'review' }" @click="tab = 'review'; loadReview()">
          复核队列
          <span v-if="review.counts?.total" class="tab-count">{{ review.counts.total }}</span>
        </button>
        <button class="tab" :class="{ on: tab === 'assign' }" @click="tab = 'assign'; loadAssign()">指派记录</button>
      </div>

      <!-- 方案列表 -->
      <div v-if="tab === 'plans'" class="card">
        <div class="card-head">
          <span class="card-title">培训方案</span>
          <span class="card-desc">上传公司资料后由 AI 生成大纲与题库</span>
          <VButton size="sm" variant="primary" class="ml-auto" icon="+" @click="createOpen = true">
            生成新方案
          </VButton>
          <VButton size="sm" icon="⟳" :loading="loading" @click="load">刷新</VButton>
        </div>
        <VState :items="rows" :loading="loading" title="还没有培训方案" icon="◈"
                desc="先用「资料库」上传公司文档，再生成方案 —— AI 会优先依据上传的资料出题。">
          <table class="table">
            <thead>
              <tr><th>方案</th><th style="width:110px">岗位</th><th style="width:70px">章节</th>
              <th style="width:70px">题目</th><th style="width:90px">状态</th><th style="width:150px">操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="p in rows" :key="p.id">
                <td class="bold clickable" @click="$router.push(`/training/${p.id}`)">{{ p.title }}</td>
                <td class="small">{{ p.position_name }}</td>
                <td class="num">{{ p.chapter_count }}</td>
                <td class="num">{{ p.question_count }}</td>
                <td><span class="tag" :class="p.status === '已发布' ? 'tag-ok' : 'tag-warn'">{{ p.status }}</span></td>
                <td>
                  <VButton size="sm" variant="primary" @click="$router.push(`/training/${p.id}`)">打开</VButton>
                </td>
              </tr>
            </tbody>
          </table>
        </VState>
      </div>

      <!-- 复核队列 -->
      <div v-if="tab === 'review'" class="card">
        <div class="card-head">
          <span class="card-title">考试复核队列</span>
          <span class="card-desc">风险信号只作线索，是否作废由你判断</span>
          <VButton size="sm" icon="⟳" class="ml-auto" :loading="reviewLoading" @click="loadReview">刷新</VButton>
        </div>

        <div class="grid grid-4 mb-3">
          <div class="stat">
            <div class="stat-label">待处理</div>
            <div class="stat-value sm">{{ review.counts?.total ?? 0 }}</div>
          </div>
          <div class="stat">
            <div class="stat-label">风险可疑</div>
            <div class="stat-value sm" :style="{ color: (review.counts?.suspect ?? 0) ? 'var(--danger)' : '' }">
              {{ review.counts?.suspect ?? 0 }}
            </div>
          </div>
          <div class="stat">
            <div class="stat-label">需关注</div>
            <div class="stat-value sm" :style="{ color: (review.counts?.watch ?? 0) ? 'var(--warn)' : '' }">
              {{ review.counts?.watch ?? 0 }}
            </div>
          </div>
          <div class="stat">
            <div class="stat-label">待终评</div>
            <div class="stat-value sm">{{ review.counts?.pending_final ?? 0 }}</div>
          </div>
        </div>

        <div class="alert alert-info mb-3">
          <span class="alert-icon">◆</span>
          <div>{{ review.note }}</div>
        </div>

        <VState :items="review.items || []" :loading="reviewLoading" title="复核队列为空" icon="✓"
                desc="没有需要人工处理的考试记录。">
          <div v-for="x in review.items" :key="x.attempt_id" class="review-card">
            <div class="row-between">
              <div class="row" style="gap: 9px">
                <span class="bold">{{ x.name }}</span>
                <span class="mono tiny muted">{{ x.userid }}</span>
                <span class="tag" :class="riskTag(x.risk_level)">风险：{{ x.risk_level }}</span>
                <span v-if="x.mentor_confirmed" class="tag tag-ok">已终评</span>
                <span v-else class="tag tag-warn">待终评</span>
              </div>
              <span class="tiny muted">{{ fmtTime(x.submitted_at) }}</span>
            </div>

            <div class="row mt-2" style="gap: 22px">
              <div>
                <div class="tiny muted">客观题</div>
                <div class="bold num" style="font-size: 16px">
                  {{ x.objective_score }}<span class="tiny muted">/{{ x.objective_full }}</span>
                </div>
              </div>
              <div>
                <div class="tiny muted">用时</div>
                <div class="bold num" style="font-size: 16px">{{ fmtLeft(x.duration_seconds) }}</div>
              </div>
              <div>
                <div class="tiny muted">风险分</div>
                <div class="bold num" style="font-size: 16px"
                     :style="{ color: x.risk_level === '可疑' ? 'var(--danger)' : x.risk_level === '关注' ? 'var(--warn)' : '' }">
                  {{ x.risk_score }}
                </div>
              </div>
            </div>

            <div v-if="x.signals?.length" class="signals">
              <div class="tiny muted mb-1">行为信号（线索，非判据）</div>
              <div class="row wrap" style="gap: 6px">
                <span v-for="s in x.signals" :key="s.kind" class="tag tag-warn tiny">
                  {{ s.label }} × {{ s.count }}
                </span>
              </div>
            </div>

            <div class="row mt-2" style="gap: 8px">
              <input v-model.number="finalScore[x.attempt_id]" type="number" min="0" max="100"
                     class="input" style="width: 120px" placeholder="终评分数" />
              <VButton size="sm" variant="primary" @click="doConfirm(x)">确认终评</VButton>
              <VButton size="sm" variant="danger" @click="openVoid(x)">作废重考</VButton>
              <VButton size="sm" variant="ghost" @click="viewDetail(x)">查看作答</VButton>
            </div>
          </div>
        </VState>
      </div>

      <!-- 指派 -->
      <div v-if="tab === 'assign'" class="card">
        <div class="card-head">
          <span class="card-title">指派记录</span>
          <VButton size="sm" icon="⟳" class="ml-auto" :loading="assignLoading" @click="loadAssign">刷新</VButton>
        </div>
        <VState :items="assignments" :loading="assignLoading" title="还没有指派记录" icon="◉"
                desc="在培训方案详情页可以把方案指派给新人。">
          <table class="table">
            <thead>
              <tr><th>新人</th><th>培训方案</th><th style="width:90px">状态</th>
              <th style="width:80px">已用/上限</th><th style="width:110px">截止</th><th style="width:80px"></th></tr>
            </thead>
            <tbody>
              <tr v-for="a in assignments" :key="a.id">
                <td><span class="bold">{{ a.name }}</span>
                    <span class="mono tiny muted"> {{ a.userid }}</span></td>
                <td class="small">{{ a.plan_title }}</td>
                <td><span class="tag" :class="statusTag(a.status)">{{ a.status }}</span></td>
                <td class="num">{{ a.used_attempts }}/{{ a.max_attempts }}</td>
                <td class="tiny muted">{{ a.due_at ? fmtDate(a.due_at) : '—' }}</td>
                <td>
                  <VButton size="sm" variant="ghost" @click="removeAssignment(a)">撤销</VButton>
                </td>
              </tr>
            </tbody>
          </table>
        </VState>
      </div>
    </div>

    <!-- 开考确认 -->
    <VModal v-if="starting" title="开始考试" @close="starting = null">
      <div class="alert alert-warn mb-2">
        <span class="alert-icon">⚠</span>
        <div>
          <b>开始后计时立即启动，中途退出计时不会暂停。</b>
          <div class="mt-1">本次考试限时 <b>{{ starting.exam_minutes }} 分钟</b>，及格分 <b>{{ starting.pass_score }}</b> 分。</div>
        </div>
      </div>
      <div v-if="starting.description" class="alert alert-info mb-2">
        <span class="alert-icon">◆</span><div>{{ starting.description }}</div>
      </div>
      <div class="alert alert-info">
        <span class="alert-icon">◆</span>
        <div>
          考试期间的行为会被记录：切换窗口、粘贴、打开开发者工具等。
          这些记录<b>仅作为线索提交给带教人</b>，不直接判定作弊 ——
          系统无法分辨你是切屏查资料还是被系统弹窗打断，最终判断由人做出。
        </div>
      </div>
      <template #foot>
        <VButton @click="starting = null">取消</VButton>
        <VButton variant="primary" :loading="acting" @click="doStart">开始作答</VButton>
      </template>
    </VModal>

    <!-- 作废确认 -->
    <VModal v-if="voiding" title="作废本次作答" @close="voiding = null">
      <div class="alert alert-warn mb-2">
        <span class="alert-icon">⚠</span>
        <div>
          将作废 <b>{{ voiding.name }}</b> 的这次作答，并<b>返还一次重考机会</b>。
          作废不影响其重新作答的权利。
        </div>
      </div>
      <div class="field">
        <label class="field-label">作废理由（会写入决策日志）</label>
        <textarea v-model="voidNote" class="textarea" rows="3"
                  placeholder="例如：考试期间 4 次长时间离开页面，且作答内容与内部资料高度雷同" />
      </div>
      <template #foot>
        <VButton @click="voiding = null">取消</VButton>
        <VButton variant="danger" :disabled="!voidNote.trim()" :loading="acting" @click="doVoid">
          确认作废
        </VButton>
      </template>
    </VModal>

    <!-- 生成方案 -->
    <VModal v-if="createOpen" title="生成培训方案" wide @close="createOpen = false">
      <div class="grid grid-2">
        <div class="field">
          <label class="field-label">方案名称<span class="req">*</span></label>
          <input v-model.trim="form.title" class="input" maxlength="60"
                 placeholder="例如：后端开发新人培训（2026 秋）" />
          <div class="field-hint">不填则自动用「岗位名 + 新人培训方案」</div>
        </div>
        <div class="field">
          <label class="field-label">目标岗位<span class="req">*</span></label>
          <select v-model.number="form.position_id" class="select">
            <option :value="0">请选择岗位</option>
            <option v-for="p in positions" :key="p.id" :value="p.id">
              {{ p.name }}（{{ p.business_line }}）
            </option>
          </select>
          <div class="field-hint">大纲与题库按该岗位的能力项生成</div>
        </div>
      </div>

      <div class="field">
        <label class="field-label">方案说明</label>
        <input v-model.trim="form.description" class="input" maxlength="200"
               placeholder="选填，例如：面向交易研发部，含支付链路规范" />
      </div>

      <div class="grid grid-3">
        <div class="field">
          <label class="field-label">培训开始</label>
          <input v-model="form.start_date" type="date" class="input" />
        </div>
        <div class="field">
          <label class="field-label">培训结束</label>
          <input v-model="form.end_date" type="date" class="input" />
          <div class="field-hint">到期后新人无法再开考</div>
        </div>
        <div class="field">
          <label class="field-label">考试限时（分钟）</label>
          <input v-model.number="form.exam_minutes" type="number" min="10" max="180" class="input" />
          <div class="field-hint">由方案固定，考生不能自行修改</div>
        </div>
      </div>

      <div class="field">
        <label class="field-label">及格分</label>
        <input v-model.number="form.pass_score" type="number" min="0" max="100"
               class="input" style="max-width: 160px" />
        <div class="field-hint">带教人终评达到该分数即记为通过</div>
      </div>

      <!-- 资料上传 -->
      <div class="divider" />
      <div class="row-between mb-2">
        <div>
          <div class="bold small">公司资料</div>
          <div class="tiny muted">AI 优先依据这些资料出题，比通用知识库更贴合实际</div>
        </div>
        <VButton size="sm" :loading="matUploading" @click="pickMaterial">选择文件上传</VButton>
      </div>
      <input ref="matInput" type="file" hidden multiple
             accept=".pdf,.docx,.doc,.txt,.md,.csv,.xlsx" @change="onMaterialPick" />

      <div v-if="pendingMats.length" class="mat-list">
        <div v-for="(m, i) in pendingMats" :key="i" class="mat-item">
          <span class="mat-icon">{{ m.uploading ? '⏳' : m.error ? '✕' : '▤' }}</span>
          <div style="flex: 1; min-width: 0">
            <div class="row" style="gap: 7px">
              <span class="small truncate">{{ m.title }}</span>
              <span v-if="m.uploading" class="tag tag-gray tiny">上传中</span>
              <span v-else-if="m.error" class="tag tag-danger tiny">{{ m.error }}</span>
              <span v-else class="tag tag-ok tiny">{{ m.char_count }} 字</span>
            </div>
            <div v-if="m.warning" class="tiny" style="color: var(--warn)">{{ m.warning }}</div>
          </div>
          <button class="mat-x" type="button" @click="pendingMats.splice(i, 1)">✕</button>
        </div>
      </div>
      <div v-else class="mat-empty">
        尚未上传资料 — 也可以稍后在方案详情页追加
      </div>

      <div class="field mt-2">
        <label class="field-label row" style="gap: 8px">
          <input type="checkbox" v-model="form.with_exam" style="accent-color: var(--brand)" />
          <span>同时生成考核题库</span>
        </label>
      </div>

      <div v-if="creating" class="alert alert-info">
        <span class="alert-icon">◆</span>
        <div>
          <div>{{ createStep }}</div>
          <div class="bar mt-1" style="max-width: 320px">
            <div class="bar-fill" :style="{ width: createProgress + '%', transition: 'width 0.6s' }" />
          </div>
          <div class="tiny muted mt-1">AI 生成通常需要 40 至 90 秒，请勿关闭页面</div>
        </div>
      </div>

      <template #foot>
        <VButton :disabled="creating" @click="createOpen = false">取消</VButton>
        <VButton variant="primary" :disabled="!form.position_id || creating"
                 :loading="creating" @click="doCreate">
          {{ creating ? '生成中…' : '生成方案' }}
        </VButton>
      </template>
    </VModal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import VButton from '../components/VButton.vue'
import VModal from '../components/VModal.vue'
import VState from '../components/VState.vue'
import { examApi, modelApi, planApi } from '../api'
import { fmtDate, fmtTime } from '../api/labels'
import { toast } from '../components/toast'

const props = defineProps<{ user?: any }>()
const router = useRouter()

const isNewcomer = computed(() => props.user?.role === '新人')

const loading = ref(false)
const rows = ref<any[]>([])
const mine = ref<any[]>([])
const positions = ref<any[]>([])
const materials = ref<any[]>([])

const tab = ref('plans')
const review = ref<any>({})
const reviewLoading = ref(false)
const assignments = ref<any[]>([])
const assignLoading = ref(false)

const createOpen = ref(false)
const starting = ref<any>(null)
const startMinutes = ref(60)
const questionCount = ref(0)
const voiding = ref<any>(null)
const voidNote = ref('')
const acting = ref(false)
const finalScore = ref<Record<number, number>>({})

const form = ref({
  title: '', position_id: 0, description: '',
  start_date: '', end_date: '', exam_minutes: 60, pass_score: 60,
  with_exam: true,
})

function statusTag(s: string) {
  return { 待开始: 'tag-blue', 进行中: 'tag-warn', 已完成: 'tag-gray',
           已通过: 'tag-ok', 已过期: 'tag-danger' }[s] || 'tag-gray'
}
function riskTag(r: string) {
  return { 正常: 'tag-ok', 关注: 'tag-warn', 可疑: 'tag-danger' }[r] || 'tag-gray'
}
function fmtLeft(sec: number | null) {
  if (sec === null || sec === undefined) return '—'
  const s = Math.max(0, sec)
  const m = Math.floor(s / 60)
  return `${m} 分 ${s % 60} 秒`
}

async function load() {
  // user 由父组件传入，首次渲染时可能还没就绪。
  // 此时 isNewcomer 会误判为 false，进而去调带教人才有权限的接口、
  // 弹出一个莫名其妙的 403。等拿到角色再加载。
  if (!props.user?.role) {
    loading.value = false
    return
  }
  loading.value = true
  try {
    if (isNewcomer.value) {
      const d = await examApi.my()
      mine.value = d.items || []
    } else {
      rows.value = await examApi.plans()
      try { positions.value = await modelApi.positions() } catch { /* 无权则忽略 */ }
      try { materials.value = await examApi.materials() } catch { /* 无权则忽略 */ }
    }
  } catch (e) {
    toast.err(e, '加载失败')
  } finally {
    loading.value = false
  }
}

async function loadReview() {
  reviewLoading.value = true
  try { review.value = await examApi.reviewQueue() } catch (e) { toast.err(e) } finally { reviewLoading.value = false }
}

async function loadAssign() {
  assignLoading.value = true
  try { assignments.value = await examApi.assignments() } catch (e) { toast.err(e) } finally { assignLoading.value = false }
}

function openStart(m: any) {
  // 只打开确认框，不在这里调接口 —— 之前一打开就把计时启动了，
  // 用户还在犹豫时考试已经开始走表。
  starting.value = m
}

async function doStart() {
  acting.value = true
  try {
    // 时长由方案决定，不传 —— 服务端会按 plan.exam_minutes 计时
    const r = await examApi.start(starting.value.plan_id)
    toast.ok('考试已开始', `${r.questions.length} 道题，限时 ${r.seconds_left / 60 | 0} 分钟`)
    // 考试页会自己从服务端取同一份试卷，这里不需要传参
    router.push(`/exam/${r.attempt_id}`)
  } catch (e) {
    toast.err(e, '无法开始考试')
  } finally {
    acting.value = false
  }
}

function resume(m: any) {
  router.push(`/exam/${m.ongoing_attempt_id}`)
}

const matInput = ref<HTMLInputElement | null>(null)
const matUploading = ref(false)
const pendingMats = ref<any[]>([])
const creating = ref(false)
const createStep = ref('')
const createProgress = ref(0)

function pickMaterial() { matInput.value?.click() }

async function onMaterialPick(e: Event) {
  const el = e.target as HTMLInputElement
  const files = Array.from(el.files || [])
  if (!files.length) return
  matUploading.value = true
  for (const f of files) {
    const row: any = { title: f.name, uploading: true, char_count: 0 }
    pendingMats.value.push(row)
    try {
      const fd = new FormData()
      fd.append('file', f)
      fd.append('title', f.name.replace(/\.[^.]+$/, ''))
      fd.append('plan_id', '0')
      const r = await planApi.uploadMaterial(fd)
      Object.assign(row, {
        uploading: false, id: r.id, title: r.title,
        char_count: r.char_count, warning: r.warning || '',
      })
    } catch (err: any) {
      Object.assign(row, { uploading: false, error: err?.friendly || '上传失败' })
    }
  }
  matUploading.value = false
  if (el) el.value = ''
}

function resetCreateForm() {
  form.value = {
    title: '', position_id: 0, description: '',
    start_date: '', end_date: '', exam_minutes: 60, pass_score: 60,
    with_exam: true,
  }
  pendingMats.value = []
}

async function doCreate() {
  creating.value = true
  createStep.value = '正在提交…'
  createProgress.value = 10
  const t1 = window.setTimeout(() => {
    createStep.value = '正在依据资料生成培训大纲…'
    createProgress.value = 45
  }, 1500)
  const t2 = window.setTimeout(() => {
    createStep.value = '正在生成考核题库与参考答案…'
    createProgress.value = 75
  }, 25000)

  try {
    const ids = pendingMats.value.filter((m: any) => m.id).map((m: any) => m.id)
    const r = await planApi.create({ ...form.value, material_ids: ids })
    for (const w of (r.warnings || [])) toast.warn(w)
    window.clearTimeout(t1); window.clearTimeout(t2)
    createStep.value = '完成'; createProgress.value = 100
    toast.ok(`已生成 ${r.outline.length} 章大纲`, '内容为草稿，请审核后发布再指派给新人')
    createOpen.value = false
    resetCreateForm()
    await load()
    router.push(`/training/${r.plan_id}`)
  } catch (e) {
    window.clearTimeout(t1); window.clearTimeout(t2)
    toast.err(e, '生成失败')
  } finally {
    creating.value = false
    createStep.value = ''
    createProgress.value = 0
  }
}

async function doConfirm(x: any) {
  const v = finalScore.value[x.attempt_id]
  if (v === undefined || v === null || v === '') { toast.warn('请填写终评分数'); return }
  if (!x.submission_id) { toast.warn('该记录还没有判卷结果，无法终评'); return }
  try {
    await examApi.confirm(x.submission_id, Number(v))
    toast.ok('终评已确认，成绩已回流至 M5')
    await loadReview()
  } catch (e) { toast.err(e) }
}

function openVoid(x: any) { voiding.value = x; voidNote.value = '' }

async function doVoid() {
  acting.value = true
  try {
    const r = await examApi.voidAttempt(voiding.value.attempt_id, voidNote.value)
    toast.ok(r.message)
    voiding.value = null
    await loadReview()
  } catch (e) {
    toast.err(e)
  } finally {
    acting.value = false
  }
}

function viewDetail(x: any) {
  router.push(`/exam/${x.attempt_id}?review=1`)
}

async function removeAssignment(a: any) {
  if (!confirm(`确定撤销对「${a.name}」的指派？`)) return
  try {
    await examApi.deleteAssignment(a.id)
    toast.ok('已撤销')
    await loadAssign()
  } catch (e) { toast.err(e) }
}

watch(() => props.user?.role, (r) => { if (r) load() }, { immediate: true })
</script>

<style scoped>
.tabs { display: flex; gap: 4px; margin-bottom: 14px; border-bottom: 1px solid var(--border); }
.tab {
  padding: 8px 15px; background: none; border: none; cursor: pointer;
  color: var(--text-1); font-size: 13px; font-family: inherit;
  border-bottom: 2px solid transparent; margin-bottom: -1px;
}
.tab:hover { color: var(--text-0); }
.tab.on { color: var(--brand-text); border-bottom-color: var(--brand); font-weight: 570; }
.tab-count {
  display: inline-block; margin-left: 5px; font-size: 10.5px;
  padding: 0 5px; border-radius: 8px; background: var(--warn); color: #fff;
}

.my-card {
  padding: 15px; border: 1px solid var(--border);
  border-radius: var(--radius-lg); margin-bottom: 13px; background: var(--bg-0);
}
.outline-box {
  margin-top: 12px; padding: 11px 12px;
  background: var(--bg-2); border-radius: var(--radius);
}
.outline-row {
  display: flex; align-items: center; gap: 9px;
  padding: 5px 0; border-bottom: 1px dashed var(--border);
}
.outline-row:last-child { border-bottom: none; }
.outline-idx {
  width: 18px; height: 18px; flex-shrink: 0; border-radius: 4px;
  background: var(--brand-dim); color: var(--brand-text);
  font-size: 11px; display: flex; align-items: center; justify-content: center;
}
.score-box {
  margin-top: 12px; padding: 11px 12px;
  background: var(--ai-bg); border-left: 3px solid var(--brand);
  border-radius: var(--radius);
}
.radar-row { display: flex; align-items: center; gap: 7px; padding: 2px 0; }

.review-card {
  padding: 13px; border: 1px solid var(--border);
  border-radius: var(--radius-lg); margin-bottom: 11px; background: var(--bg-0);
}
.signals {
  margin-top: 11px; padding: 9px 11px;
  background: var(--warn-bg); border-radius: var(--radius);
}

.mat-list { display: flex; flex-direction: column; gap: 6px; }
.mat-item {
  display: flex; gap: 9px; align-items: flex-start;
  padding: 8px 10px; border: 1px solid var(--border);
  border-radius: var(--radius); background: var(--bg-0);
}
.mat-icon { flex-shrink: 0; color: var(--text-2); margin-top: 1px; }
.mat-x {
  background: none; border: none; color: var(--text-2);
  cursor: pointer; font-size: 11px; padding: 2px 5px; font-family: inherit;
}
.mat-x:hover { color: var(--danger); }
.mat-empty {
  padding: 16px; text-align: center; font-size: 12.5px;
  color: var(--text-2); border: 1px dashed var(--border);
  border-radius: var(--radius);
}
</style>
