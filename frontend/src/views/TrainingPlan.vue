<template>
  <div v-if="p">
    <div class="card">
      <div class="card-head">
        <VButton variant="ghost" size="sm" @click="$router.push('/training')">← 返回</VButton>
        <span class="card-title">{{ p.title }}</span>
        <span class="tag" :class="p.status === '已发布' ? 'tag-ok' : 'tag-warn'">{{ p.status }}</span>
        <div class="ml-auto row">
          <VButton size="sm" :loading="regening" icon="⟳" @click="regenerate">
            重新生成
          </VButton>
          <VButton v-if="p.status !== '已发布'" size="sm" variant="ok" :loading="busy" @click="publish">
            确认并发布
          </VButton>
          <VButton size="sm" variant="ghost" @click="remove">删除</VButton>
        </div>
      </div>

      <div v-if="p.status !== '已发布'" class="alert alert-warn">
        <span class="alert-icon">⚠</span>
        <div>
          内容为<b>草稿</b>状态。发布前请确认：大纲章节与能力项一一对应、
          每道题的答案可溯源到知识库出处。发布后新人即可参加考核。
        </div>
      </div>
      <div v-else class="alert alert-ok">
        <span class="alert-icon">✓</span>
        <div>已发布。新人可在培训模块查看并参加考核。</div>
      </div>

      <VDegradeAlert
        :info="degradeInfo" spaced
        title="本方案的部分内容走了降级路径"
        hint="降级产生的大纲或题库可能与该岗位能力项不匹配，发布前请逐项核对。"
      />

      <!-- 方案设置一览：创建时填了名称、时间、限时、及格分，这里要能回看和修改，
           否则带教人只能记得住，新人更无从知道什么时候能考、考多久 -->
      <div class="plan-meta">
        <div class="meta-item">
          <span class="meta-k">培训时间</span>
          <span class="meta-v">
            {{ fmtDate(p.start_date) || '不限' }} ~ {{ fmtDate(p.end_date) || '不限' }}
          </span>
        </div>
        <div class="meta-item">
          <span class="meta-k">考试限时</span>
          <span class="meta-v">{{ p.exam_minutes || 60 }} 分钟</span>
        </div>
        <div class="meta-item">
          <span class="meta-k">及格分</span>
          <span class="meta-v">{{ p.pass_score ?? 60 }} 分</span>
        </div>
        <div class="meta-item">
          <span class="meta-k">培训对象</span>
          <span class="meta-v">{{ p.position_name || positionName || '—' }}</span>
        </div>
        <VButton size="sm" variant="ghost" class="ml-auto" @click="openSettings">修改设置</VButton>
      </div>
      <div v-if="p.description" class="plan-desc">{{ p.description }}</div>

      <!-- 修改设置：只改规则，不重新生成内容 -->
      <VModal v-if="settingsOpen" title="修改方案设置" @close="settingsOpen = false">
        <div class="grid grid-2">
          <div class="field">
            <label class="field-label">方案名称</label>
            <input v-model.trim="sform.title" class="input" maxlength="60" />
          </div>
          <div class="field">
            <label class="field-label">参数与新人可见说明</label>
            <input v-model.trim="sform.description" class="input" maxlength="200"
                   placeholder="选填，例如：面向交易研发部，含支付链路规范" />
          </div>
        </div>
        <div class="grid grid-4">
          <div class="field">
            <label class="field-label">培训开始</label>
            <input v-model="sform.start_date" type="date" class="input" />
          </div>
          <div class="field">
            <label class="field-label">培训结束</label>
            <input v-model="sform.end_date" type="date" class="input" />
          </div>
          <div class="field">
            <label class="field-label">考试限时（分钟）</label>
            <input v-model.number="sform.exam_minutes" type="number" min="10" max="180" class="input" />
          </div>
          <div class="field">
            <label class="field-label">及格分</label>
            <input v-model.number="sform.pass_score" type="number" min="0" max="100" class="input" />
          </div>
        </div>
        <div class="tiny muted">
          修改只影响时间与考试规则，不会重新生成大纲和题库。
          若已有新人考过，改动会记入决策日志。
        </div>
        <template #foot>
          <VButton :disabled="savingSettings" @click="settingsOpen = false">取消</VButton>
          <VButton variant="primary" :loading="savingSettings" @click="saveSettings">保存</VButton>
        </template>
      </VModal>
    </div>

    <div class="tabs">
      <button class="tab" :class="{ on: tab === 'outline' }" @click="tab = 'outline'">
        培训大纲（{{ p.outline?.length || 0 }}）
      </button>
      <button class="tab" :class="{ on: tab === 'exam' }" @click="tab = 'exam'">
        考核题库（{{ p.questions?.length || 0 }}）
      </button>
      <button class="tab" :class="{ on: tab === 'assign' }" @click="tab = 'assign'; loadAssign(); loadMaterials()">
        资料与指派
      </button>
      <button class="tab" :class="{ on: tab === 'results' }" @click="tab = 'results'">
        考核结果（{{ p.submissions?.length || 0 }}）
      </button>
    </div>

    <!-- 大纲 -->
    <div v-if="tab === 'outline'" class="card">
      <div class="card-head">
        <span class="card-title">培训大纲</span>
        <span class="card-desc">按能力项组织章节，学习材料只索引位置不复制正文，避免版本不一致</span>
      </div>
      <div v-for="(o, i) in p.outline" :key="i" class="chapter">
        <div class="row-between">
          <span class="bold">{{ o.chapter }}</span>
          <span class="tag tag-blue">{{ o.hours }} 学时</span>
        </div>
        <div class="row mt-1" style="gap: 10px">
          <span class="tiny muted">对应能力项：{{ o.ability_name }}</span>
        </div>
        <div v-if="o.material_ref" class="tiny mt-1" style="color: var(--brand-text)">
          学习材料：{{ o.material_ref }}
        </div>
      </div>

      <div v-if="needsAnswer.length" class="alert alert-warn mt-3">
        <span class="alert-icon">⚠</span>
        <div>
          有 {{ needsAnswer.length }} 道题缺少知识库出处，需人工补充答案后再发布。
        </div>
      </div>
    </div>

    <!-- 题库 -->
    <div v-if="tab === 'exam'" class="card">
      <div class="card-head">
        <span class="card-title">考核题库</span>
        <span class="card-desc">客观题规则自动判，主观题 AI 只给要点覆盖分析</span>
        <VButton size="sm" class="ml-auto" icon="✎" @click="takeOpen = true">模拟作答</VButton>
      </div>

      <div v-for="(q, i) in p.questions" :key="q.id" class="q-item">
        <div class="row-between">
          <div class="row" style="gap: 7px">
            <span class="tag tag-gray">{{ q.qtype }}</span>
            <span class="tag tag-blue">{{ q.difficulty }}</span>
            <span class="tag tag-purple tiny">{{ q.ability_name || '未关联' }}</span>
          </div>
          <span class="tiny muted">{{ q.full_score }} 分</span>
        </div>
        <div class="mt-1" style="font-size: 13px; line-height: 1.75">
          {{ i + 1 }}. {{ q.stem }}
        </div>
        <div v-if="q.options?.length" class="options">
          <div v-for="(o, oi) in q.options" :key="oi" class="opt">{{ o }}</div>
        </div>
        <div class="row wrap mt-1" style="gap: 10px">
          <span class="tiny"><b>答案：</b>{{ q.answer || '待补充' }}</span>
          <span v-if="q.ref" class="tiny" style="color: var(--brand-text)">
            <b>出处：</b>{{ q.ref }}
          </span>
          <span v-else class="tag tag-warn tiny">无出处，需人工补充</span>
        </div>
      </div>

      <div v-if="!p.questions?.length" class="empty">
        <div class="empty-title">该方案没有题目</div>
        <div class="empty-desc">生成方案时可勾选「同时生成考核题库」。</div>
      </div>
    </div>

    <!-- 资料与指派 -->
    <div v-if="tab === 'assign'">
      <div class="card">
        <div class="card-head">
          <span class="card-title">公司资料</span>
          <span class="card-desc">上传后 AI 会优先依据这些资料出题，比通用知识库更贴合实际</span>
          <VButton size="sm" variant="primary" class="ml-auto" icon="↑" @click="matOpen = true">
            上传资料
          </VButton>
        </div>
        <VState :items="materials" :loading="matLoading" title="还没有上传公司资料" icon="▤"
                desc="建议上传岗位 SOP、技术规范、内部培训材料等。没有资料时 AI 会退回通用知识库出题，可能与企业实际做法脱节。">
          <table class="table">
            <thead>
              <tr><th>资料</th><th style="width:110px">字数</th><th style="width:90px">片段</th>
              <th style="width:110px">上传时间</th><th style="width:70px"></th></tr>
            </thead>
            <tbody>
              <tr v-for="m in materials" :key="m.id">
                <td>
                  <div class="bold small">{{ m.title }}</div>
                  <div class="tiny muted truncate" style="max-width: 420px">{{ m.preview }}</div>
                </td>
                <td class="num">{{ m.char_count }}</td>
                <td class="num">{{ m.chunk_count }}</td>
                <td class="tiny muted">{{ fmtDate(m.created_at) }}</td>
                <td><VButton size="sm" variant="ghost" @click="removeMaterial(m)">删除</VButton></td>
              </tr>
            </tbody>
          </table>
        </VState>
      </div>

      <div class="card mt-3">
        <div class="card-head">
          <span class="card-title">指派给新人</span>
          <span class="card-desc">只有被指派的新人才能看到并参加这场考核</span>
        </div>
        <div v-if="p.status !== '已发布'" class="alert alert-warn mb-2">
          <span class="alert-icon">⚠</span>
          <div>方案尚未发布，需先在顶部「确认并发布」后才能指派给新人。</div>
        </div>
        <VState :items="newcomers" :loading="ncLoading" title="还没有新人账号" icon="◉"
                desc="新人账号可由 admin 在「账号管理」中创建，或在登录页自助注册（角色选「新人」需要管理员创建）。">
          <div class="assign-grid">
            <div class="field mb-0">
              <label class="field-label">选择新人<span class="req">*</span></label>
              <MultiSelect
                v-model="picked"
                :items="assignable"
                label-key="name"
                sub-key="userid"
                placeholder="点击选择，可搜索姓名或账号"
                search-placeholder="搜索姓名或账号…"
                empty-text="没有可指派的新人"
              />
              <div v-if="alreadyAssignedIds.length" class="field-hint">
                已在名单中的 {{ alreadyAssignedIds.length }} 人默认隐藏，
                <button class="link-inline" type="button" @click="includeAssigned = !includeAssigned">
                  {{ includeAssigned ? '隐藏他们' : '显示全部' }}
                </button>
              </div>
            </div>

            <div class="field mb-0">
              <label class="field-label">截止天数</label>
              <input v-model.number="assignForm.due_days" type="number" min="1" class="input" />
              <div class="field-hint">从今天算起，逾期会标红提醒</div>
            </div>

            <div class="field mb-0">
              <label class="field-label">考试次数上限</label>
              <input v-model.number="assignForm.max_attempts" type="number" min="1" max="10" class="input" />
              <div class="field-hint">建议 2 至 3 次，留一次失误机会</div>
            </div>
          </div>

          <div class="row mt-3" style="gap: 10px">
            <VButton variant="primary"
                     :disabled="!picked.length || p.status !== '已发布'"
                     :loading="acting" @click="doAssign">
              指派给 {{ picked.length }} 人
            </VButton>
            <VButton v-if="picked.length" variant="ghost" @click="picked = []">取消选择</VButton>
            <span v-if="p.status !== '已发布'" class="tiny" style="color: var(--warn)">
              方案未发布，无法指派
            </span>
          </div>

          <div v-if="assignments.length" class="mt-3">
            <div class="row-between mb-1">
              <span class="tiny muted">已指派名单（{{ assignments.length }}）</span>
              <span class="tiny muted">
                已开始 {{ assignments.filter(a => a.used_attempts > 0).length }} 人 ·
                已通过 {{ assignments.filter(a => a.status === '已通过').length }} 人
              </span>
            </div>
            <table class="table">
              <thead>
                <tr>
                  <th>新人</th><th style="width:90px">状态</th>
                  <th style="width:80px">已用/上限</th><th style="width:110px">截止</th>
                  <th style="width:70px"></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="a in assignments" :key="a.id">
                  <td>
                    <span class="small bold">{{ a.name }}</span>
                    <span class="mono tiny muted"> {{ a.userid }}</span>
                  </td>
                  <td><span class="tag" :class="assignStatusTag(a.status)">{{ a.status }}</span></td>
                  <td class="num">{{ a.used_attempts }}/{{ a.max_attempts }}</td>
                  <td class="tiny muted">{{ a.due_at ? fmtDate(a.due_at) : '—' }}</td>
                  <td><VButton size="sm" variant="ghost" @click="removeAssignment(a)">撤销</VButton></td>
                </tr>
              </tbody>
            </table>
          </div>
        </VState>
      </div>
    </div>

    <!-- 结果 -->
    <div v-if="tab === 'results'" class="card">
      <div class="card-head">
        <span class="card-title">考核结果</span>
        <span class="card-desc">主观题终评分数由带教人给出，AI 不提供终评</span>
      </div>

      <div v-for="s in p.submissions" :key="s.id" class="sub-item">
        <div class="row-between">
          <div class="row" style="gap: 9px">
            <span class="bold">{{ s.trainee_name }}</span>
            <span v-if="s.mentor_confirmed" class="tag tag-ok">已确认</span>
            <span v-else class="tag tag-warn">待带教人终评</span>
          </div>
          <span class="tiny muted">{{ fmtTime(s.created_at) }}</span>
        </div>

        <div class="grid grid-2 mt-2">
          <div>
            <div class="tiny muted mb-1">客观题得分（规则自动判）</div>
            <div class="bold num" style="font-size: 19px">
              {{ s.objective_score }}<span class="muted" style="font-size: 13px">/{{ s.objective_full }}</span>
            </div>
            <div v-if="s.final_score !== null && s.final_score !== undefined" class="mt-1">
              <div class="tiny muted">带教人终评</div>
              <div class="bold num" style="font-size: 19px; color: var(--brand-text)">{{ s.final_score }}</div>
            </div>
          </div>
          <div>
            <div class="tiny muted mb-1">能力雷达（维度即能力项）</div>
            <div v-for="(v, k) in (s.radar || {})" :key="k" class="radar-row">
              <span class="tiny truncate" style="flex: 1">{{ k }}</span>
              <div class="bar" style="width: 70px"><div class="bar-fill" :style="{ width: v + '%' }" /></div>
              <span class="tiny num" style="width: 32px; text-align: right">{{ v }}</span>
            </div>
          </div>
        </div>

        <!-- 主观题要点覆盖分析 -->
        <div v-if="subjective(s).length" class="mt-2">
          <div class="ai-block">
            <div class="ai-label">◆ AI 要点覆盖分析（不给终评分数）</div>
            <div v-for="(j, i) in subjective(s)" :key="i" class="mt-2">
              <div class="row" style="gap: 6px">
                <span class="tag tag-gray tiny">{{ j.qtype }}</span>
                <span class="tiny muted">题号 {{ j.question_id }}</span>
              </div>
              <div v-if="j.hit_points?.length" class="mt-1">
                <span class="tiny bold" style="color: var(--ok)">命中要点：</span>
                <span v-for="(h, hi) in j.hit_points" :key="hi" class="tag tag-ok tiny" style="margin: 2px 3px 0 0">{{ h }}</span>
              </div>
              <div v-if="j.miss_points?.length" class="mt-1">
                <span class="tiny bold" style="color: var(--warn)">缺失要点：</span>
                <span v-for="(m, mi) in j.miss_points" :key="mi" class="tag tag-warn tiny" style="margin: 2px 3px 0 0">{{ m }}</span>
              </div>
              <div v-if="j.comment" class="tiny mt-1">{{ j.comment }}</div>
            </div>
          </div>
        </div>

        <div class="row mt-2" style="gap: 8px">
          <template v-if="!s.mentor_confirmed">
            <input v-model.number="finalScore[s.id]" type="number" min="0" max="100"
                   class="input" style="width: 110px" placeholder="终评分数" />
            <VButton size="sm" variant="primary" @click="confirmFinal(s)">确认终评</VButton>
          </template>
          <template v-else>
            <VButton size="sm" @click="writeProbation(s, true)">试用期通过</VButton>
            <VButton size="sm" variant="ghost" @click="writeProbation(s, false)">试用期未通过</VButton>
            <span class="tiny muted">回写至 M5，用于季度权重复盘</span>
          </template>
        </div>
      </div>

      <div v-if="!p.submissions?.length" class="empty">
        <div class="empty-title">还没有考核记录</div>
        <div class="empty-desc">新人提交考核后会在这里显示判卷结果与能力雷达图。</div>
      </div>
    </div>

    <!-- 模拟作答 -->
    <VModal v-if="takeOpen" title="模拟作答与判卷" wide @close="takeOpen = false">
      <div class="field">
        <label class="field-label">作答人姓名</label>
        <input v-model="trainee" class="input" placeholder="例如：新入职的李明" />
      </div>
      <div v-for="(q, i) in p.questions" :key="q.id" class="mt-2">
        <div class="small bold">{{ i + 1 }}. {{ q.stem }}</div>
        <div v-if="q.options?.length" class="tiny muted mt-1">{{ q.options.join('　') }}</div>
        <input v-model="answers[String(q.id)]" class="input mt-1"
               :placeholder="q.qtype === '单选' ? '填写选项字母，如 A' : q.qtype === '多选' ? '填写选项，如 ABD' : '填写作答内容'" />
      </div>
      <template #foot>
        <VButton @click="takeOpen = false">取消</VButton>
        <VButton variant="primary" :disabled="!trainee" :loading="busy" @click="doSubmit">提交并判卷</VButton>
      </template>
    </VModal>

    <VModal v-if="matOpen" title="上传公司资料" @close="matOpen = false">
      <div class="field">
        <label class="field-label">资料标题</label>
        <input v-model="matForm.title" class="input" placeholder="例如：支付链路研发规范 V2.1" />
      </div>
      <div class="field">
        <label class="field-label">文件</label>
        <input ref="matFile" type="file" class="input"
               accept=".pdf,.docx,.doc,.txt,.md,.csv,.xlsx" />
        <div class="field-hint">支持 PDF / Word / 纯文本 / Markdown / Excel，最大 30MB</div>
      </div>
      <div class="alert alert-info">
        <span class="alert-icon">◆</span>
        <div>
          上传后重新生成方案，AI 会优先从这份资料里出题。
          若资料中直接写着「答案：B」这类内容，会提示你先清理 —— 那会显著拉低题目质量。
        </div>
      </div>
      <template #foot>
        <VButton @click="matOpen = false">取消</VButton>
        <VButton variant="primary" :loading="acting" @click="uploadMaterial">上传</VButton>
      </template>
    </VModal>

    <VModal v-if="judgeResult" title="判卷结果" wide @close="judgeResult = null">
      <div class="alert alert-info mb-2">
        <span class="alert-icon">◆</span>
        <div>{{ judgeResult.note }}</div>
      </div>
      <div class="row-between mb-2">
        <span>客观题得分</span>
        <span class="bold num" style="font-size: 18px">
          {{ judgeResult.objective_score }} / {{ judgeResult.objective_full }}
        </span>
      </div>
      <div v-for="(j, i) in judgeResult.items" :key="i" class="judge-row">
        <div class="row-between">
          <span class="tag tag-gray tiny">{{ j.qtype }}</span>
          <span v-if="j.method?.includes('规则')" class="tag tag-ok tiny">规则自动判</span>
          <span v-else class="tag tag-ai tiny">AI 要点分析</span>
        </div>
        <div class="tiny mt-1">
          <template v-if="j.method?.includes('规则')">
            得分 {{ j.score }} / {{ j.full_score }} {{ j.comment }}
          </template>
          <template v-else>
            命中 {{ j.hit_points?.length || 0 }} 个要点，缺失 {{ j.miss_points?.length || 0 }} 个
          </template>
        </div>
      </div>
      <template #foot>
        <VButton variant="primary" @click="judgeResult = null; load()">知道了</VButton>
      </template>
    </VModal>
  </div>

  <VState v-else :items="[]" :loading="loading" title="方案不存在" icon="✕" />
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import VButton from '../components/VButton.vue'
import VModal from '../components/VModal.vue'
import VState from '../components/VState.vue'
import MultiSelect from '../components/MultiSelect.vue'
import VDegradeAlert from '../components/VDegradeAlert.vue'
import { examApi, planApi, trainingApi } from '../api'
import { fmtDate, fmtTime } from '../api/labels'
import { toast } from '../components/toast'

const route = useRoute()
const pid = Number(route.params.id)

const p = ref<any>(null)
const loading = ref(false)
const busy = ref(false)
const tab = ref('outline')
const judgeResult = ref<any>(null)
const takeOpen = ref(false)
const trainee = ref('')
const answers = ref<Record<string, string>>({})
const finalScore = ref<Record<number, number>>({})

/* ---------- 方案设置（时间与考试规则） ---------- */
const settingsOpen = ref(false)
const savingSettings = ref(false)
const sform = ref({
  title: '', description: '', start_date: '', end_date: '',
  exam_minutes: 60, pass_score: 60,
})
const positionName = ref('')

// <input type="date"> 只认 YYYY-MM-DD，后端给的是带时间的字符串
function toDateInput(v: any): string {
  if (!v) return ''
  return String(v).slice(0, 10)
}

function openSettings() {
  const d = p.value || {}
  sform.value = {
    title: d.title || '',
    description: d.description || '',
    start_date: toDateInput(d.start_date),
    end_date: toDateInput(d.end_date),
    exam_minutes: d.exam_minutes ?? 60,
    pass_score: d.pass_score ?? 60,
  }
  settingsOpen.value = true
}

async function saveSettings() {
  savingSettings.value = true
  try {
    await planApi.update(pid, { ...sform.value })
    toast.ok('设置已保存')
    settingsOpen.value = false
    await load()
  } catch (e) {
    toast.err(e, '保存失败')
  } finally {
    savingSettings.value = false
  }
}

const needsAnswer = computed(() => (p.value?.questions || []).filter((q: any) => !q.ref))

/** 降级信息来自方案自身的 ai_meta，因此刷新页面后依然可见 */
const degradeInfo = computed(() => {
  const m = p.value?.ai_meta || {}
  const exam = m.exam_meta || {}
  if (m.degraded || exam.degraded) {
    return {
      reason: m.degrade_reason || exam.degrade_reason || '生成过程中有一步走了降级路径',
      level: m.degrade || exam.degrade || 'human',
      model: m.model || exam.model || '',
    }
  }
  return null
})

function subjective(s: any) {
  return (s.judge?.items || []).filter((j: any) => j.method && !j.method.includes('规则'))
}

async function load() {
  loading.value = true
  try { p.value = await trainingApi.get(pid) } catch (e) { toast.err(e); p.value = null }
  finally { loading.value = false }
}

async function publish() {
  busy.value = true
  try {
    const r = await trainingApi.publish(pid)
    toast.ok(r.message)
    await load()
  } catch (e: any) {
    // 缺出处时后端会拒绝，提示是否接受继续
    if (String(e?.friendly || '').includes('缺少知识库出处')) {
      if (confirm(`${e.friendly}\n\n是否仍然发布？`)) {
        try {
          const r = await trainingApi.publish(pid, true)
          toast.ok(r.message)
          await load()
        } catch (e2) { toast.err(e2) }
      }
    } else {
      toast.err(e)
    }
  } finally {
    busy.value = false
  }
}

async function regenerate() {
  const msg = '用当前的资料与能力项重新生成大纲与题库？\n\n'
    + '方案 ID 与指派关系会保留，但题目会变。\n'
    + '若已有作答记录，新旧成绩将不可比。'
  if (!confirm(msg)) return
  regening.value = true
  try {
    const r = await examApi.regenerate(pid, true)
    toast.ok(r.message, '现在的大纲只包含可培训的能力项')
    await load()
  } catch (e) {
    toast.err(e, '重新生成失败')
  } finally {
    regening.value = false
  }
}

async function remove() {
  if (!confirm('确定删除该培训方案？此操作不可撤销。')) return
  try {
    await trainingApi.remove(pid)
    toast.ok('已删除')
    history.back()
  } catch (e) { toast.err(e) }
}

async function doSubmit() {
  busy.value = true
  try {
    const r = await trainingApi.submit(pid, { trainee_name: trainee.value, answers: answers.value })
    judgeResult.value = r
    takeOpen.value = false
  } catch (e) {
    toast.err(e, '判卷失败')
  } finally {
    busy.value = false
  }
}

async function confirmFinal(s: any) {
  const v = finalScore.value[s.id]
  if (v === undefined || v === null || v === '') { toast.warn('请填写终评分数'); return }
  try {
    await trainingApi.confirm(s.id, Number(v))
    toast.ok('终评已确认，成绩已回流至 M5')
    await load()
  } catch (e) { toast.err(e) }
}

async function writeProbation(s: any, passed: boolean) {
  try {
    const r = await trainingApi.probation(s.id, { passed })
    toast.ok(r.message)
  } catch (e) { toast.err(e) }
}

/* ---------- 资料与指派 ---------- */
const materials = ref<any[]>([])
const matLoading = ref(false)
const matOpen = ref(false)
const regening = ref(false)
const matFile = ref<HTMLInputElement | null>(null)
const matForm = ref({ title: '' })
const newcomers = ref<any[]>([])
const ncLoading = ref(false)
const picked = ref<number[]>([])
const includeAssigned = ref(false)

// 已指派的人默认从候选里去掉 —— 否则勾了也只会被"跳过"，
// 用户以为是成功的，实际什么都没发生。
const alreadyAssignedIds = computed(() => assignments.value.map((a: any) => a.user_id))
const assignable = computed(() => includeAssigned.value
  ? newcomers.value
  : newcomers.value.filter((n: any) => !alreadyAssignedIds.value.includes(n.id)))
const assignments = ref<any[]>([])
const assignForm = ref({ due_days: 7, max_attempts: 1 })

const acting = ref(false)

/** 撤销某个新人的指派 */
async function removeAssignment(a: any) {
  if (!confirm(`确定撤销对「${a.name}」的指派？

撤销后该新人将看不到这场考核，`
    + `已有的作答记录会保留。`)) return
  try {
    await examApi.deleteAssignment(a.id)
    toast.ok('已撤销指派')
    await loadAssign()
  } catch (e) {
    toast.err(e, '撤销失败')
  }
}

/** 指派状态对应的标签配色 */
function assignStatusTag(s: string) {
  return {
    待开始: 'tag-blue', 进行中: 'tag-warn', 已完成: 'tag-gray',
    已通过: 'tag-ok', 已过期: 'tag-danger',
  }[s] || 'tag-gray'
}

async function loadMaterials() {
  matLoading.value = true
  try { materials.value = await examApi.materials() } catch (e) { toast.err(e) } finally { matLoading.value = false }
}

async function uploadMaterial() {
  const f = matFile.value?.files?.[0]
  if (!f) { toast.warn('请选择文件'); return }
  acting.value = true
  try {
    const fd = new FormData()
    fd.append('file', f)
    fd.append('title', matForm.value.title || f.name)
    fd.append('plan_id', '0')
    const r = await examApi.uploadMaterial(fd)
    toast.ok(r.message, r.warning || '')
    matOpen.value = false
    matForm.value = { title: '' }
    if (matFile.value) matFile.value.value = ''
    await loadMaterials()
  } catch (e) {
    toast.err(e, '上传失败')
  } finally {
    acting.value = false
  }
}

async function removeMaterial(m: any) {
  if (!confirm(`确定删除资料「${m.title}」？`)) return
  try {
    await examApi.deleteMaterial(m.id)
    toast.ok('已删除')
    await loadMaterials()
  } catch (e) { toast.err(e) }
}

async function loadAssign() {
  ncLoading.value = true
  try {
    newcomers.value = await examApi.newcomers()
    assignments.value = await examApi.assignments(pid)
  } catch (e) { toast.err(e) } finally { ncLoading.value = false }
}

async function doAssign() {
  acting.value = true
  try {
    const r = await examApi.assign({
      plan_id: pid, user_ids: picked.value,
      due_days: assignForm.value.due_days,
      max_attempts: assignForm.value.max_attempts,
    })
    toast.ok(r.message)
    picked.value = []
    await loadAssign()
  } catch (e) {
    toast.err(e, '指派失败')
  } finally {
    acting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
/* 方案设置一览条：把创建时填的时间与考试规则摊开显示 */
.plan-meta {
  display: flex; align-items: center; gap: 22px; flex-wrap: wrap;
  margin-top: 12px; padding: 11px 14px;
  background: var(--bg-soft, rgba(127, 127, 127, 0.06));
  border: 1px solid var(--border); border-radius: 9px;
}
.meta-item { display: flex; flex-direction: column; gap: 3px; }
.meta-k { font-size: 11px; color: var(--text-muted); }
.meta-v { font-size: 13px; font-weight: 600; }
.plan-desc {
  margin-top: 9px; padding: 9px 13px; font-size: 13px;
  color: var(--text-muted); border-left: 3px solid var(--border);
}
.tabs { display: flex; gap: 4px; margin: 14px 0; border-bottom: 1px solid var(--border); }
.tab {
  padding: 8px 15px; background: none; border: none; cursor: pointer;
  color: var(--text-1); font-size: 13px; font-family: inherit;
  border-bottom: 2px solid transparent; margin-bottom: -1px;
}
.tab:hover { color: var(--text-0); }
.tab.on { color: var(--brand-text); border-bottom-color: var(--brand); font-weight: 570; }

.chapter {
  padding: 11px 13px; border: 1px solid var(--border);
  border-radius: var(--radius); margin-bottom: 9px; background: var(--bg-0);
}
.q-item {
  padding: 12px 13px; border: 1px solid var(--border);
  border-radius: var(--radius); margin-bottom: 10px; background: var(--bg-0);
}
.options { margin-top: 7px; display: flex; flex-direction: column; gap: 3px; }
.opt { font-size: 12.5px; color: var(--text-1); padding-left: 12px; }
.sub-item {
  padding: 13px; border: 1px solid var(--border);
  border-radius: var(--radius); margin-bottom: 12px; background: var(--bg-0);
}
.radar-row { display: flex; align-items: center; gap: 7px; padding: 2px 0; }
.judge-row { padding: 8px 0; border-bottom: 1px dashed var(--border); }
.judge-row:last-child { border-bottom: none; }

/* 指派区：下拉占主列，两个数值参数并排在右 */
.assign-grid {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) 130px 150px;
  gap: 14px; align-items: start;
}
@media (max-width: 900px) { .assign-grid { grid-template-columns: 1fr; } }

.link-inline {
  background: none; border: none; color: var(--brand-text);
  font-size: inherit; cursor: pointer; padding: 0;
  font-family: inherit; text-decoration: underline;
}
</style>
