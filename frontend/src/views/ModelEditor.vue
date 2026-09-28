<template>
  <div v-if="m">
    <div class="card">
      <div class="card-head">
        <VButton variant="ghost" size="sm" @click="$router.push('/models')">← 返回</VButton>
        <span class="card-title">{{ m.position_name }}</span>
        <span class="tag tag-gray">{{ m.version }}</span>
        <span v-if="m.active" class="tag tag-ok">已启用</span>
        <span v-else class="tag tag-warn">停用中</span>
        <div class="ml-auto row">
          <VButton size="sm" :loading="analyzing" icon="✦" @click="aiExtract">
            AI 提炼能力项
          </VButton>
          <VButton size="sm" icon="⟳" :loading="loading" @click="load">刷新</VButton>
          <VButton v-if="!m.active" size="sm" variant="ok" :disabled="!weightOk" @click="toggle">
            启用模型
          </VButton>
          <VButton v-else size="sm" @click="toggle">停用</VButton>
        </div>
      </div>

      <div class="grid grid-4">
        <div class="stat">
          <div class="stat-label">权重合计</div>
          <div class="stat-value sm" :style="{ color: weightOk ? 'var(--ok)' : 'var(--danger)' }">
            {{ totalWeight * 10 }}<span class="stat-unit">%</span>
          </div>
          <div class="stat-hint" :class="weightOk ? 'ok' : 'danger'">
            {{ weightOk ? '校验通过，可保存' : `差 ${Math.abs(100 - totalWeight * 10)}%，系统不会自动归一化` }}
          </div>
        </div>
        <div class="stat">
          <div class="stat-label">能力项</div>
          <div class="stat-value sm">{{ items.length }}<span class="stat-unit">项</span></div>
          <div class="stat-hint">须可被证据验证，不可写抽象词</div>
        </div>
        <div class="stat">
          <div class="stat-label">否决项</div>
          <div class="stat-value sm" :style="{ color: vetoCount > 3 ? 'var(--danger)' : '' }">
            {{ vetoCount }}<span class="stat-unit">/3</span>
          </div>
          <div class="stat-hint" :class="vetoCount > 3 ? 'danger' : ''">
            超出会使打分退化为硬性筛选
          </div>
        </div>
        <div class="stat">
          <div class="stat-label">影响范围</div>
          <div class="stat-value sm">{{ m.headcount }}<span class="stat-unit">在招</span></div>
          <div class="stat-hint">修改已启用模型会生成新版本</div>
        </div>
      </div>
    </div>

    <div class="editor-grid">
      <!-- 左：能力项编辑 -->
      <div class="card">
        <div class="card-head">
          <span class="card-title">能力项</span>
          <span class="card-desc">权重为 1 至 10 整数，系统实时换算为百分比</span>
          <VButton size="sm" class="ml-auto" icon="+" :disabled="items.length >= 10" @click="addItem">
            添加能力项
          </VButton>
        </div>

        <div v-for="(it, i) in items" :key="i" class="item-card">
          <div class="row-between">
            <span class="tiny muted">#{{ i + 1 }}</span>
            <div class="row" style="gap: 8px">
              <label class="row tiny" style="gap: 5px; cursor: pointer">
                <span>否决项</span>
                <span class="switch" style="transform: scale(0.85)">
                  <input type="checkbox" v-model="it.is_veto" @change="onVetoChange(it)" />
                  <span class="switch-slider" />
                </span>
              </label>
              <VButton size="sm" variant="ghost" :disabled="items.length <= 1" @click="items.splice(i, 1)">
                删除
              </VButton>
            </div>
          </div>

          <div class="field mt-1">
            <input v-model="it.name" class="input" maxlength="30"
                   placeholder="能力项名称，例如：独立负责过完整业务模块的设计与落地" />
          </div>

          <div v-if="it.is_veto" class="field">
            <label class="field-label">否决方向</label>
            <div class="row" style="gap: 8px">
              <label class="radio-inline" :class="{ on: it.veto_polarity === 'positive' }">
                <input type="radio" value="positive" v-model="it.veto_polarity" />
                <div>
                  <div class="bold tiny">必须具备（未命中即归零）</div>
                  <div class="tiny muted">例：学历本科及以上</div>
                </div>
              </label>
              <label class="radio-inline" :class="{ on: it.veto_polarity === 'negative' }">
                <input type="radio" value="negative" v-model="it.veto_polarity" />
                <div>
                  <div class="bold tiny">排除条款（命中即归零）</div>
                  <div class="tiny muted">例：简历真实性存疑</div>
                </div>
              </label>
            </div>
            <div class="field-hint">
              方向设错会导致系统性误判：「简历真实性存疑」若按「必须具备」处理，
              会把简历真实的候选人全部打成 0 分。
            </div>
          </div>

          <div class="field">
            <label class="field-label">权重（{{ it.weight }} → {{ it.weight * 10 }}%）</label>
            <input v-model.number="it.weight" type="range" min="1" max="10" class="range" />
          </div>

          <div class="field">
            <label class="field-label">证据类型</label>
            <div class="row wrap" style="gap: 6px">
              <label v-for="e in EVIDENCE_OPTS" :key="e.k" class="chip" :class="{ on: it.evidence_types.includes(e.k) }">
                <input type="checkbox" :value="e.k" v-model="it.evidence_types" hidden />
                {{ e.label }}
              </label>
            </div>
          </div>

          <div class="field mb-0">
            <label class="field-label">判定说明</label>
            <textarea v-model="it.criteria" class="textarea" rows="2" maxlength="200"
                      placeholder="描述什么样的表述算命中，作为模型判断依据" />
          </div>
        </div>

        <div v-if="!items.length" class="empty">
          <div class="empty-icon">◆</div>
          <div class="empty-title">还没有能力项</div>
          <div class="empty-desc">可以手动添加，或用上方的「AI 提炼能力项」从 JD 自动生成草稿。</div>
        </div>
      </div>

      <!-- 右：试算区 -->
      <div class="card trial-card">
        <div class="card-head">
          <span class="card-title">试算区</span>
          <span class="card-desc">选 3 至 5 份历史简历，保存前先判断标准是否合理</span>
        </div>

        <div class="row mb-2">
          <select v-model="sampleSeq" class="select" style="max-width: 150px" @change="loadSamples">
            <option value="">全部序列</option>
            <option v-for="s in ['技术', '产品', '运营', '职能']" :key="s">{{ s }}</option>
          </select>
          <VButton size="sm" variant="primary" :loading="trialing" :disabled="!canTrial" @click="runTrial">
            用当前草稿试算
          </VButton>
        </div>

        <div class="sample-list">
          <label v-for="s in samples" :key="s.id" class="sample-row" :class="{ on: picked.includes(s.id) }">
            <input type="checkbox" :value="s.id" v-model="picked" hidden />
            <div style="flex: 1; min-width: 0">
              <div class="row" style="gap: 6px">
                <span class="bold tiny">{{ s.name }}</span>
                <span class="tag tiny" :class="s.label === 'pass' ? 'tag-ok' : 'tag-gray'">
                  历史结论：{{ s.label === 'pass' ? '通过' : '拒绝' }}
                </span>
                <span v-if="s.is_edge_case" class="tag tag-purple tiny">边界样本</span>
              </div>
              <div class="tiny muted truncate">{{ s.preview }}</div>
            </div>
          </label>
        </div>

        <div v-if="trial" class="mt-2">
          <div class="alert" :class="trial.is_draft ? 'alert-info' : 'alert-warn'">
            <span class="alert-icon">ℹ</span>
            <div>
              使用版本：<b>{{ trial.version }}</b>。
              {{ trial.is_draft ? '这是未保存的草稿试算，不影响在库候选人。' : '这是当前生效版本。' }}
            </div>
          </div>

          <div v-for="s in trial.samples" :key="s.sample_id" class="trial-result">
            <div class="row-between">
              <span class="bold small">{{ s.name }}</span>
              <div class="row" style="gap: 6px">
                <span class="tag" :class="tierColor(s.tier)">{{ s.tier }}</span>
                <span class="num bold" :style="{ color: scoreColor(s.total) }">{{ num(s.total) }}</span>
              </div>
            </div>
            <div class="row-between tiny muted mt-1">
              <span>历史结论：{{ s.truth_label === 'pass' ? '通过' : '拒绝' }}</span>
              <span v-if="tierMatch(s)" class="tag tag-ok tiny">✓ 与历史结论一致</span>
              <span v-else class="tag tag-warn tiny">✕ 与历史结论不一致</span>
            </div>
            <div v-if="s.veto_hit" class="tiny mt-1" style="color: var(--danger)">
              触发否决：{{ s.veto_reason }}
            </div>
            <div class="mt-1">
              <div v-for="a in s.abilities" :key="a.ability_id" class="trial-ability">
                <span class="truncate" style="flex: 1">{{ a.ability_name }}</span>
                <span class="tag tiny" :class="stateColor(a.state)">{{ STATE_LABEL[a.state] }}</span>
                <span class="num tiny" style="width: 34px; text-align: right">{{ num(a.score) }}</span>
                <span class="tiny muted" style="width: 22px; text-align: right">
                  {{ a.evidence?.length || 0 }}
                </span>
              </div>
              <div class="tiny muted mt-1" style="text-align: right">末列为证据条数</div>
            </div>
          </div>
        </div>

        <div v-else class="empty" style="padding: 26px 12px">
          <div class="empty-icon">◇</div>
          <div class="empty-title">尚未试算</div>
          <div class="empty-desc">
            勾选历史简历后点击试算，可看到每份简历的总分、逐项状态与证据条数，
            以及是否与历史人工结论一致。
          </div>
        </div>
      </div>
    </div>

    <!-- 保存栏 -->
    <div class="save-bar">
      <div class="row">
        <span class="tiny" :style="{ color: weightOk ? 'var(--ok)' : 'var(--danger)' }">
          {{ weightOk ? '✓ 权重校验通过' : `✕ 权重合计 ${totalWeight * 10}%，需等于 100%` }}
        </span>
        <span v-if="vetoCount > 3" class="tiny" style="color: var(--danger)">
          ✕ 否决项 {{ vetoCount }} 个，上限 3 个
        </span>
        <span v-if="m.active" class="tiny muted">
          保存将生成新版本 {{ nextVersion }}，不影响已完成打分的历史记录
        </span>
      </div>
      <div class="row ml-auto">
        <VButton @click="load">放弃修改</VButton>
        <VButton variant="primary" :disabled="!canSave" :loading="saving" @click="save">
          保存{{ m.active ? '并生成新版本' : '' }}
        </VButton>
      </div>
    </div>
  </div>

  <VState v-else :items="[]" :loading="loading" title="模型不存在" icon="✕" />
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import VButton from '../components/VButton.vue'
import VState from '../components/VState.vue'
import { modelApi } from '../api'
import { STATE_LABEL, num, scoreColor, stateColor, tierColor } from '../api/labels'
import { toast } from '../components/toast'

const route = useRoute()
const m = ref<any>(null)
const items = ref<any[]>([])
const loading = ref(false)
const saving = ref(false)
const analyzing = ref(false)
const trialing = ref(false)
const samples = ref<any[]>([])
const picked = ref<number[]>([])
const sampleSeq = ref('')
const trial = ref<any>(null)

const EVIDENCE_OPTS = [
  { k: 'project', label: '项目经历' },
  { k: 'tenure', label: '任职履历' },
  { k: 'skill', label: '技能标签' },
  { k: 'metric', label: '量化成果' },
  { k: 'education', label: '教育背景' },
]

const totalWeight = computed(() => items.value.reduce((s, i) => s + (Number(i.weight) || 0), 0))
const vetoCount = computed(() => items.value.filter((i) => i.is_veto).length)
const weightOk = computed(() => totalWeight.value === 10)
const canSave = computed(() => weightOk.value && vetoCount.value <= 3 && items.value.length > 0
  && items.value.every((i) => i.name?.trim()))
const canTrial = computed(() => picked.value.length >= 1 && items.value.length > 0)
const nextVersion = computed(() => {
  const n = parseInt(String(m.value?.version || 'v1').replace('v', ''), 10) || 1
  return `v${n + 1}`
})

async function load() {
  loading.value = true
  try {
    const d = await modelApi.get(Number(route.params.id))
    m.value = d
    items.value = d.items.map((i: any) => ({
      name: i.name, weight: i.weight,
      evidence_types: [...(i.evidence_types || [])],
      criteria: i.criteria || '', is_veto: !!i.is_veto,
      veto_polarity: i.veto_polarity || 'positive',
    }))
    trial.value = null
  } catch (e) {
    toast.err(e, '加载模型失败')
    m.value = null
  } finally {
    loading.value = false
  }
}

function addItem() {
  items.value.push({
    name: '', weight: 1, evidence_types: ['project'], criteria: '',
    is_veto: false, veto_polarity: 'positive',
  })
}

function onVetoChange(it: any) {
  if (vetoCount.value > 3) {
    it.is_veto = false
    toast.warn('否决项上限为 3 个', '否决项过多会使打分退化为硬性筛选，失去模型意义')
  }
}

async function save() {
  saving.value = true
  try {
    const r = await modelApi.update(Number(route.params.id), items.value)
    toast.ok(m.value.active ? `已生成新版本 ${r.version}` : '已保存',
             m.value.active ? '历史打分记录仍显示当时使用的版本' : '')
    await load()
  } catch (e) {
    toast.err(e)
  } finally {
    saving.value = false
  }
}

async function toggle() {
  try {
    const r = await modelApi.toggle(Number(route.params.id))
    toast.ok(r.model.active ? '已启用' : '已停用',
             r.model.active ? `影响范围：在招岗位 ${r.impact['在招岗位数']} 个、在库候选人 ${r.impact['在库候选人数']} 人` : '')
    await load()
  } catch (e) {
    toast.err(e)
  }
}

async function aiExtract() {
  if (!m.value?.jd_text && !m.value?.position_name) return
  analyzing.value = true
  try {
    const jd = m.value.jd_text || `${m.value.position_name}（${m.value.seq}序列）`
    const r = await modelApi.analyzeJd(jd, m.value.seq)
    const cands = r.analysis.ability_candidates || []
    if (!cands.length) { toast.warn('未提炼出能力项候选'); return }
    cands.forEach((name: string) => {
      if (items.value.length < 10) {
        items.value.push({
          name: name.slice(0, 30), weight: 1, evidence_types: ['project', 'tenure'],
          criteria: '', is_veto: false, veto_polarity: 'positive',
        })
      }
    })
    toast.ok(`已添加 ${cands.length} 个能力项草稿`, '请在保存前调整权重至总和 100%')
  } catch (e) {
    toast.err(e, 'AI 提炼失败')
  } finally {
    analyzing.value = false
  }
}

async function loadSamples() {
  try { samples.value = await modelApi.samples(sampleSeq.value) } catch { /* ignore */ }
}

async function runTrial() {
  trialing.value = true
  try {
    trial.value = await modelApi.trial(Number(route.params.id), {
      sample_ids: picked.value,
      items: items.value.map((i, idx) => ({ ...i, id: `draft-${idx}` })),
    })
  } catch (e) {
    toast.err(e, '试算失败')
  } finally {
    trialing.value = false
  }
}

function tierMatch(s: any) {
  const aiPass = ['高分档', '中间档'].includes(s.tier)
  return aiPass === (s.truth_label === 'pass')
}

onMounted(async () => {
  await load()
  await loadSamples()
})
</script>

<style scoped>
.editor-grid {
  display: grid; grid-template-columns: 1fr 460px;
  gap: 14px; margin-top: 14px; align-items: start;
}
@media (max-width: 1200px) { .editor-grid { grid-template-columns: 1fr; } }

.item-card {
  padding: 12px 13px; border: 1px solid var(--border);
  border-radius: var(--radius); margin-bottom: 10px; background: var(--bg-0);
}
.range { width: 100%; accent-color: var(--brand); }

.chip {
  padding: 4px 10px; border: 1px solid var(--border); border-radius: 14px;
  font-size: 12px; cursor: pointer; color: var(--text-1); transition: all 0.12s;
}
.chip:hover { border-color: var(--border-strong); }
.chip.on { background: var(--brand-dim); border-color: var(--brand); color: var(--brand-text); font-weight: 550; }

.radio-inline {
  display: flex; gap: 7px; align-items: flex-start; flex: 1;
  padding: 8px 10px; border: 1px solid var(--border);
  border-radius: var(--radius); cursor: pointer; transition: all 0.12s;
}
.radio-inline:hover { background: var(--bg-2); }
.radio-inline.on { border-color: var(--brand); background: var(--ai-bg); }
.radio-inline input { accent-color: var(--brand); margin-top: 2px; }

.trial-card { position: sticky; top: 0; max-height: calc(100vh - 120px); overflow-y: auto; }
.sample-list {
  max-height: 220px; overflow-y: auto;
  border: 1px solid var(--border); border-radius: var(--radius);
}
.sample-row {
  display: flex; gap: 9px; padding: 8px 10px; cursor: pointer;
  border-bottom: 1px solid var(--border); transition: background 0.12s;
}
.sample-row:last-child { border-bottom: none; }
.sample-row:hover { background: var(--bg-2); }
.sample-row.on { background: var(--ai-bg); }

.trial-result {
  padding: 11px 12px; border: 1px solid var(--border);
  border-radius: var(--radius); margin-top: 10px; background: var(--bg-0);
}
.trial-ability {
  display: flex; align-items: center; gap: 8px;
  font-size: 12px; padding: 3px 0; border-bottom: 1px dashed var(--border);
}
.trial-ability:last-of-type { border-bottom: none; }

.save-bar {
  position: sticky; bottom: 0; margin-top: 14px;
  display: flex; align-items: center; gap: 14px;
  padding: 12px 16px; background: var(--bg-1);
  border: 1px solid var(--border-strong); border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg); flex-wrap: wrap;
}
</style>
