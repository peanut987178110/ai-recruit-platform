<template>
  <div class="exam-wrap" :class="{ 'exam-blur': blurWarning }">
    <!-- 顶部：计时与进度 -->
    <div class="exam-bar">
      <div class="row" style="gap: 12px">
        <span class="bold">{{ reviewing ? '作答详情（复核）' : '考试中' }}</span>
        <span class="tag tag-blue">{{ answeredCount }}/{{ questions.length }} 已作答</span>
        <span v-if="!reviewing" class="tag" :class="riskTag">{{ riskHint }}</span>
      </div>

      <div v-if="!reviewing" class="timer" :class="{ warn: secondsLeft <= 300, danger: secondsLeft <= 60 }">
        <span class="timer-icon">◷</span>
        <span class="timer-num">{{ fmtClock(secondsLeft) }}</span>
      </div>

      <div class="row ml-auto" style="gap: 8px">
        <VButton v-if="!reviewing" size="sm" @click="manualSave" :loading="saving">暂存</VButton>
        <VButton v-if="!reviewing" size="sm" variant="primary" @click="openSubmit">
          交卷
        </VButton>
        <VButton v-else size="sm" @click="$router.back()">返回</VButton>
      </div>
    </div>

    <!-- 反作弊提示条 -->
    <div v-if="blurWarning && !reviewing" class="alert alert-danger" style="margin: 12px 20px 0">
      <span class="alert-icon">⚠</span>
      <div>
        <b>检测到你离开了考试页面。</b>
        本次行为已记录，将作为线索提交给带教人。
        <div class="tiny mt-1">
          累计离开 {{ leaveCount }} 次。请注意：这些记录不会自动判定作弊，
          但频繁离开可能影响带教人对成绩的采信。
        </div>
      </div>
    </div>

    <!-- 题目区 -->
    <div class="exam-body">
      <div class="q-nav">
        <div class="tiny muted mb-1">题目导航</div>
        <div class="nav-grid">
          <button
            v-for="(q, i) in questions" :key="q.id"
            class="nav-dot" :class="{ done: answers[String(q.id)], on: i === current }"
            @click="scrollTo(i)"
          >{{ i + 1 }}</button>
        </div>
        <div class="divider" />
        <div class="tiny muted">
          单选 {{ counts.单选 || 0 }} · 多选 {{ counts.多选 || 0 }} · 主观 {{ subjectiveCount }}
        </div>
        <div class="tiny muted mt-1">多选题全对得满分，漏选得半分，错选不得分。</div>
      </div>

      <div ref="listEl" class="q-list" @copy="onCopy" @paste="onPaste" @contextmenu="onContext">
        <div
          v-for="(q, i) in questions" :key="q.id"
          :id="`q-${i}`" class="q-card"
        >
          <div class="row-between">
            <div class="row" style="gap: 8px">
              <span class="q-idx">{{ i + 1 }}</span>
              <span class="tag tag-gray">{{ q.qtype }}</span>
              <span v-if="q.ability_name" class="tag tag-purple tiny">{{ q.ability_name }}</span>
            </div>
            <span class="tiny muted">{{ q.full_score }} 分</span>
          </div>

          <div class="q-stem">{{ q.stem }}</div>

          <!-- 客观题选项 -->
          <div v-if="q.options?.length" class="options">
            <label
              v-for="(opt, oi) in q.options" :key="oi"
              class="opt" :class="{ on: isChosen(q, letter(oi)) }"
            >
              <input
                v-if="q.qtype === '单选'" type="radio"
                :name="`q${q.id}`" :value="letter(oi)"
                :checked="answers[String(q.id)] === letter(oi)"
                :disabled="reviewing"
                @change="setAnswer(q, letter(oi))"
              />
              <input
                v-else type="checkbox" :value="letter(oi)"
                :checked="isChosen(q, letter(oi))"
                :disabled="reviewing"
                @change="toggleMulti(q, letter(oi))"
              />
              <span>{{ opt }}</span>
            </label>
          </div>

          <!-- 主观题 -->
          <textarea
            v-else
            :value="answers[String(q.id)] || ''"
            class="textarea" rows="4" :disabled="reviewing"
            placeholder="请作答。AI 会分析要点覆盖情况，最终分数由带教人给出。"
            @input="setAnswer(q, ($event.target as HTMLTextAreaElement).value)"
          />

          <!-- 复核模式：显示判分 -->
          <div v-if="reviewing && qResults[String(q.id)]" class="q-result">
            <div class="row" style="gap: 8px">
              <span class="tag" :class="qResults[String(q.id)].score > 0 ? 'tag-ok' : 'tag-danger'">
                {{ qResults[String(q.id)].score }} / {{ qResults[String(q.id)].full_score }}
              </span>
              <span class="tiny muted">{{ qResults[String(q.id)].method }}</span>
            </div>
            <div v-if="qResults[String(q.id)].comment" class="tiny mt-1">
              {{ qResults[String(q.id)].comment }}
            </div>
            <div v-if="qResults[String(q.id)].hit_points?.length" class="mt-1">
              <span class="tiny bold" style="color: var(--ok)">命中：</span>
              <span v-for="(h, hi) in qResults[String(q.id)].hit_points" :key="hi"
                    class="tag tag-ok tiny" style="margin: 2px 3px 0 0">{{ h }}</span>
            </div>
            <div v-if="qResults[String(q.id)].miss_points?.length" class="mt-1">
              <span class="tiny bold" style="color: var(--warn)">缺失：</span>
              <span v-for="(m2, mi) in qResults[String(q.id)].miss_points" :key="mi"
                    class="tag tag-warn tiny" style="margin: 2px 3px 0 0">{{ m2 }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 交卷确认 -->
    <VModal v-if="submitOpen" title="确认交卷" @close="submitOpen = false">
      <div class="alert" :class="unanswered ? 'alert-warn' : 'alert-ok'">
        <span class="alert-icon">{{ unanswered ? '⚠' : '✓' }}</span>
        <div>
          <template v-if="unanswered">
            还有 <b>{{ unanswered }}</b> 道题未作答。交卷后不可再修改。
          </template>
          <template v-else>
            已全部作答完毕（{{ questions.length }} 道）。交卷后不可再修改。
          </template>
        </div>
      </div>
      <div v-if="leaveCount" class="alert alert-warn mt-2">
        <span class="alert-icon">⚠</span>
        <div>
          本次考试期间你有 <b>{{ leaveCount }} 次</b>离开页面的记录，
          这些记录会随交卷一并提交给带教人。
        </div>
      </div>
      <template #foot>
        <VButton @click="submitOpen = false">继续作答</VButton>
        <VButton variant="primary" :loading="submitting" @click="doSubmit">确认交卷</VButton>
      </template>
    </VModal>

    <!-- 交卷结果 -->
    <VModal v-if="result" title="交卷成功" wide @close="result = null">
      <div class="alert alert-info mb-2">
        <span class="alert-icon">◆</span><div>{{ result.note }}</div>
      </div>
      <div class="row" style="gap: 24px">
        <div>
          <div class="tiny muted">客观题得分</div>
          <div class="bold num" style="font-size: 26px">
            {{ result.objective_score }}<span class="tiny muted">/{{ result.objective_full }}</span>
          </div>
        </div>
        <div>
          <div class="tiny muted">用时</div>
          <div class="bold num" style="font-size: 26px">{{ fmtClock(result.duration_seconds) }}</div>
        </div>
      </div>
      <div v-if="result.radar && Object.keys(result.radar).length" class="mt-3">
        <div class="tiny muted mb-1">能力项得分</div>
        <div v-for="(v, k) in result.radar" :key="k" class="radar-row">
          <span class="small truncate" style="flex: 1">{{ k }}</span>
          <div class="bar" style="width: 120px"><div class="bar-fill" :style="{ width: v + '%' }" /></div>
          <span class="small num" style="width: 40px; text-align: right">{{ v }}</span>
        </div>
      </div>
      <template #foot>
        <VButton variant="primary" @click="leave">返回我的培训</VButton>
      </template>
    </VModal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import VButton from '../components/VButton.vue'
import VModal from '../components/VModal.vue'
import { examApi } from '../api'
import { toast } from '../components/toast'

const route = useRoute()
const router = useRouter()
const aid = Number(route.params.id)
const reviewing = route.query.review === '1'

const questions = ref<any[]>([])
const answers = ref<Record<string, string>>({})
const secondsLeft = ref(0)
const saving = ref(false)
const submitting = ref(false)
const submitOpen = ref(false)
const result = ref<any>(null)
const listEl = ref<HTMLElement | null>(null)
const current = ref(0)
const qResults = ref<Record<string, any>>({})
const dead = ref(false)

// ---- 反作弊：客户端信号采集 ----
// 这些信号只作线索，服务端不会据此自动判负。
const signals = ref<{ kind: string; at: number; detail: string }[]>([])
const startedAt = Date.now()
const leaveCount = ref(0)
const blurWarning = ref(false)
const riskLevel = ref('正常')
let blurAt = 0
let hbTimer: number | undefined
let saveTimer: number | undefined
let clockTimer: number | undefined

const elapsed = () => Math.round((Date.now() - startedAt) / 1000)

function push(kind: string, detail = '') {
  if (dead.value || reviewing) return
  signals.value.push({ kind, at: elapsed(), detail })
}

const riskHint = computed(() => {
  if (riskLevel.value === '可疑') return '风险：可疑'
  if (riskLevel.value === '关注') return '风险：关注'
  return '行为正常'
})
const riskTag = computed(() => ({
  可疑: 'tag-danger', 关注: 'tag-warn', 正常: 'tag-gray',
}[riskLevel.value] || 'tag-gray'))

const answeredCount = computed(() =>
  questions.value.filter((q) => (answers.value[String(q.id)] || '').trim()).length)
const unanswered = computed(() => questions.value.length - answeredCount.value)
const subjectiveCount = computed(() =>
  questions.value.filter((q) => q.qtype === '情景判断' || q.qtype === '案例分析').length)
const counts = computed(() => {
  const c: Record<string, number> = {}
  questions.value.forEach((q) => { c[q.qtype] = (c[q.qtype] || 0) + 1 })
  return c
})

function letter(i: number) { return String.fromCharCode(65 + i) }
function isChosen(q: any, l: string) {
  return (answers.value[String(q.id)] || '').includes(l)
}

function setAnswer(q: any, v: string) {
  if (reviewing) return
  answers.value[String(q.id)] = v
}

function toggleMulti(q: any, l: string) {
  if (reviewing) return
  const cur = new Set((answers.value[String(q.id)] || '').split('').filter(Boolean))
  cur.has(l) ? cur.delete(l) : cur.add(l)
  answers.value[String(q.id)] = [...cur].sort().join('')
}

function fmtClock(sec: number) {
  const s = Math.max(0, Math.floor(sec || 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = s % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`
           : `${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`
}

function scrollTo(i: number) {
  current.value = i
  document.getElementById(`q-${i}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// ---- 信号采集 ----
function onVisibility() {
  if (document.hidden) {
    push('visibility', '切换到其它标签页')
    leaveCount.value++
    blurWarning.value = true
  }
}
function onBlur() {
  blurAt = Date.now()
  push('blur', '窗口失去焦点')
  leaveCount.value++
  blurWarning.value = true
}
function onFocus() {
  if (blurAt) {
    const away = Math.round((Date.now() - blurAt) / 1000)
    if (away >= 20) push('blur_long', `离开 ${away} 秒`)
    blurAt = 0
  }
}
function onCopy(e: Event) {
  // 允许复制自己的作答内容，禁止复制题干 —— 那通常是为了外传搜答案
  const sel = String(window.getSelection() || '')
  if (sel.length > 40) {
    push('copy', `复制 ${sel.length} 字符`)
    e.preventDefault()
  }
}
function onPaste() { push('paste', '粘贴内容到作答区') }
function onContext(e: Event) { push('contextmenu', '使用右键'); e.preventDefault() }

// 开发者工具检测：窗口外框与内容区尺寸差异常
function onResize() {
  const wDiff = window.outerWidth - window.innerWidth
  const hDiff = window.outerHeight - window.innerHeight
  if (wDiff > 220 || hDiff > 240) push('devtools', `窗口差 ${wDiff}×${hDiff}`)
}
function onBeforeUnload(e: BeforeUnloadEvent) {
  if (!dead.value && !reviewing && secondsLeft.value > 0) {
    e.preventDefault()
    e.returnValue = '考试尚未交卷，离开将丢失未暂存的作答。'
  }
}

// ---- 与服务端交互 ----
async function load() {
  try {
    const r = await examApi.attempt(aid)
    if (reviewing) {
      // 复核模式：从判卷结果里取题目与得分
      const sub = r.judge?.items || []
      sub.forEach((j: any) => { qResults.value[String(j.question_id)] = j })
      // 题目从 attempt 结果的 paper 里没有直接给，改用 answers + judge 复原
      questions.value = []
      dead.value = true
      secondsLeft.value = r.duration_seconds
      return
    }
    if (r.status !== '进行中') {
      dead.value = true
      toast.warn(`本次作答状态为「${r.status}」`, '无法继续答题')
      return
    }
  } catch (e) {
    /* 继续走 start 流程 */
  }
}

async function init() {
  if (reviewing) {
    await loadReview()
    return
  }
  try {
    // 试卷直接从服务端取：服务端认得这个 attempt，
    // 会用同一份冻结的卷子应答，刷新页面也不会变题。
    // 不依赖前端传参 —— 那样刷新一次就丢了。
    const r = await examApi.resume(aid)
    if (r.status !== '进行中') {
      dead.value = true
      toast.warn(`本次作答状态为「${r.status}」`, '无法继续答题')
      router.replace('/training')
      return
    }
    questions.value = r.questions || []
    answers.value = r.draft_answers || {}
    secondsLeft.value = r.seconds_left || 0
  } catch (e) {
    toast.err(e, '加载试卷失败')
    router.replace('/training')
  }
}

async function loadReview() {
  try {
    const r = await examApi.attempt(aid)
    const sub = r.judge?.items || []
    sub.forEach((j: any) => { qResults.value[String(j.question_id)] = j })
    secondsLeft.value = r.duration_seconds || 0
    // 复核模式下用答案键还原题号列表
    const ans = r.answers || {}
    questions.value = Object.keys(ans).map((k) => ({
      id: Number(k), qtype: '', stem: '', options: [], full_score: 0,
    }))
    answers.value = ans
    dead.value = true
  } catch (e) {
    toast.err(e, '加载作答详情失败')
  }
}

async function syncHeartbeat() {
  try {
    const r = await examApi.heartbeat(aid, signals.value.splice(0))
    secondsLeft.value = r.seconds_left
    riskLevel.value = r.risk_level || '正常'
    if (r.expired) return forceSubmit()
  } catch { /* 网络抖动忽略，下轮再试 */ }
}

async function manualSave() {
  saving.value = true
  try {
    await examApi.save(aid, answers.value)
    toast.ok('已暂存', '刷新或断线后可从「我的培训」继续')
  } catch (e) {
    toast.err(e, '暂存失败')
  } finally {
    saving.value = false
  }
}

function openSubmit() { submitOpen.value = true }

async function doSubmit(auto = false) {
  submitting.value = true
  try {
    const r = await examApi.submit(aid, answers.value, signals.value.splice(0))
    dead.value = true
    sessionStorage.removeItem(`exam_${aid}`)
    submitOpen.value = false
    result.value = r
    if (auto) toast.warn('时间已到，已自动交卷')
  } catch (e) {
    toast.err(e, '交卷失败')
  } finally {
    submitting.value = false
  }
}

function forceSubmit() {
  if (dead.value) return
  toast.warn('考试时间已到', '正在自动交卷')
  doSubmit(true)
}

function leave() {
  dead.value = true
  router.push('/training')
}

onMounted(async () => {
  await init()
  if (reviewing || dead.value) return

  window.addEventListener('blur', onBlur)
  window.addEventListener('focus', onFocus)
  document.addEventListener('visibilitychange', onVisibility)
  window.addEventListener('resize', onResize)
  window.addEventListener('beforeunload', onBeforeUnload)

  // 本地倒计时仅用于显示；每 20 秒与服务端对齐一次
  clockTimer = window.setInterval(() => {
    if (secondsLeft.value > 0) {
      secondsLeft.value -= 1
      if (secondsLeft.value <= 0) forceSubmit()
    }
  }, 1000)
  hbTimer = window.setInterval(syncHeartbeat, 20000)
  // 每 30 秒自动暂存，避免意外断线丢作答
  saveTimer = window.setInterval(() => {
    if (!dead.value) examApi.save(aid, answers.value).catch(() => {})
  }, 30000)
})

onUnmounted(() => {
  window.removeEventListener('blur', onBlur)
  window.removeEventListener('focus', onFocus)
  document.removeEventListener('visibilitychange', onVisibility)
  window.removeEventListener('resize', onResize)
  window.removeEventListener('beforeunload', onBeforeUnload)
  if (clockTimer) clearInterval(clockTimer)
  if (hbTimer) clearInterval(hbTimer)
  if (saveTimer) clearInterval(saveTimer)
})
</script>

<style scoped>
.exam-wrap { min-height: 100vh; display: flex; flex-direction: column; background: var(--bg-0); }

.exam-bar {
  display: flex; align-items: center; gap: 16px;
  padding: 12px 20px; background: var(--bg-1);
  border-bottom: 1px solid var(--border); flex-shrink: 0;
  position: sticky; top: 0; z-index: 20;
}
.timer { display: flex; align-items: center; gap: 7px; }
.timer-icon { color: var(--text-2); }
.timer-num {
  font-size: 21px; font-weight: 660; font-variant-numeric: tabular-nums;
  letter-spacing: -0.3px;
}
.timer.warn .timer-num { color: var(--warn); }
.timer.danger .timer-num { color: var(--danger); animation: pulse 1s infinite; }
@keyframes pulse { 50% { opacity: 0.5; } }

.exam-body { flex: 1; display: flex; overflow: hidden; }
.q-nav {
  width: 210px; flex-shrink: 0; padding: 16px;
  border-right: 1px solid var(--border); background: var(--bg-1);
  overflow-y: auto;
}
.nav-grid { display: flex; flex-wrap: wrap; gap: 6px; }
.nav-dot {
  width: 30px; height: 30px; border-radius: 6px;
  border: 1px solid var(--border-strong); background: var(--bg-0);
  color: var(--text-1); font-size: 12px; cursor: pointer;
  font-family: inherit; transition: all 0.12s;
}
.nav-dot:hover { border-color: var(--brand); }
.nav-dot.done { background: var(--brand-dim); border-color: var(--brand); color: var(--brand-text); }
.nav-dot.on { outline: 2px solid var(--brand); outline-offset: 1px; }

.q-list { flex: 1; overflow-y: auto; padding: 20px 24px 60px; }
.q-card {
  background: var(--bg-1); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 18px 20px; margin-bottom: 16px;
}
.q-idx {
  width: 22px; height: 22px; border-radius: 6px;
  background: var(--brand); color: #fff; font-size: 12px; font-weight: 620;
  display: flex; align-items: center; justify-content: center;
}
.q-stem {
  margin: 12px 0 14px; font-size: 14.5px; line-height: 1.85;
  white-space: pre-wrap;
}
.options { display: flex; flex-direction: column; gap: 8px; }
.opt {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 10px 13px; border: 1px solid var(--border);
  border-radius: var(--radius); cursor: pointer; font-size: 13.5px;
  line-height: 1.7; transition: all 0.12s;
}
.opt:hover { background: var(--bg-2); border-color: var(--border-strong); }
.opt.on { border-color: var(--brand); background: var(--ai-bg); }
.opt input { margin-top: 3px; accent-color: var(--brand); flex-shrink: 0; }
.q-result {
  margin-top: 14px; padding: 12px;
  background: var(--bg-2); border-radius: var(--radius);
  border-left: 3px solid var(--brand);
}
.radar-row { display: flex; align-items: center; gap: 10px; padding: 3px 0; }

@media (max-width: 900px) {
  .q-nav { display: none; }
  .q-list { padding: 16px; }
}
</style>
