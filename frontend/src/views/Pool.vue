<template>
  <div>
    <div class="alert alert-info mb-3">
      <span class="alert-icon">ℹ</span>
      <div>
        待定池按剩余天数升序排列，剩余 2 天内的记录标黄，到期前一天会向负责 HR 发送汇总提醒。
        <b>归档不发送任何对外通知，不等于拒绝</b>，也不触发任何对外消息。归档记录仍可检索。
      </div>
    </div>

    <div class="grid grid-3 mb-3">
      <div class="stat">
        <div class="stat-label">待定池人数</div>
        <div class="stat-value">{{ d.total ?? 0 }}<span class="stat-unit">人</span></div>
        <div class="stat-hint">保留 {{ d.keep_days ?? 7 }} 天，到期自动归档</div>
      </div>
      <div class="stat">
        <div class="stat-label">2 天内到期</div>
        <div class="stat-value" :style="{ color: (d.expiring_soon ?? 0) > 0 ? 'var(--warn)' : '' }">
          {{ d.expiring_soon ?? 0 }}<span class="stat-unit">人</span>
        </div>
        <div class="stat-hint warn">需尽快决定是否捞回</div>
      </div>
      <div class="stat">
        <div class="stat-label">捞回即误杀线索</div>
        <div class="stat-value sm">专项复盘</div>
        <div class="stat-hint">捞回后通过面试的案例是发现误杀模式最直接的入口</div>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <span class="card-title">待定池</span>
        <VButton size="sm" icon="⟳" class="ml-auto" :loading="loading" @click="load">刷新</VButton>
      </div>

      <VState :items="d.items || []" :loading="loading" title="待定池为空" icon="◲"
              desc="低分档或被否决的候选人会进入这里，保留 7 天人工捞回窗口。">
        <table class="table">
          <thead>
            <tr>
              <th>候选人</th>
              <th style="width: 90px">原分档</th>
              <th style="width: 80px">总分</th>
              <th style="width: 110px">剩余天数</th>
              <th style="width: 130px">到期时间</th>
              <th>风险提示</th>
              <th style="width: 100px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in d.items" :key="c.id" :class="{ 'row-warn': c.warn }">
              <td class="clickable" @click="$router.push(`/candidate/${c.id}`)">
                <span class="bold">{{ c.name }}</span>
                <div class="tiny muted truncate" style="max-width: 260px">{{ c.summary || c.preview }}</div>
              </td>
              <td><span class="tag" :class="tierColor(c.tier)">{{ c.tier || '—' }}</span></td>
              <td class="num">{{ num(c.total_score) }}</td>
              <td>
                <span class="bold num" :style="{ color: c.warn ? 'var(--warn)' : '' }">
                  {{ c.pool_days_left }}
                </span>
                <span class="tiny muted"> 天</span>
              </td>
              <td class="tiny muted">{{ fmtDate(c.pool_expire_at) }}</td>
              <td>
                <span v-for="t in (c.risk_tags || []).slice(0, 2)" :key="t" class="tag tag-warn tiny" style="margin-right: 3px">{{ t }}</span>
                <span v-if="!c.risk_tags?.length" class="tiny muted">—</span>
              </td>
              <td>
                <VButton size="sm" variant="primary" @click="openRescue(c)">捞回</VButton>
              </td>
            </tr>
          </tbody>
        </table>
      </VState>
    </div>

    <VModal v-if="rescuing" title="从待定池捞回" @close="rescuing = null">
      <div class="alert alert-info mb-2">
        <span class="alert-icon">ℹ</span>
        <div>
          将捞回「{{ rescuing.name }}」，原分档 <b>{{ rescuing.tier }}</b>。
          该操作会记为误杀线索，进入每周复盘列表。
        </div>
      </div>
      <div class="field">
        <label class="field-label">捞回原因</label>
        <textarea v-model="reason" class="textarea" rows="3"
                  placeholder="例如：业务方反馈该候选人项目经历与岗位高度匹配" />
      </div>
      <template #foot>
        <VButton @click="rescuing = null">取消</VButton>
        <VButton variant="primary" :loading="busy" @click="doRescue">确认捞回</VButton>
      </template>
    </VModal>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import VButton from '../components/VButton.vue'
import VModal from '../components/VModal.vue'
import VState from '../components/VState.vue'
import { candApi } from '../api'
import { fmtDate, num, tierColor } from '../api/labels'
import { toast } from '../components/toast'

const d = ref<any>({})
const loading = ref(false)
const rescuing = ref<any>(null)
const reason = ref('')
const busy = ref(false)

async function load() {
  loading.value = true
  try { d.value = await candApi.poolSummary() } catch (e) { toast.err(e) } finally { loading.value = false }
}

function openRescue(c: any) { rescuing.value = c; reason.value = '' }

async function doRescue() {
  busy.value = true
  try {
    const r = await candApi.rescue(rescuing.value.id, reason.value)
    toast.ok(r.message)
    rescuing.value = null
    await load()
  } catch (e) {
    toast.err(e)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.row-warn { background: var(--warn-bg); }
.row-warn:hover { background: var(--warn-bg) !important; }
</style>
