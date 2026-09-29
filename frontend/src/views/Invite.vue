<template>
  <div class="invite-page">
    <div class="invite-card">
      <div class="brand">
        <span class="dot" />
        <span>面试邀请</span>
      </div>

      <div v-if="loading" class="state">正在加载…</div>

      <div v-else-if="error" class="state">
        <div class="state-icon">◷</div>
        <div class="state-title">链接无法使用</div>
        <div class="state-desc">{{ error }}</div>
      </div>

      <template v-else-if="d">
        <h1 class="title">{{ d.candidate_name }}，您好</h1>
        <p class="lead">您的 <b>{{ d.position_name }}</b> {{ d.round_name }}已安排，信息如下。</p>

        <div class="info">
          <div class="row"><span class="k">时间</span><span class="v">{{ fmtWhen(d.scheduled_at) }}</span></div>
          <div class="row"><span class="k">时长</span><span class="v">约 {{ d.plan_minutes }} 分钟</span></div>
          <div class="row"><span class="k">方式</span><span class="v">{{ d.mode }}面试</span></div>
          <div v-if="d.mode === '视频' && d.meeting_code" class="row">
            <span class="k">会议号</span><span class="v mono">{{ d.meeting_code }}</span>
          </div>
          <div v-if="d.mode === '现场'" class="row">
            <span class="k">地点</span><span class="v">{{ d.location }}</span>
          </div>
        </div>

        <a v-if="d.mode === '视频' && d.meeting_url" :href="d.meeting_url" target="_blank"
           rel="noopener noreferrer" class="join">▣ 进入视频会议</a>
        <p v-if="d.mode === '视频'" class="hint">
          会议将在您的会议软件（或浏览器）中打开。建议提前 5 分钟进入并检查摄像头与麦克风。
        </p>
        <p v-else-if="d.mode === '电话'" class="hint">面试官将按您简历上的电话号码与您联系，请保持电话畅通。</p>
        <p v-else class="hint">请携带有效身份证件，提前 10 分钟到达。</p>

        <!-- 录音同意：PRD 3.3.3 要求候选人明示同意后才可录音；不同意不影响面试 -->
        <div class="consent">
          <div class="consent-title">关于面试录音</div>
          <p class="consent-text">
            为了让面试评价更准确，面试官希望对本次面试录音，录音仅用于本岗位的面试评估，
            不会用于其他用途。<b>是否同意完全由您决定，不同意不会影响面试结果。</b>
          </p>
          <div v-if="d.consent_status !== '未回复'" class="consent-now">
            您已选择：
            <b :class="d.consent_status === '同意' ? 'ok' : 'warn'">{{ d.consent_status }}录音</b>
            <span v-if="d.consent_editable" class="muted">（面试开始前可以修改）</span>
          </div>
          <div v-if="d.consent_editable" class="consent-btns">
            <button :class="{ on: d.consent_status === '同意' }" :disabled="saving" @click="answer(true)">同意录音</button>
            <button :class="{ on: d.consent_status === '不同意' }" :disabled="saving" @click="answer(false)">不同意</button>
          </div>
          <p v-else class="muted small">面试已开始，如需变更请直接告诉面试官。</p>
        </div>

        <p class="foot">
          本链接仅用于您本人的这场面试，请勿转发。如时间不便，请回复发送邀请的 HR。
        </p>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { inviteApi } from '../api'

const route = useRoute()
// 用 computed 而不是常量：同一页内换链接时 vue-router 会复用组件，常量会停在旧令牌上
const token = computed(() => String(route.params.token || ''))

const d = ref<any>(null)
const loading = ref(true)
const saving = ref(false)
const error = ref('')

function fmtWhen(v: string): string {
  if (!v) return '待定'
  const dt = new Date(v.replace(' ', 'T'))
  if (isNaN(dt.getTime())) return v
  const p = (n: number) => String(n).padStart(2, '0')
  const w = '日一二三四五六'[dt.getDay()]
  return `${dt.getFullYear()} 年 ${dt.getMonth() + 1} 月 ${dt.getDate()} 日（周${w}）${p(dt.getHours())}:${p(dt.getMinutes())}`
}

async function load() {
  loading.value = true
  error.value = ''
  d.value = null
  try {
    d.value = await inviteApi.view(token.value)
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '邀请链接无效或已过期。如需参加面试，请联系发送邀请的 HR。'
  } finally {
    loading.value = false
  }
}

async function answer(agree: boolean) {
  saving.value = true
  try {
    const r = await inviteApi.consent(token.value, agree)
    d.value.consent_status = r.consent_status
  } catch (e: any) {
    alert(e?.response?.data?.detail || '提交失败，请稍后重试')
  } finally {
    saving.value = false
  }
}

onMounted(load)
watch(token, load)
</script>

<style scoped>
.invite-page {
  min-height: 100vh; display: flex; justify-content: center; align-items: flex-start;
  padding: 40px 16px; background: var(--bg-1, #f5f7fa);
}
.invite-card {
  width: 100%; max-width: 520px; background: var(--bg-0, #fff);
  border: 1px solid var(--border); border-radius: 14px; padding: 28px 26px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.06);
}
.brand { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-1); margin-bottom: 18px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--brand); }
.title { font-size: 21px; margin: 0 0 6px; font-weight: 640; }
.lead { margin: 0 0 18px; color: var(--text-1); line-height: 1.7; }
.info { border: 1px solid var(--border); border-radius: 10px; padding: 4px 14px; }
.info .row { display: flex; padding: 9px 0; border-bottom: 1px dashed var(--border); }
.info .row:last-child { border-bottom: none; }
.k { width: 64px; color: var(--text-2, #888); font-size: 13px; flex-shrink: 0; }
.v { font-size: 14px; }
.join {
  display: block; text-align: center; margin: 18px 0 8px; padding: 12px;
  background: var(--brand); color: #fff; border-radius: 10px; text-decoration: none;
  font-weight: 600; font-size: 15px;
}
.join:hover { filter: brightness(1.08); }
.hint { font-size: 12.5px; color: var(--text-2, #888); line-height: 1.7; margin: 8px 0 0; }
.consent { margin-top: 22px; padding: 14px 15px; border-radius: 10px; background: var(--bg-1, #f5f7fa); }
.consent-title { font-weight: 620; font-size: 14px; margin-bottom: 6px; }
.consent-text { font-size: 13px; line-height: 1.75; color: var(--text-1); margin: 0 0 10px; }
.consent-now { font-size: 13px; margin-bottom: 10px; }
.consent-now .ok { color: var(--ok); }
.consent-now .warn { color: var(--warn); }
.consent-btns { display: flex; gap: 10px; }
.consent-btns button {
  flex: 1; padding: 10px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg-0, #fff); cursor: pointer; font-family: inherit; font-size: 14px; color: var(--text-0);
}
.consent-btns button.on { border-color: var(--brand); color: var(--brand-text); font-weight: 600; }
.consent-btns button:disabled { opacity: 0.5; cursor: wait; }
.foot { margin: 20px 0 0; font-size: 12px; color: var(--text-2, #888); line-height: 1.7; }
.state { text-align: center; padding: 30px 0; color: var(--text-1); }
.state-icon { font-size: 32px; margin-bottom: 8px; }
.state-title { font-weight: 620; font-size: 16px; margin-bottom: 6px; }
.state-desc { font-size: 13px; line-height: 1.7; }
.muted { color: var(--text-2, #888); }
.small { font-size: 12.5px; }
.mono { font-family: ui-monospace, Consolas, monospace; }
</style>
