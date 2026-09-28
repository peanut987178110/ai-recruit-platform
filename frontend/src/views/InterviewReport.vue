<template>
  <div>
    <div class="card">
      <div class="card-head">
        <VButton variant="ghost" size="sm" @click="$router.push('/interviews')">← 返回</VButton>
        <span class="card-title">面试评估报告</span>
        <span v-if="r?.candidate_name" class="card-desc">{{ r.candidate_name }}</span>
        <span v-if="r?.human_confirmed" class="tag tag-ok ml-auto">面试官已确认</span>
        <span v-else class="tag tag-warn ml-auto">待面试官确认</span>
      </div>

      <!-- 未生成：输入逐字稿 -->
      <VDegradeAlert
        v-if="degradeInfo" :info="degradeInfo" spaced
        title="报告未生成，已降级为手动摘要模式"
        hint="候选人未明示同意录音时按合规要求降级，不影响面试流程，可在下方手动填写摘要。"
      />

      <template v-if="!r">
        <div class="alert alert-info mb-3">
          <span class="alert-icon">◆</span>
          <div>
            面试结束后由录音转写生成报告，目标是让面试官<b>零额外操作</b>即产出结构化评价。
            转写与归类自动完成，面试官只需确认或修正。
            <b>AI 不预填面试官评分</b> —— 预填会产生锚定效应，使面试官倾向于微调而非独立判断，
            直接损害评价独立性。
          </div>
        </div>

        <div class="field">
          <label class="field-label row" style="gap: 8px">
            <input type="checkbox" v-model="consent" style="accent-color: var(--brand)" />
            <span>候选人已明示同意录音（同意记录将写入决策日志）</span>
          </label>
          <div class="field-hint">
            未同意时该模块降级为面试官手动填写摘要，不阻断流程。
          </div>
        </div>

        <div class="field">
          <label class="field-label">面试逐字稿</label>
          <textarea v-model="transcript" class="textarea" rows="12"
                    placeholder="粘贴录音转写后的逐字稿。系统会按能力项归类问答，并给出锚点对照结果。" />
          <div class="field-hint">{{ transcript.length }} 字</div>
        </div>

        <VButton variant="primary" :disabled="!transcript || !consent" :loading="building" @click="build">
          生成评估报告
        </VButton>
      </template>

      <!-- 已生成 -->
      <template v-else>
        <div class="grid grid-3 mb-3">
          <div class="stat">
            <div class="stat-label">基本信息</div>
            <div class="stat-value sm">{{ r.ai_meta?.basic?.候选人 }}</div>
            <div class="stat-hint">
              {{ r.ai_meta?.basic?.面试官 || '—' }} · {{ r.ai_meta?.basic?.时长 || '—' }}
            </div>
          </div>
          <div class="stat">
            <div class="stat-label">已覆盖能力项</div>
            <div class="stat-value sm">{{ r.by_ability?.length || 0 }}<span class="stat-unit">项</span></div>
            <div class="stat-hint">按能力项归类的问答</div>
          </div>
          <div class="stat">
            <div class="stat-label">未覆盖能力项</div>
            <div class="stat-value sm" :style="{ color: (r.uncovered?.length || 0) > 0 ? 'var(--warn)' : '' }">
              {{ r.uncovered?.length || 0 }}<span class="stat-unit">项</span>
            </div>
            <div class="stat-hint">供下一轮面试官接续，避免重复或遗漏</div>
          </div>
        </div>

        <div class="grid grid-2">
          <div>
            <!-- AI 参考区：明确标注为参考，与评分区视觉分离 -->
            <div class="ai-block mb-3">
              <div class="ai-label">◆ AI 建议参考（不代表结论）</div>
              <div class="tiny muted mb-1">
                以下内容由录音转写自动生成，面试官可参考或忽略。AI 不参与评分。
              </div>
              <ul v-if="r.suggestions?.length" class="sug-list">
                <li v-for="(s, i) in r.suggestions" :key="i">{{ s }}</li>
              </ul>
              <div v-else class="tiny muted">（无观察点）</div>
            </div>

            <div v-for="a in r.by_ability" :key="a.ability_id" class="ability-block">
              <div class="row-between">
                <span class="bold small">{{ a.ability_name }}</span>
                <span class="tag tag-gray tiny">{{ a.qa?.length || 0 }} 组问答</span>
              </div>
              <div v-for="(qa, i) in a.qa" :key="i" class="qa-row">
                <div class="qa-q">问：{{ qa.question }}</div>
                <div class="qa-a">答：{{ qa.answer_summary }}</div>
                <div v-if="qa.anchor_match" class="qa-anchor">锚点对照：{{ qa.anchor_match }}</div>
              </div>
            </div>

            <div v-if="r.uncovered?.length" class="alert alert-warn mt-3">
              <span class="alert-icon">⚠</span>
              <div>
                <b>本轮未覆盖的能力项</b>（建议下一轮接续）：
                <div class="row wrap mt-1" style="gap: 5px">
                  <span v-for="u in r.uncovered" :key="u" class="tag tag-warn">{{ u }}</span>
                </div>
              </div>
            </div>
          </div>

          <div>
            <!-- 评分区：人工录入，AI 无任何预填值 -->
            <div class="human-block mb-3">
              <div class="human-label">✎ 面试官评分（1 至 5 分）</div>
              <div class="tiny muted mb-2">
                此区域无任何 AI 预填值。评分由面试官独立给出。
              </div>
              <div v-for="a in r.by_ability" :key="a.ability_id" class="score-row">
                <span class="small truncate" style="flex: 1">{{ a.ability_name }}</span>
                <div class="row" style="gap: 4px">
                  <button
                    v-for="n in 5" :key="n"
                    class="score-dot" :class="{ on: (scores[a.ability_id] || 0) >= n }"
                    @click="scores[a.ability_id] = n"
                  >{{ n }}</button>
                </div>
              </div>
            </div>

            <div class="field">
              <label class="field-label">整体结论</label>
              <textarea v-model="conclusion" class="textarea" rows="4"
                        placeholder="填写整体评价与推进建议" />
            </div>

            <VButton variant="primary" :loading="saving" :disabled="!Object.keys(scores).length"
                     style="width: 100%" @click="submitScores">
              提交评分并确认报告
            </VButton>

            <div class="divider" />
            <div class="row-between">
              <span class="tiny muted">原始转写（{{ (r.transcript || '').length }} 字）</span>
              <button class="link-btn" @click="showTranscript = !showTranscript">
                {{ showTranscript ? '折叠' : '展开' }}
              </button>
            </div>
            <div v-if="showTranscript" class="transcript">{{ r.transcript }}</div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import VButton from '../components/VButton.vue'
import VDegradeAlert from '../components/VDegradeAlert.vue'
import { interviewApi } from '../api'
import { toast } from '../components/toast'

const route = useRoute()
const sid = Number(route.params.id)

const r = ref<any>(null)
const transcript = ref('')
const consent = ref(false)
const building = ref(false)
const saving = ref(false)
const scores = ref<Record<string, number>>({})
const conclusion = ref('')
const showTranscript = ref(false)
const degradeInfo = ref<{reason:string;level:string;model:string}|null>(null)

async function load() {
  try {
    r.value = await interviewApi.getReport(sid)
    scores.value = { ...(r.value.scores || {}) }
    conclusion.value = r.value.conclusion || ''
  } catch {
    r.value = null   // 尚未生成报告，走输入流程
  }
}

async function build() {
  building.value = true
  try {
    const res = await interviewApi.buildReport(sid, {
      transcript: transcript.value, consent_recorded: consent.value,
    })
    if (res.degraded) {
      degradeInfo.value = { reason: res.reason, level: 'human', model: '' }
      toast.warn('已降级为手动摘要模式', res.reason)
      return
    }
    r.value = res.report
    r.value.ai_meta = { ...res.meta, basic: res.report.basic }
    toast.ok('报告已生成')
  } catch (e) {
    toast.err(e, '生成失败')
  } finally {
    building.value = false
  }
}

async function submitScores() {
  saving.value = true
  try {
    await interviewApi.submitScores(sid, { scores: scores.value, conclusion: conclusion.value })
    toast.ok('评分已提交，报告已确认')
    await load()
  } catch (e) {
    toast.err(e)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.sug-list { margin: 0; padding-left: 18px; font-size: 12.5px; line-height: 1.8; }
.ability-block {
  padding: 12px; border: 1px solid var(--border);
  border-radius: var(--radius); margin-bottom: 10px; background: var(--bg-0);
}
.qa-row {
  padding: 9px 0; border-bottom: 1px dashed var(--border);
}
.qa-row:last-child { border-bottom: none; }
.qa-q { font-size: 12.5px; font-weight: 550; margin-bottom: 3px; }
.qa-a { font-size: 12.5px; color: var(--text-1); line-height: 1.7; }
.qa-anchor { font-size: 11.5px; color: var(--brand-text); margin-top: 4px; }

.score-row {
  display: flex; align-items: center; gap: 10px;
  padding: 7px 0; border-bottom: 1px dashed var(--border);
}
.score-row:last-of-type { border-bottom: none; }
.score-dot {
  width: 26px; height: 26px; border-radius: 5px;
  border: 1px solid var(--border-strong); background: var(--bg-0);
  color: var(--text-1); font-size: 12px; cursor: pointer;
  font-family: inherit; transition: all 0.12s;
}
.score-dot:hover { border-color: var(--brand); color: var(--text-0); }
.score-dot.on { background: var(--brand); border-color: var(--brand); color: #fff; font-weight: 600; }

.transcript {
  margin-top: 9px; padding: 11px; max-height: 340px; overflow-y: auto;
  background: var(--bg-0); border: 1px solid var(--border);
  border-radius: var(--radius); font-size: 12.5px; line-height: 1.85;
  white-space: pre-wrap;
}
.link-btn {
  background: none; border: none; color: var(--brand-text);
  font-size: 11.5px; cursor: pointer; padding: 0; font-family: inherit;
}
</style>
