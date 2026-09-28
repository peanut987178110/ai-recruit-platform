<template>
  <div>
    <div class="card">
      <div class="card-head">
        <VButton variant="ghost" size="sm" @click="$router.push('/interviews')">← 返回</VButton>
        <span class="card-title">{{ d.candidate?.name }}</span>
        <span class="card-desc">{{ d.position_name }} · {{ d.round_name }}</span>
        <span v-if="d.tier" class="tag" :class="tierColor(d.tier)">{{ d.tier }}</span>
        <div class="ml-auto row">
          <select v-model.number="planMinutes" class="select" style="width: 130px">
            <option :value="30">30 分钟</option>
            <option :value="60">60 分钟</option>
          </select>
          <VButton size="sm" variant="primary" :loading="genning" icon="✦" @click="generate">
            {{ d.questions?.length ? '重新生成' : '生成题目' }}
          </VButton>
          <VButton size="sm" @click="$router.push(`/interview/${sid}/report`)">评估报告</VButton>
        </div>
      </div>

      <div class="alert alert-info">
        <span class="alert-icon">◆</span>
        <div>
          出题输入是简历与能力模型的<b>差集</b>：优先针对简历中未体现或部分命中的能力项出题，
          已充分证明的能力项最多生成 1 道确认题，避免面试时间浪费在已知信息上。
          题目按时间预算自动取舍。
        </div>
      </div>

      <VDegradeAlert
        :info="degradeInfo" spaced
        title="本次题目是降级结果，不是针对该候选人生成的。"
        hint="下面是按能力项套用的通用题模板，不能直接用于面试，请手动改写或稍后重试生成。"
      />
    </div>

    <div class="prep-grid">
      <!-- 左：能力缺口 -->
      <div class="card">
        <div class="card-head">
          <span class="card-title">能力项缺口</span>
          <span class="tag tag-gray ml-auto">{{ d.gaps?.length || 0 }} 项待验证</span>
        </div>
        <div class="tiny muted mb-2">按权重降序，权重高的更需要面试验证。</div>

        <div v-for="g in d.gaps" :key="g.ability_id" class="gap-row">
          <div class="row-between">
            <span class="small bold">{{ g.ability_name }}</span>
            <span class="tag" :class="stateColor(g.state)">{{ STATE_LABEL[g.state] }}</span>
          </div>
          <div class="row-between mt-1">
            <span class="tiny muted">权重 {{ g.weight * 10 }}%</span>
            <span class="tiny dim">{{ g.hint }}</span>
          </div>
        </div>

        <div v-if="!d.gaps?.length" class="empty" style="padding: 24px 10px">
          <div class="empty-title">没有明显缺口</div>
          <div class="empty-desc">简历已充分覆盖各项能力，题目将转为确认与深挖。</div>
        </div>
      </div>

      <!-- 右：题目 -->
      <div class="card">
        <div class="card-head">
          <span class="card-title">面试题目</span>
          <span class="tag tag-gray">{{ d.questions?.length || 0 }} 道</span>
          <span v-if="totalMin" class="tag tag-blue">预计 {{ totalMin }} 分钟</span>
          <span class="tiny muted ml-auto">面试官操作即采纳率来源</span>
        </div>

        <div v-if="blocked.length" class="alert alert-warn mb-2">
          <span class="alert-icon">⚠</span>
          <div>已拦截 {{ blocked.length }} 道涉及敏感话题的题目（整条丢弃并记录日志）。</div>
        </div>

        <VState :items="d.questions || []" :loading="genning" title="还没有题目" icon="◷"
                desc="点击右上角「生成题目」，系统会按能力缺口与时间预算自动出题。">
          <div v-for="layer in LAYERS" :key="layer" class="layer-block">
            <div v-if="byLayer(layer).length" class="layer-head">
              <span class="layer-name">{{ layer }}</span>
              <span class="tiny muted">{{ byLayer(layer).length }} 道</span>
            </div>
            <div v-for="q in byLayer(layer)" :key="q.id" class="q-card"
                 :class="{ deleted: q.action === '删除', adopted: q.action === '采纳' }">
              <div class="row-between">
                <span class="tag tag-gray tiny">{{ q.ability_name || '未关联' }}</span>
                <span class="tiny muted">{{ q.duration_min }} 分钟</span>
              </div>

              <div v-if="q.action === '编辑' && editing === q.id" class="mt-1">
                <textarea v-model="editText" class="textarea" rows="2" />
                <div class="row mt-1">
                  <VButton size="sm" variant="primary" @click="saveEdit(q)">保存</VButton>
                  <VButton size="sm" @click="editing = null">取消</VButton>
                </div>
              </div>
              <div v-else class="q-content" @click="toggle(q.id)">
                {{ q.action === '编辑' && q.edited_content ? q.edited_content : q.content }}
              </div>

              <div v-if="expanded === q.id">
                <div v-if="q.basis" class="q-basis">
                  <span class="tiny bold">出题依据</span>
                  <div class="tiny">{{ q.basis }}</div>
                </div>
                <div v-if="q.anchors && Object.keys(q.anchors).length" class="anchors">
                  <div v-for="(v, k) in q.anchors" :key="k" class="anchor-row">
                    <span class="tag tiny" :class="k === '高' ? 'tag-ok' : k === '中' ? 'tag-warn' : 'tag-gray'">
                      {{ k }}档
                    </span>
                    <span class="tiny">{{ v }}</span>
                  </div>
                </div>
                <div v-if="q.probe" class="tiny muted mt-1">追问建议：{{ q.probe }}</div>
              </div>

              <div class="row mt-2" style="gap: 6px">
                <VButton size="sm" :variant="q.action === '采纳' ? 'ok' : 'default'"
                         @click.stop="act(q, '采纳')">
                  {{ q.action === '采纳' ? '✓ 已采纳' : '采纳' }}
                </VButton>
                <VButton size="sm" @click.stop="startEdit(q)">编辑</VButton>
                <VButton size="sm" variant="ghost" @click.stop="act(q, '删除')">删除</VButton>
                <button class="link-btn ml-auto" @click.stop="toggle(q.id)">
                  {{ expanded === q.id ? '收起' : '展开锚点' }}
                </button>
              </div>
            </div>
          </div>
        </VState>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import VButton from '../components/VButton.vue'
import VState from '../components/VState.vue'
import VDegradeAlert from '../components/VDegradeAlert.vue'
import { interviewApi } from '../api'
import { STATE_LABEL, stateColor, tierColor } from '../api/labels'
import { toast } from '../components/toast'

const route = useRoute()
const sid = Number(route.params.id)

const d = ref<any>({})
const genning = ref(false)
const planMinutes = ref(30)
const expanded = ref<number | null>(null)
const editing = ref<number | null>(null)
const editText = ref('')
const blocked = ref<any[]>([])
const degradeInfo = ref<{reason:string;level:string;model:string}|null>(null)

const LAYERS = ['基础考察', '项目深挖', '压力追问', '真实性验证']
const totalMin = computed(() => (d.value.questions || []).reduce((s: number, q: any) => s + (q.duration_min || 0), 0))

function byLayer(l: string) {
  return (d.value.questions || []).filter((q: any) => q.layer === l)
}

async function load() {
  try {
    d.value = await interviewApi.prepare(sid, planMinutes.value)
  } catch (e) {
    toast.err(e, '加载失败')
  }
}

async function generate() {
  genning.value = true
  try {
    const r = await interviewApi.generate(sid, planMinutes.value)
    blocked.value = r.blocked || []
    d.value.questions = r.questions
    d.value.total_minutes = r.total_minutes

    // 降级状态必须留在页面上，不能只弹一个 5 秒就消失的提示。
    // 否则面试官拿到的是一套通用模板题，却以为它是针对这位候选人生成的 ——
    // 这正好是 PRD 4.3「禁止静默降级」要防的事。
    const m = r.meta || {}
    if (m.degraded) {
      degradeInfo.value = {
        reason: m.degrade_reason || '题目生成失败，已降级为按能力项的通用模板',
        level: m.degrade || 'human',
        model: m.model || '',
      }
      toast.warn('题目生成失败，已降级为通用模板', '请查看页面上方的降级说明')
    } else {
      degradeInfo.value = null
      toast.ok(`已生成 ${r.questions.length} 道题，预计 ${r.total_minutes} 分钟`)
    }
  } catch (e) {
    toast.err(e, '生成失败')
  } finally {
    genning.value = false
  }
}

function toggle(id: number) { expanded.value = expanded.value === id ? null : id }

function startEdit(q: any) { editing.value = q.id; editText.value = q.edited_content || q.content }

async function saveEdit(q: any) {
  try {
    await interviewApi.questionAction(q.id, '编辑', editText.value)
    q.action = '编辑'
    q.edited_content = editText.value
    editing.value = null
    toast.ok('已保存修改')
  } catch (e) { toast.err(e) }
}

async function act(q: any, action: string) {
  try {
    await interviewApi.questionAction(q.id, action)
    if (action === '删除') q.action = '删除'
    else q.action = action
    toast.ok(action === '采纳' ? '已采纳，该题会计入采纳率' : '已删除')
  } catch (e) {
    toast.err(e)
  }
}

onMounted(() => load())
</script>

<style scoped>
.prep-grid { display: grid; grid-template-columns: 340px 1fr; gap: 14px; margin-top: 14px; align-items: start; }
@media (max-width: 1100px) { .prep-grid { grid-template-columns: 1fr; } }

.gap-row {
  padding: 9px 11px; border: 1px solid var(--border);
  border-radius: var(--radius); margin-bottom: 7px; background: var(--bg-0);
}
.layer-block { margin-bottom: 14px; }
.layer-head {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 0; border-bottom: 1px solid var(--border); margin-bottom: 9px;
}
.layer-name { font-size: 12.5px; font-weight: 620; color: var(--brand-text); }

.q-card {
  padding: 11px 12px; border: 1px solid var(--border);
  border-radius: var(--radius); margin-bottom: 9px; background: var(--bg-0);
  transition: all 0.13s;
}
.q-card:hover { border-color: var(--border-strong); }
.q-card.adopted { border-left: 3px solid var(--ok); }
.q-card.deleted { opacity: 0.45; }
.q-content {
  margin-top: 7px; font-size: 13px; line-height: 1.75; cursor: pointer;
}
.q-basis {
  margin-top: 9px; padding: 8px 10px; background: var(--ai-bg);
  border-left: 3px solid var(--brand); border-radius: var(--radius);
}
.anchors { margin-top: 8px; display: flex; flex-direction: column; gap: 5px; }
.anchor-row { display: flex; gap: 8px; align-items: flex-start; line-height: 1.65; }
.link-btn {
  background: none; border: none; color: var(--brand-text);
  font-size: 11.5px; cursor: pointer; padding: 0; font-family: inherit;
}
.link-btn:hover { text-decoration: underline; }
</style>
