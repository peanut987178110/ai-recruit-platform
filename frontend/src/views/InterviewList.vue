<template>
  <div>
    <div class="card">
      <div class="card-head">
        <span class="card-title">面试日程</span>
        <span class="card-desc">
          {{ isInterviewer ? '只显示指派给你的面试' : '面试官仅可见被指派给自己的场次' }}
        </span>
        <div class="ml-auto row">
          <div class="seg">
            <button v-for="t in TABS" :key="t.v" :class="{ on: tab === t.v }" @click="tab = t.v; load()">
              {{ t.label }}
            </button>
          </div>
          <VButton size="sm" icon="⟳" :loading="loading" @click="load">刷新</VButton>
        </div>
      </div>

      <VState :items="rows" :loading="loading" title="暂无面试安排" icon="◷"
              :desc="isInterviewer
                ? '还没有面试指派给你。HR 安排面试时选择你作为面试官后，会出现在这里。'
                : '在候选人复核页点击「采纳」后即可安排面试：选择面试官、时间与方式（视频 / 现场 / 电话）。'">
        <table class="table">
          <thead>
            <tr>
              <th>候选人</th>
              <th style="width: 76px">轮次</th>
              <th style="width: 150px">面试时间</th>
              <th style="width: 130px">面试官</th>
              <th style="width: 150px">方式</th>
              <th style="width: 100px">录音同意</th>
              <th style="width: 80px">状态</th>
              <th style="width: 280px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in rows" :key="s.id" :class="{ soon: isSoon(s) }">
              <td>
                <div class="bold">{{ s.candidate_name }}</div>
                <div class="tiny muted">{{ s.position_name }}</div>
              </td>
              <td><span class="tag tag-gray">{{ s.round_name }}</span></td>
              <td class="small">
                <div>{{ fmtWhen(s.scheduled_at) }}</div>
                <div class="tiny muted">{{ s.plan_minutes }} 分钟 · {{ relative(s.scheduled_at) }}</div>
              </td>
              <td class="small">
                <template v-if="s.interviewer">
                  <div>{{ s.interviewer }}</div>
                  <div class="tiny muted mono">{{ s.interviewer_userid }}</div>
                </template>
                <span v-else class="tag tag-warn">未指派</span>
              </td>
              <td class="small">
                <span class="tag" :class="modeTag(s.mode)">{{ s.mode }}</span>
                <div v-if="s.mode === '现场'" class="tiny muted mt-1 truncate" :title="s.location">{{ s.location }}</div>
                <div v-else-if="s.mode === '视频' && s.meeting_code" class="tiny muted mt-1 mono">{{ s.meeting_code }}</div>
              </td>
              <td>
                <span class="tag tiny" :class="consentTag(s.consent_status)">{{ s.consent_status }}</span>
              </td>
              <td>
                <span class="tag" :class="statusTag(s.status)">{{ s.status }}</span>
              </td>
              <td>
                <div class="ops">
                  <a v-if="s.mode === '视频' && s.meeting_url && s.status === '待面试'"
                     :href="s.meeting_url" target="_blank" rel="noopener noreferrer"
                     class="btn btn-sm btn-ok">进入会议</a>
                  <VButton size="sm" variant="primary" @click="$router.push(`/interview/${s.id}`)">
                    {{ s.question_count ? '准备' : '出题' }}
                  </VButton>
                  <VButton v-if="s.has_report" size="sm" @click="$router.push(`/interview/${s.id}/report`)">报告</VButton>
                  <template v-if="canSchedule && s.status === '待面试'">
                    <VButton size="sm" variant="ghost" @click="copyInvite(s)">复制候选人链接</VButton>
                    <VButton size="sm" variant="ghost" title="旧链接立即失效" @click="resetInvite(s)">重发链接</VButton>
                    <VButton size="sm" variant="ghost" @click="editing = s">改期/改派</VButton>
                    <VButton size="sm" variant="ghost" @click="cancelling = s; cancelReason = ''">取消</VButton>
                  </template>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </VState>
    </div>

    <!-- 视频面试怎么进 —— 两类人入口不同，写在页面上而不是藏在文档里 -->
    <div class="card mt-3">
      <div class="card-head">
        <span class="card-title">视频面试怎么进</span>
        <span class="card-desc">平台不自建音视频，使用公司已有的会议工具</span>
      </div>
      <div class="grid grid-2">
        <div class="how">
          <div class="bold small mb-1">面试官</div>
          <ol class="tiny">
            <li>用自己的平台账号登录（与日常登录相同）</li>
            <li>在本页或面试准备页点「进入会议」，会在新窗口打开会议</li>
            <li>一边面试一边在平台上看分层题目与评分锚点</li>
            <li>结束后回到平台生成评估报告并打分</li>
          </ol>
        </div>
        <div class="how">
          <div class="bold small mb-1">候选人</div>
          <ol class="tiny">
            <li><b>无需平台账号</b>，HR 通过邮件或短信发送邀请链接</li>
            <li>打开链接可看到时间、方式、会议入口</li>
            <li>面试开始前在链接页确认是否同意录音</li>
            <li>链接只含本场面试信息，取消或重发后旧链接立即失效</li>
          </ol>
        </div>
      </div>
    </div>

    <ScheduleModal v-if="editing" :candidate-id="editing.candidate_id"
                   :candidate-name="editing.candidate_name" :schedule="editing"
                   @close="editing = null" @done="onEdited" />

    <VModal v-if="cancelling" :title="`取消 ${cancelling.candidate_name} 的${cancelling.round_name}`"
            @close="cancelling = null">
      <div class="field">
        <label class="field-label">取消原因<span class="req">*</span></label>
        <input v-model.trim="cancelReason" class="input" placeholder="例如：候选人已接受其它 offer" />
      </div>
      <div class="alert alert-warn">
        <span class="alert-icon">⚠</span>
        <div>取消后候选人的邀请链接立即失效，面试官日程中不再显示。如需通知候选人，请另行告知。</div>
      </div>
      <template #foot>
        <VButton @click="cancelling = null">返回</VButton>
        <VButton variant="danger" :disabled="!cancelReason" :loading="busy" @click="doCancel">确认取消</VButton>
      </template>
    </VModal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import VButton from '../components/VButton.vue'
import VModal from '../components/VModal.vue'
import VState from '../components/VState.vue'
import ScheduleModal from './parts/ScheduleModal.vue'
import { currentUser, interviewApi, inviteUrl } from '../api'
import { toast } from '../components/toast'

const TABS = [
  { v: '待面试', label: '待面试' },
  { v: '已完成', label: '已完成' },
  { v: '已取消', label: '已取消' },
  { v: '', label: '全部' },
]

const rows = ref<any[]>([])
const loading = ref(false)
const busy = ref(false)
const tab = ref('待面试')
const editing = ref<any>(null)
const cancelling = ref<any>(null)
const cancelReason = ref('')

const me = computed(() => currentUser.value)
const isInterviewer = computed(() => me.value?.role === '业务面试官')
// 与后端 interview.schedule 权限一致；后端才是真正的边界
const canSchedule = computed(() =>
  !!me.value && (me.value.is_super || ['招聘HR', 'HR负责人', '用人经理'].includes(me.value.role)))

async function load() {
  loading.value = true
  try {
    // 「全部」要连已取消的一起看，所以显式传状态
    rows.value = await interviewApi.schedules(tab.value ? { status: tab.value } : { status: '' })
    if (!tab.value) {
      const cancelled = await interviewApi.schedules({ status: '已取消' })
      rows.value = [...rows.value, ...cancelled]
    }
  } catch (e) {
    toast.err(e)
  } finally {
    loading.value = false
  }
}

function fmtWhen(v: string): string {
  if (!v) return '未定'
  const d = new Date(v.replace(' ', 'T'))
  if (isNaN(d.getTime())) return v
  const p = (n: number) => String(n).padStart(2, '0')
  const w = '日一二三四五六'[d.getDay()]
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} 周${w} ${p(d.getHours())}:${p(d.getMinutes())}`
}
function relative(v: string): string {
  if (!v) return ''
  const ms = new Date(v.replace(' ', 'T')).getTime() - Date.now()
  const h = Math.round(ms / 3600000)
  if (ms < 0) return h > -24 ? '已开始' : '已过'
  if (h < 1) return '即将开始'
  if (h < 24) return `${h} 小时后`
  return `${Math.round(h / 24)} 天后`
}
function isSoon(s: any): boolean {
  if (s.status !== '待面试' || !s.scheduled_at) return false
  const ms = new Date(s.scheduled_at.replace(' ', 'T')).getTime() - Date.now()
  return ms > -3600000 && ms < 3600000
}
const modeTag = (m: string) => ({ 视频: 'tag-blue', 现场: 'tag-purple', 电话: 'tag-gray' }[m] || 'tag-gray')
const consentTag = (c: string) => ({ 同意: 'tag-ok', 不同意: 'tag-warn' }[c] || 'tag-gray')
const statusTag = (s: string) => ({ 已完成: 'tag-ok', 已取消: 'tag-gray' }[s] || 'tag-blue')

async function copyInvite(s: any) {
  if (!s.invite_token) { toast.warn('该场面试没有有效的邀请链接'); return }
  const url = inviteUrl(s.invite_token)
  const text = `${s.candidate_name}您好，您的${s.round_name}安排在 ${fmtWhen(s.scheduled_at)}（${s.mode}面试，约 ${s.plan_minutes} 分钟）。\n面试信息与入口：${url}\n请在面试开始前打开链接确认是否同意录音。`
  try {
    await navigator.clipboard.writeText(text)
    toast.ok('已复制邀请文本', '可直接粘贴到邮件或短信中发给候选人')
  } catch {
    // 非 https 下剪贴板不可用，退回让用户手动复制
    prompt('请复制以下内容发给候选人：', text)
  }
}

async function resetInvite(s: any) {
  if (!confirm(`为 ${s.candidate_name} 重新生成邀请链接？

旧链接会立即失效，需要把新链接重新发给候选人。`)) return
  try {
    const r = await interviewApi.resetInvite(s.id)
    s.invite_token = r.invite_token
    toast.ok('已生成新链接', '旧链接已失效')
    await copyInvite(s)
  } catch (e) {
    toast.err(e)
  }
}

function onEdited(r: any) {
  editing.value = null
  toast.ok('已保存调整', (r.changes || []).join('；'))
  load()
}

async function doCancel() {
  busy.value = true
  try {
    await interviewApi.cancel(cancelling.value.id, cancelReason.value)
    toast.ok('已取消面试')
    cancelling.value = null
    load()
  } catch (e) {
    toast.err(e)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.seg { display: inline-flex; border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
.seg button {
  padding: 4px 12px; background: var(--bg-0); border: none; cursor: pointer;
  font-family: inherit; font-size: 12px; color: var(--text-1);
}
.seg button + button { border-left: 1px solid var(--border); }
.seg button.on { background: var(--brand); color: #fff; }
.ops { display: flex; flex-wrap: wrap; gap: 4px; }
tr.soon td { background: var(--warn-soft, rgba(245, 158, 11, 0.07)); }
.how { padding: 12px 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--bg-0); }
.how ol { margin: 0; padding-left: 18px; line-height: 1.9; color: var(--text-1); }
.btn.btn-sm { display: inline-flex; align-items: center; text-decoration: none; }
</style>
