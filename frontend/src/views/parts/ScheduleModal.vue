<template>
  <VModal :title="editing ? '调整面试安排' : '安排面试'" wide :close-on-mask="false" @close="$emit('close')">
    <div v-if="candidateName" class="who mb-2">
      <span class="bold">{{ candidateName }}</span>
      <span v-if="lineLabel" class="tag tag-gray tiny">{{ lineLabel }}</span>
    </div>

    <div class="grid grid-3">
      <div class="field">
        <label class="field-label">面试轮次<span class="req">*</span></label>
        <select v-model="f.round_name" class="select" :disabled="editing">
          <option v-for="r in ROUNDS" :key="r">{{ r }}</option>
        </select>
      </div>
      <div class="field">
        <label class="field-label">面试时间<span class="req">*</span></label>
        <input v-model="f.scheduled_at" type="datetime-local" class="input" />
      </div>
      <div class="field">
        <label class="field-label">时长</label>
        <select v-model.number="f.plan_minutes" class="select">
          <option :value="30">30 分钟（8 至 10 题）</option>
          <option :value="45">45 分钟</option>
          <option :value="60">60 分钟（12 题以上）</option>
          <option :value="90">90 分钟</option>
        </select>
      </div>
    </div>

    <!-- 面试官：按业务线分组，附待面试场次，安排人能看出谁更合适、谁更空 -->
    <div class="field">
      <label class="field-label">面试官<span class="req">*</span></label>
      <select v-model.number="f.interviewer_id" class="select">
        <option :value="0" disabled>请选择面试官</option>
        <optgroup v-for="g in groups" :key="g.label" :label="g.label">
          <option v-for="p in g.items" :key="p.id" :value="p.id">
            {{ p.name }}（{{ p.userid }}）· {{ p.role }}
            {{ p.department ? '· ' + p.department : '' }} · 待面试 {{ p.pending }} 场
          </option>
        </optgroup>
      </select>
      <div class="field-hint">
        <template v-if="!people.length">
          还没有可指派的面试官。请到「账号管理」新建「业务面试官」账号。
        </template>
        <template v-else>
          同业务线的面试官排在最前；指派后该候选人只对这位面试官可见。
        </template>
      </div>
    </div>

    <!-- 面试方式 -->
    <div class="field">
      <label class="field-label">面试方式<span class="req">*</span></label>
      <div class="row" style="gap: 8px">
        <button v-for="m in MODES" :key="m.v" type="button" class="mode-btn"
                :class="{ on: f.mode === m.v }" @click="f.mode = m.v">
          <span>{{ m.icon }}</span> {{ m.v }}
        </button>
      </div>
    </div>

    <template v-if="f.mode === '视频'">
      <div class="grid grid-3">
        <div class="field" style="grid-column: span 2">
          <label class="field-label">会议链接<span class="req">*</span></label>
          <input v-model.trim="f.meeting_url" class="input"
                 placeholder="https://meeting.tencent.com/dm/…  或飞书、Zoom 会议链接" />
        </div>
        <div class="field">
          <label class="field-label">会议号 / 入会密码</label>
          <input v-model.trim="f.meeting_code" class="input" placeholder="选填" />
        </div>
      </div>
      <div class="alert alert-info">
        <span class="alert-icon">ℹ</span>
        <div>
          平台不自建视频，使用公司已有的会议工具。先在会议工具里创建好会议，再把链接贴到这里。
          <b>面试官</b>用自己的平台账号登录，在「面试日程」或面试准备页点「进入会议」；
          <b>候选人</b>无需账号，凭安排后生成的邀请链接查看时间、入会并确认是否同意录音。
        </div>
      </div>
    </template>
    <div v-else-if="f.mode === '现场'" class="field">
      <label class="field-label">面试地点<span class="req">*</span></label>
      <input v-model.trim="f.location" class="input" placeholder="例如：总部 A 座 12 层 1203 会议室" />
    </div>
    <div v-else class="alert alert-info">
      <span class="alert-icon">ℹ</span>
      <div>电话面试由面试官按候选人简历上的联系方式拨打，无需填写会议信息。</div>
    </div>

    <div class="field">
      <label class="field-label">备注</label>
      <input v-model.trim="f.note" class="input" maxlength="200" placeholder="选填，例如：候选人希望下午" />
    </div>

    <div v-if="error" class="alert alert-danger">
      <span class="alert-icon">✕</span><div>{{ error }}</div>
    </div>

    <template #foot>
      <VButton @click="$emit('close')">取消</VButton>
      <VButton variant="primary" :loading="busy" :disabled="!canSubmit" @click="submit">
        {{ editing ? '保存调整' : '确认安排' }}
      </VButton>
    </template>
  </VModal>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import VButton from '../../components/VButton.vue'
import VModal from '../../components/VModal.vue'
import { interviewApi } from '../../api'
import { toast } from '../../components/toast'

const props = defineProps<{
  candidateId: number
  candidateName?: string
  /** 传入则为改期模式 */
  schedule?: any
}>()
const emit = defineEmits<{ close: []; done: [r: any] }>()

const ROUNDS = ['初面', '二面', '三面', '终面', 'HR面']
const MODES = [
  { v: '视频', icon: '▣' },
  { v: '现场', icon: '⌂' },
  { v: '电话', icon: '☏' },
]

const editing = computed(() => !!props.schedule)
const people = ref<any[]>([])
const line = ref('')
const busy = ref(false)
const error = ref('')

/** datetime-local 需要 YYYY-MM-DDTHH:mm */
function toLocal(v: string | Date): string {
  const d = typeof v === 'string' ? new Date(v.replace(' ', 'T')) : v
  if (isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}
function defaultWhen(): string {
  // 默认明天上午 10 点，比「现在」更接近真实排期
  const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(10, 0, 0, 0)
  return toLocal(d)
}

const s = props.schedule
const f = ref({
  round_name: s?.round_name || '初面',
  scheduled_at: s?.scheduled_at ? toLocal(s.scheduled_at) : defaultWhen(),
  plan_minutes: s?.plan_minutes || 30,
  interviewer_id: s?.interviewer_id || 0,
  mode: s?.mode || '视频',
  meeting_url: s?.meeting_url || '',
  meeting_code: s?.meeting_code || '',
  location: s?.location || '',
  note: s?.note || '',
})

const lineLabel = computed(() => line.value)
const groups = computed(() => {
  const order = ['同业务线', '跨业务线', '其它业务线']
  return order
    .map((k) => ({
      label: k === '同业务线' ? `同业务线（${line.value}）` : k === '跨业务线' ? '不限业务线' : '其它业务线',
      items: people.value.filter((p) => p.match === k),
    }))
    .filter((g) => g.items.length)
})

const canSubmit = computed(() => {
  if (!f.value.interviewer_id || !f.value.scheduled_at) return false
  if (f.value.mode === '视频' && !f.value.meeting_url) return false
  if (f.value.mode === '现场' && !f.value.location) return false
  return true
})

onMounted(async () => {
  try {
    const r = await interviewApi.interviewers(props.candidateId)
    people.value = r.items || []
    line.value = r.business_line || ''
    // 新建时默认选中最合适的那位（已按业务线与负载排好序）
    if (!f.value.interviewer_id && people.value.length) {
      f.value.interviewer_id = people.value[0].id
    }
  } catch (e) {
    toast.err(e, '加载面试官失败')
  }
})

async function submit() {
  error.value = ''
  busy.value = true
  try {
    const payload = { ...f.value }
    const r = editing.value
      ? await interviewApi.update(props.schedule.id, payload)
      : await interviewApi.create({ ...payload, candidate_id: props.candidateId })
    for (const w of r.warnings || []) toast.warn('时间冲突', w)
    emit('done', r)
  } catch (e: any) {
    error.value = e?.friendly || '保存失败'
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.who { display: flex; align-items: center; gap: 8px; }
.mode-btn {
  padding: 7px 16px; border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--bg-0); color: var(--text-1); cursor: pointer;
  font-family: inherit; font-size: 13px;
}
.mode-btn:hover { border-color: var(--brand); }
.mode-btn.on { border-color: var(--brand); color: var(--brand-text); background: var(--brand-soft, rgba(59,130,246,.08)); font-weight: 560; }
</style>
