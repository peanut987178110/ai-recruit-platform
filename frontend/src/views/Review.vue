<template>
  <div class="review-wrap">
    <div v-if="loading" class="pad">
      <div class="skeleton" style="height: 60px" />
    </div>

    <template v-else-if="c">
      <!-- 顶栏 -->
      <div class="rev-head">
        <VButton variant="ghost" size="sm" @click="$router.push('/workbench')">← 返回队列</VButton>
        <div class="rev-name">
          <span class="bold" style="font-size: 15px">{{ c.name }}</span>
          <span class="muted small">· {{ c.position_name }}</span>
          <span class="tag" :class="STATUS_TAG[c.status] || 'tag-gray'">{{ c.status }}</span>
          <span v-if="c.tier" class="tag" :class="tierColor(c.tier)">{{ c.tier }}</span>
        </div>
        <div class="ml-auto row">
          <span class="tiny muted">键盘：J/K 切换 · A 采纳 · D 否决</span>
        </div>
      </div>

      <div v-if="injectionWarn" class="alert alert-danger" style="margin: 0 16px 12px">
        <span class="alert-icon">⚠</span>
        <div>
          <b>该简历含疑似指令文本，已剥离后正常解析。</b>
          系统识别到 {{ c.parse_meta?.injection_hits?.length || 0 }} 处疑似注入内容，
          原文已留档并标记待审，由管理员判断是格式问题还是恶意行为。
          这不会影响本页的评分结果，但请留意简历内容的真实性。
        </div>
      </div>

      <div v-if="degrade" class="alert alert-warn" style="margin: 0 16px 12px">
        <span class="alert-icon">⚠</span>
        <div>
          本次打分走了降级路径：{{ degrade }}。
          {{ c.score?.ai_meta?.degrade_reason || '' }}
        </div>
      </div>

      <!-- 左右分栏：左 60% 简历原文，右 40% 打分与证据 -->
      <div class="rev-body">
        <section class="rev-left">
          <div class="pane-head">
            <span class="pane-title">简历原文</span>
            <span v-if="!c.resume_visible" class="tag tag-danger">当前角色无权查看正文</span>
            <div class="ml-auto row">
              <input v-model="q" class="input" style="width: 160px; padding: 3px 9px; font-size: 12px" placeholder="关键词高亮" />
              <span v-if="q" class="tiny muted">{{ matchCount }} 处</span>
            </div>
          </div>
          <div ref="textEl" class="pane-body" v-html="highlighted" />

          <div v-if="lowConf.length" class="low-conf">
            <div class="row-between mb-1">
              <span class="tiny bold" style="color: var(--warn)">⚠ 低置信字段需人工确认后才可打分</span>
              <VButton size="sm" @click="confirmOpen = true">补充/确认</VButton>
            </div>
            <div class="row wrap">
              <span v-for="f in lowConf" :key="f" class="tag tag-warn">{{ f }}</span>
            </div>
          </div>
        </section>

        <section class="rev-right">
          <!-- 总分卡片 -->
          <div class="score-card" v-if="c.score">
            <div class="row-between">
              <div>
                <div class="tiny muted">匹配分</div>
                <div class="score-num" :style="{ color: scoreColor(c.total_score) }">
                  {{ num(c.total_score) }}<span class="score-max">/100</span>
                </div>
              </div>
              <div class="center">
                <div class="tiny muted">置信度</div>
                <div class="bold num">{{ num(c.confidence, 2) }}</div>
                <div class="tiny muted">仅用于分流，不展示给业务</div>
              </div>
            </div>
            <div v-if="c.score.summary" class="score-summary">{{ c.score.summary }}</div>

            <div v-if="c.score.veto_hit" class="alert alert-danger mt-2">
              <span class="alert-icon">✕</span>
              <div><b>触发否决项，总分归零。</b>{{ c.score.veto_reason }}</div>
            </div>

            <div v-if="c.risk_tags?.length" class="mt-2">
              <div class="tiny muted mb-1">风险提示（客观事实，非主观归因）</div>
              <div class="row wrap">
                <span v-for="t in c.risk_tags" :key="t" class="tag tag-warn">{{ t }}</span>
              </div>
            </div>

            <div v-if="c.score.ai_meta" class="model-line">
              <span class="tiny">模型 {{ c.score.ai_meta.model || '—' }}</span>
              <span class="tiny">提示词 {{ c.score.ai_meta.prompt_version || '—' }}</span>
              <span class="tiny">{{ c.score.ai_meta.latency_ms }} ms</span>
              <span class="tiny">¥{{ Number(c.score.ai_meta.cost_cny || 0).toFixed(4) }}</span>
            </div>
          </div>

          <!-- 能力项 -->
          <div class="pane-head">
            <span class="pane-title">能力项判断</span>
            <span class="tiny muted ml-auto">{{ abilities.length }} 项</span>
          </div>

          <div class="ability-list">
            <div
              v-for="a in abilities" :key="a.ability_id"
              class="ability" :class="{ active: activeAbility === a.ability_id }"
              @click="focusAbility(a)"
            >
              <div class="row-between">
                <span class="ability-name">{{ a.ability_name }}</span>
                <span class="tag" :class="stateColor(a.state)">{{ STATE_LABEL[a.state] }}</span>
              </div>
              <div class="row-between mt-1">
                <div class="row" style="gap: 8px">
                  <span class="tiny muted">权重 {{ a.weight * 10 }}%</span>
                  <span v-if="a.is_gate" class="tag tag-purple tiny">排除条款</span>
                  <span v-else-if="a.veto_polarity === 'negative'" class="tag tag-purple tiny">排除条款</span>
                </div>
                <span class="num bold" :style="{ color: a.score >= 7 ? 'var(--ok)' : a.score > 0 ? 'var(--warn)' : 'var(--text-2)' }">
                  {{ num(a.score) }}<span class="tiny muted">/10</span>
                </span>
              </div>
              <div v-if="a.reason" class="ability-reason">{{ a.reason }}</div>
              <div class="row mt-1">
                <span class="tiny" :style="{ color: 'var(--text-2)' }">{{ STATE_HINT[a.state] }}</span>
                <button v-if="a.evidence?.length" class="link-btn ml-auto" @click.stop="showEvidence(a)">
                  查看依据（{{ a.evidence.length }}）
                </button>
                <span v-else class="tiny muted ml-auto">无证据</span>
              </div>
            </div>
          </div>

          <!-- 操作区 -->
          <div class="action-bar">
            <div v-if="c.status === '待复核'" class="col" style="gap: 8px; width: 100%">
              <div class="row" style="gap: 8px">
                <VButton variant="ok" style="flex: 1" :loading="acting" @click="doAction('采纳')">
                  ✓ 采纳（转待安排面试）
                </VButton>
                <VButton variant="danger" style="flex: 1" @click="rejectOpen = true">
                  ✕ 否决
                </VButton>
              </div>
              <div class="row" style="gap: 8px">
                <VButton size="sm" style="flex: 1" @click="doubtOpen = true">标记疑问</VButton>
                <VButton size="sm" style="flex: 1" @click="showLog = true">决策日志</VButton>
              </div>
            </div>

            <div v-else-if="c.status === '待安排面试'" class="col" style="gap: 8px; width: 100%">
              <VButton variant="primary" style="width: 100%" @click="scheduleOpen = true">
                安排面试
              </VButton>
              <VButton size="sm" style="width: 100%" @click="doAction('退回复核')">退回复核</VButton>
            </div>

            <div v-else-if="c.status === '待定池'" class="col" style="gap: 8px; width: 100%">
              <div class="alert alert-info">
                <span class="alert-icon">◲</span>
                <div>待定池剩余 <b>{{ c.pool_days_left }}</b> 天（{{ fmtDate(c.pool_expire_at) }} 到期归档）</div>
              </div>
              <VButton variant="primary" style="width: 100%" @click="rescueOpen = true">捞回复核</VButton>
            </div>

            <div v-else-if="c.status === '解析异常'" class="col" style="gap: 8px; width: 100%">
              <div class="alert alert-warn">
                <span class="alert-icon">⚠</span>
                <div>{{ c.parse_error || '解析失败，可重新上传或人工补录' }}</div>
              </div>
              <VButton variant="primary" style="width: 100%" :loading="acting" @click="doReparse">
                重新解析
              </VButton>
            </div>

            <div v-else class="alert alert-info" style="width: 100%">
              <span class="alert-icon">ℹ</span>
              <div>当前状态「{{ c.status }}」无可执行动作{{ c.status === '已归档' ? '。归档不等于拒绝，也不触发对外通知。' : '' }}</div>
            </div>
          </div>
        </section>
      </div>
    </template>

    <VState v-else :items="[]" title="候选人不存在" icon="✕" />

    <!-- 证据抽屉：不跳转页面，保证复核动作不被打断（PRD 2.3） -->
    <VDrawer :visible="!!evidence" :title="`证据链 · ${evidence?.ability_name || ''}`"
             :subtitle="STATE_LABEL[evidence?.state || '']" @close="evidence = null">
      <template v-if="evidence">
        <div class="ai-block mb-3">
          <div class="ai-label">◆ AI 判定依据</div>
          <div>{{ evidence.reason || '（模型未给出说明）' }}</div>
          <div class="row mt-2" style="gap: 14px">
            <span class="tiny muted">状态：{{ STATE_LABEL[evidence.state] }}</span>
            <span class="tiny muted">得分：{{ num(evidence.score) }}/10</span>
            <span class="tiny muted">权重：{{ evidence.weight * 10 }}%</span>
          </div>
        </div>

        <div class="tiny muted mb-1">{{ STATE_HINT[evidence.state] }}</div>
        <div class="divider" />

        <div v-if="evidence.evidence?.length">
          <div class="bold small mb-2">简历原文证据（{{ evidence.evidence.length }} 条）</div>
          <div v-for="(e, i) in evidence.evidence" :key="i" class="ev-card">
            <div class="ev-quote">{{ e.quote }}</div>
            <div class="row-between mt-1">
              <span class="tiny muted">{{ e.field_source || '简历原文' }}</span>
              <button class="link-btn" @click="jumpTo(e)">定位到原文</button>
            </div>
          </div>
        </div>
        <div v-else class="alert alert-warn">
          <span class="alert-icon">⚠</span>
          <div>
            该能力项没有可定位的证据。
            <template v-if="evidence.state === 'absent'">
              这是「未体现」状态：简历里没提到这项能力，属信息缺失，应在面试中验证，
              不应视为能力不足。
            </template>
            <template v-else>
              按平台硬性约束，无证据不给分。若此项有得分，应人工复核。
            </template>
          </div>
        </div>
      </template>
    </VDrawer>

    <!-- 否决 -->
    <VModal v-if="rejectOpen" title="否决候选人" @close="rejectOpen = false">
      <div class="alert alert-warn mb-2">
        <span class="alert-icon">⚠</span>
        <div>将否决 <b>1</b> 名候选人「{{ c?.name }}」，转入待定池保留 {{ c?.pool_days_left ?? 7 }} 天。</div>
      </div>
      <div class="field">
        <label class="field-label">否决原因<span class="req">*</span></label>
        <div class="col" style="gap: 6px">
          <label v-for="r in REJECT_REASONS" :key="r" class="radio-row">
            <input v-model="reason" type="radio" :value="r" /><span>{{ r }}</span>
          </label>
        </div>
      </div>
      <div class="field">
        <label class="field-label">备注（可选）</label>
        <textarea v-model="note" class="textarea" rows="2" placeholder="将写入回流样本池" />
      </div>
      <template #foot>
        <VButton @click="rejectOpen = false">取消</VButton>
        <VButton variant="danger" :disabled="!reason" :loading="acting" @click="doReject">确认否决</VButton>
      </template>
    </VModal>

    <!-- 安排面试 -->
    <VModal v-if="scheduleOpen" title="安排面试" @close="scheduleOpen = false">
      <div class="field">
        <label class="field-label">面试轮次</label>
        <select v-model="sched.round_name" class="select">
          <option>初面</option><option>二面</option><option>终面</option>
        </select>
      </div>
      <div class="field">
        <label class="field-label">时长预算</label>
        <select v-model.number="sched.plan_minutes" class="select">
          <option :value="30">30 分钟（生成 8 至 10 题）</option>
          <option :value="60">60 分钟（生成 12 题以上）</option>
        </select>
        <div class="field-hint">题目会按时间预算自动取舍，优先保留覆盖能力缺口的题。</div>
      </div>
      <template #foot>
        <VButton @click="scheduleOpen = false">取消</VButton>
        <VButton variant="primary" :loading="acting" @click="doSchedule">安排</VButton>
      </template>
    </VModal>

    <!-- 标记疑问 -->
    <VModal v-if="doubtOpen" title="标记疑问" @close="doubtOpen = false">
      <div class="alert alert-info mb-2">
        <span class="alert-icon">ℹ</span>
        <div>标记疑问不会改变候选人状态，会加上疑问标签并可指派给用人经理协助判断。</div>
      </div>
      <div class="field">
        <label class="field-label">指派给（可选）</label>
        <select v-model.number="assigneeId" class="select">
          <option :value="0">不指派</option>
          <option v-for="u in managers" :key="u.id" :value="u.id">{{ u.name }}（{{ u.role }}）</option>
        </select>
      </div>
      <div class="field">
        <label class="field-label">疑问说明</label>
        <textarea v-model="note" class="textarea" rows="2" placeholder="例如：履历断档原因需确认" />
      </div>
      <template #foot>
        <VButton @click="doubtOpen = false">取消</VButton>
        <VButton variant="primary" :loading="acting" @click="doDoubt">提交</VButton>
      </template>
    </VModal>

    <!-- 捞回 -->
    <VModal v-if="rescueOpen" title="从待定池捞回" @close="rescueOpen = false">
      <div class="alert alert-info mb-2">
        <span class="alert-icon">ℹ</span>
        <div>捞回后转入复核队列，原分档会记为误杀线索用于每周复盘。</div>
      </div>
      <div class="field">
        <label class="field-label">捞回原因</label>
        <textarea v-model="reason" class="textarea" rows="2" placeholder="例如：业务方反馈候选人项目经历高度匹配" />
      </div>
      <template #foot>
        <VButton @click="rescueOpen = false">取消</VButton>
        <VButton variant="primary" :loading="acting" @click="doRescue">确认捞回</VButton>
      </template>
    </VModal>

    <!-- 低置信字段补录 -->
    <VModal v-if="confirmOpen" title="补充缺失字段" @close="confirmOpen = false">
      <div class="alert alert-warn mb-2">
        <span class="alert-icon">⚠</span>
        <div>补录界面只显示缺失字段，不要求重填全部内容。确认后将重新进入打分队列。</div>
      </div>
      <div v-for="f in lowConf" :key="f" class="field">
        <label class="field-label">{{ f }}</label>
        <input v-model="fields[f]" class="input" :placeholder="`请输入${f}`" />
      </div>
      <template #foot>
        <VButton @click="confirmOpen = false">取消</VButton>
        <VButton variant="primary" :loading="acting" @click="doConfirm">确认并重新打分</VButton>
      </template>
    </VModal>

    <!-- 决策日志 -->
    <VDrawer :visible="showLog" title="决策日志" subtitle="全链路可追溯" @close="showLog = false">
      <div class="tiny muted mb-2">日志保留期不短于 3 年，只追加不可编辑（PRD 5.5）。</div>
      <div v-for="(l, i) in (c?.decision_logs || [])" :key="i" class="log-row">
        <div class="row-between">
          <span class="tag" :class="l.kind === 'ai' ? 'tag-ai' : l.kind === 'injection' ? 'tag-danger' : 'tag-blue'">
            {{ LOG_KIND[l.kind] || l.kind }}
          </span>
          <span class="tiny muted">{{ fmtTime(l.at) }}</span>
        </div>
        <div class="small mt-1">{{ l.summary }}</div>
        <div v-if="l.model" class="tiny muted">模型 {{ l.model }} · 提示词 {{ l.prompt_version }}
          <span v-if="l.degrade && l.degrade !== 'none'" style="color: var(--warn)"> · 降级 {{ l.degrade }}</span>
        </div>
      </div>
    </VDrawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import VButton from '../components/VButton.vue'
import VDrawer from '../components/VDrawer.vue'
import VModal from '../components/VModal.vue'
import VState from '../components/VState.vue'
import { api, candApi, interviewApi } from '../api'
import {
  REJECT_REASONS, STATE_HINT, STATE_LABEL, STATUS_TAG, degradeText,
  fmtDate, fmtTime, num, scoreColor, stateColor, tierColor,
} from '../api/labels'
import { toast } from '../components/toast'

const route = useRoute()
const router = useRouter()

const c = ref<any>(null)
const loading = ref(true)
const acting = ref(false)
const q = ref('')
const textEl = ref<HTMLElement | null>(null)
const evidence = ref<any>(null)
const activeAbility = ref('')
const managers = ref<any[]>([])

const rejectOpen = ref(false)
const scheduleOpen = ref(false)
const doubtOpen = ref(false)
const rescueOpen = ref(false)
const confirmOpen = ref(false)
const showLog = ref(false)

const reason = ref('')
const note = ref('')
const assigneeId = ref(0)
const fields = ref<Record<string, string>>({})
const sched = ref({ round_name: '初面', plan_minutes: 30 })

const LOG_KIND: Record<string, string> = {
  ai: 'AI 决策', human: '人工操作', config: '配置变更',
  access: '权限访问', injection: '注入拦截',
}

const abilities = computed(() => {
  const list = c.value?.abilities || []
  return [...list].sort((a, b) => (b.weight || 0) - (a.weight || 0))
})

const lowConf = computed(() => c.value?.low_confidence_fields || [])
const injectionWarn = computed(() => c.value?.parse_meta?.injection_hits?.length > 0)
const degrade = computed(() => degradeText(c.value?.score?.ai_meta))

const matchCount = computed(() => {
  if (!q.value || !c.value?.resume_text) return 0
  const re = new RegExp(escapeRe(q.value), 'gi')
  return (c.value.resume_text.match(re) || []).length
})

const highlighted = computed(() => {
  const t = c.value?.resume_text || ''
  if (!t) return '<span class="muted">（无简历正文）</span>'
  const esc = t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const parts = esc.split('\n').map((line) => `<p class="rl">${line || '&nbsp;'}</p>`).join('')
  if (!q.value) return parts
  const re = new RegExp(`(${escapeRe(q.value.replace(/&/g, '&amp;').replace(/</g, '&lt;'))})`, 'gi')
  return parts.replace(re, '<mark>$1</mark>')
})

function escapeRe(s: string) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') }

async function load() {
  loading.value = true
  try {
    c.value = await candApi.get(Number(route.params.id))
  } catch (e) {
    toast.err(e, '加载候选人失败')
    c.value = null
  } finally {
    loading.value = false
  }
}

function focusAbility(a: any) {
  activeAbility.value = a.ability_id
  const e = a.evidence?.[0]
  if (e) jumpTo(e)
  else if (a.state === 'absent') {
    toast.info('该能力项在简历中未体现，无原文可定位', '这属于信息缺失，应在面试中验证，不应视为能力不足')
  }
}

/** 点击证据片段，左侧自动滚动到对应位置并高亮，保持 3 秒后渐隐 */
function jumpTo(e: any) {
  if (!textEl.value || e.start < 0) return
  const text = c.value.resume_text || ''
  const before = text.slice(0, e.start)
  const lineNo = before.split('\n').length
  const node = textEl.value.querySelectorAll('.rl')[lineNo - 1] as HTMLElement | null
  if (!node) return
  node.scrollIntoView({ behavior: 'smooth', block: 'center' })
  node.classList.add('hl')
  setTimeout(() => node.classList.remove('hl'), 3000)
}

async function showEvidence(a: any) {
  evidence.value = a
  // 记录证据点击埋点：用于验证「证据溯源是否真被使用」这个设计假设
  try { await candApi.evidence(Number(route.params.id), a.ability_id) } catch { /* ignore */ }
}

async function doAction(action: string) {
  acting.value = true
  try {
    const r = await candApi.review(Number(route.params.id), { action })
    toast.ok(r.message)
    await load()
  } catch (e) {
    toast.err(e)
  } finally {
    acting.value = false
  }
}

async function doReject() {
  if (!reason.value) return
  acting.value = true
  try {
    await candApi.review(Number(route.params.id), { action: '否决', reason: reason.value, note: note.value })
    toast.ok('已否决，候选人转入待定池；原因已写入回流样本池')
    rejectOpen.value = false
    await load()
  } catch (e) {
    toast.err(e)
  } finally {
    acting.value = false
  }
}

async function doDoubt() {
  acting.value = true
  try {
    await candApi.review(Number(route.params.id), {
      action: '标记疑问', note: note.value,
      assignee_id: assigneeId.value || undefined,
    })
    toast.ok('已标记疑问')
    doubtOpen.value = false
    await load()
  } catch (e) {
    toast.err(e)
  } finally {
    acting.value = false
  }
}

async function doRescue() {
  acting.value = true
  try {
    const r = await candApi.rescue(Number(route.params.id), reason.value)
    toast.ok(r.message)
    rescueOpen.value = false
    await load()
  } catch (e) {
    toast.err(e)
  } finally {
    acting.value = false
  }
}

async function doReparse() {
  acting.value = true
  try {
    const r = await candApi.reparse(Number(route.params.id))
    toast.ok(r.message)
    setTimeout(load, 3000)
  } catch (e) {
    toast.err(e)
  } finally {
    acting.value = false
  }
}

async function doConfirm() {
  acting.value = true
  try {
    const r = await candApi.confirmFields(Number(route.params.id), fields.value)
    toast.ok(r.message)
    confirmOpen.value = false
    setTimeout(load, 4000)
  } catch (e) {
    toast.err(e)
  } finally {
    acting.value = false
  }
}

async function doSchedule() {
  acting.value = true
  try {
    const r = await interviewApi.create({
      candidate_id: Number(route.params.id),
      round_name: sched.value.round_name,
      plan_minutes: sched.value.plan_minutes,
    })
    toast.ok('已安排面试，正在准备题目')
    scheduleOpen.value = false
    router.push(`/interview/${r.schedule_id}`)
  } catch (e) {
    toast.err(e)
  } finally {
    acting.value = false
  }
}

function onKey(e: KeyboardEvent) {
  const tag = (e.target as HTMLElement)?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
  if (e.key === 'a' || e.key === 'A') { if (c.value?.status === '待复核') doAction('采纳') }
  if (e.key === 'd' || e.key === 'D') { if (c.value?.status === '待复核') rejectOpen.value = true }
}

watch(() => route.params.id, load)
onMounted(async () => {
  await load()
  window.addEventListener('keydown', onKey)
  try {
    const us = await api.users()
    managers.value = us.filter((u) => ['用人经理', 'HR负责人'].includes(u.role))
  } catch { /* ignore */ }
})
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<style scoped>
.review-wrap { height: 100%; display: flex; flex-direction: column; }
.pad { padding: 20px; }
.rev-head {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 16px; border-bottom: 1px solid var(--border);
  background: var(--bg-1); flex-shrink: 0;
}
.rev-name { display: flex; align-items: center; gap: 8px; }

.rev-body { flex: 1; display: flex; overflow: hidden; }
.rev-left {
  width: 60%; display: flex; flex-direction: column;
  border-right: 1px solid var(--border); overflow: hidden;
}
.rev-right {
  width: 40%; display: flex; flex-direction: column;
  overflow-y: auto; background: var(--bg-1);
}
.pane-head {
  display: flex; align-items: center; gap: 9px;
  padding: 9px 15px; border-bottom: 1px solid var(--border);
  background: var(--bg-2); flex-shrink: 0;
}
.pane-title { font-size: 12.5px; font-weight: 620; }
.pane-body {
  flex: 1; overflow-y: auto; padding: 16px 20px;
  font-size: 13.5px; line-height: 1.95; white-space: pre-wrap;
  font-family: 'Microsoft YaHei', sans-serif;
}
.pane-body :deep(.rl) { margin: 0 0 2px; }
.pane-body :deep(.rl.hl) {
  background: var(--warn-bg); box-shadow: 0 0 0 3px var(--warn-bg);
  border-radius: 3px; transition: background 0.4s;
}
.pane-body :deep(mark) {
  background: var(--brand); color: #fff; border-radius: 2px; padding: 0 2px;
}

.low-conf {
  padding: 10px 15px; border-top: 1px solid var(--border);
  background: var(--warn-bg); flex-shrink: 0;
}

.score-card { padding: 15px; border-bottom: 1px solid var(--border); }
.score-num { font-size: 32px; font-weight: 700; line-height: 1.1; letter-spacing: -1px; }
.score-max { font-size: 14px; color: var(--text-2); font-weight: 400; margin-left: 2px; }
.score-summary {
  margin-top: 10px; padding: 9px 11px; background: var(--ai-bg);
  border-left: 3px solid var(--brand); border-radius: var(--radius);
  font-size: 12.5px; line-height: 1.7;
}
.model-line {
  display: flex; gap: 12px; flex-wrap: wrap; margin-top: 10px;
  padding-top: 9px; border-top: 1px dashed var(--border); color: var(--text-2);
}

.ability-list { padding: 10px; display: flex; flex-direction: column; gap: 8px; }
.ability {
  padding: 11px 12px; border: 1px solid var(--border);
  border-radius: var(--radius); cursor: pointer; transition: all 0.13s;
  background: var(--bg-0);
}
.ability:hover { border-color: var(--border-strong); background: var(--bg-2); }
.ability.active { border-color: var(--brand); background: var(--ai-bg); }
.ability-name { font-size: 13px; font-weight: 570; line-height: 1.5; }
.ability-reason { font-size: 12px; color: var(--text-1); margin-top: 5px; line-height: 1.65; }
.link-btn {
  background: none; border: none; color: var(--brand-text);
  font-size: 11.5px; cursor: pointer; padding: 0; font-family: inherit;
}
.link-btn:hover { text-decoration: underline; }

.action-bar {
  margin-top: auto; padding: 13px 15px;
  border-top: 1px solid var(--border); background: var(--bg-2);
  display: flex; gap: 8px; position: sticky; bottom: 0;
}

.ev-card {
  padding: 11px 12px; background: var(--bg-2);
  border: 1px solid var(--border); border-left: 3px solid var(--brand);
  border-radius: var(--radius); margin-bottom: 9px;
}
.ev-quote { font-size: 12.5px; line-height: 1.75; color: var(--text-0); }
.log-row {
  padding: 10px 0; border-bottom: 1px solid var(--border);
}
.radio-row {
  display: flex; align-items: center; gap: 8px; padding: 6px 9px;
  border-radius: var(--radius); border: 1px solid var(--border);
  cursor: pointer; font-size: 13px;
}
.radio-row:hover { background: var(--bg-2); }
.radio-row input { accent-color: var(--brand); }
</style>
