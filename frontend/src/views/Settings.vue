<template>
  <div v-if="forbidden" class="card">
    <div class="empty" style="padding: 60px 20px">
      <div class="empty-icon">🔒</div>
      <div class="empty-title">当前角色无权访问此页</div>
      <div class="empty-desc">
        阈值与参数配置仅对 <b>HR 负责人</b>、<b>法务审计</b>、<b>系统管理员</b> 开放。<br />
        这不只是界面限制 —— 数据可见范围在查询层强制隔离，直接访问接口同样会被拒绝。<br /><br />
        如果想体验这一页，请用左下角的下拉框把身份切换为「HR 负责人」或「系统管理员」。
      </div>
      <div class="row mt-3" style="justify-content: center; gap: 10px">
        <VButton variant="primary" @click="$router.push('/workbench')">返回候选人工作台</VButton>
        <VButton @click="switchToAdmin">切换到系统管理员</VButton>
      </div>
    </div>
  </div>

  <div v-else>
    <div class="tabs">
      <button class="tab" :class="{ on: tab === 'threshold' }" @click="tab = 'threshold'">阈值与参数</button>
      <button class="tab" :class="{ on: tab === 'gateway' }" @click="tab = 'gateway'">模型网关</button>
      <button class="tab" :class="{ on: tab === 'model' }" @click="tab = 'model'">模型路由</button>
      <button class="tab" :class="{ on: tab === 'prompt' }" @click="tab = 'prompt'">提示词管理</button>
      <button class="tab" :class="{ on: tab === 'knowledge' }" @click="tab = 'knowledge'">知识库</button>
      <button class="tab" :class="{ on: tab === 'log' }" @click="tab = 'log'">日志与审计</button>
      <button class="tab" :class="{ on: tab === 'role' }" @click="tab = 'role'">权限矩阵</button>
    </div>

    <!-- 阈值 -->
    <div v-if="tab === 'threshold'" class="card">
      <div class="card-head">
        <span class="card-title">阈值与参数配置</span>
        <span class="card-desc">调整即时生效毋需发版；每次调整记录操作人、前后值与生效时间</span>
        <VButton size="sm" variant="primary" class="ml-auto" :loading="saving" :disabled="!dirty" @click="save">
          保存修改{{ dirty ? `（${Object.keys(dirty).length}）` : '' }}
        </VButton>
      </div>

      <div v-for="(items, group) in cfg.groups" :key="group" class="mb-3">
        <div class="group-title">{{ GROUP[group] || group }}</div>
        <div v-for="it in items" :key="it.key" class="cfg-row">
          <div style="flex: 1">
            <div class="small bold">{{ it.label }}</div>
            <div class="tiny muted">{{ it.description }}</div>
            <div class="tiny muted mono">{{ it.key }}</div>
          </div>
          <div style="width: 150px">
            <input
              v-if="typeof it.value === 'boolean'" type="checkbox"
              :checked="draft[it.key] ?? it.value"
              style="accent-color: var(--brand); width: 18px; height: 18px"
              @change="setDraft(it.key, ($event.target as HTMLInputElement).checked)"
            />
            <input
              v-else :type="typeof it.value === 'number' ? 'number' : 'text'"
              class="input" :value="draft[it.key] ?? it.value"
              @input="setDraft(it.key, typeof it.value === 'number' ? Number(($event.target as HTMLInputElement).value) : ($event.target as HTMLInputElement).value)"
            />
          </div>
        </div>
      </div>

      <div class="alert alert-info">
        <span class="alert-icon">ℹ</span>
        <div>
          三档阈值调整后 1 分钟内对新进简历生效，且不影响已打分记录。
          看板会标注每次变更点，便于把指标波动与配置调整对齐做归因。
        </div>
      </div>
    </div>

    <!-- 模型网关 -->
    <div v-if="tab === 'gateway'" class="card">
      <div class="card-head">
        <span class="card-title">模型网关配置</span>
        <span class="card-desc">换机器部署时在这里填一次即可，无需改代码</span>
      </div>

      <div v-if="gw.llm_enabled" class="alert alert-ok mb-3">
        <span class="alert-icon">✓</span>
        <div>
          <b>网关已连接</b>，当前可用模型 {{ gwModelCount }} 个，自动选型正常。
          <div class="tiny mt-1">
            自动选型：小={{ gwAuto.small }} · 中={{ gwAuto.medium }} · 大={{ gwAuto.large }}
          </div>
        </div>
      </div>
      <div v-else class="alert alert-warn mb-3">
        <span class="alert-icon">⚠</span>
        <div>
          <b>未配置模型网关。</b>平台其余功能仍可使用，但所有 AI 能力会走降级路径
          （界面上会明确标注）。填入网关地址与密钥即可启用。
        </div>
      </div>

      <div class="field">
        <label class="field-label">网关地址</label>
        <input v-model.trim="gwForm.base_url" class="input"
               placeholder="https://your-gateway.example.com" />
        <div class="field-hint">Anthropic 协议兼容的网关地址，不需要带 /v1 后缀</div>
      </div>

      <div class="field">
        <label class="field-label">访问密钥</label>
        <input v-model.trim="gwForm.token" type="password" class="input"
               :placeholder="gw.token_set ? '已设置，留空则不修改' : '填入密钥'" />
        <div class="field-hint">
          保存后写入 backend/.env，不会回显。也可以用环境变量 ANTHROPIC_AUTH_TOKEN 提供。
        </div>
      </div>

      <div class="row" style="gap: 9px">
        <VButton :loading="gwTesting" @click="testGateway">测试连接</VButton>
        <VButton variant="primary" :loading="gwSaving" @click="saveGateway">保存并生效</VButton>
        <span v-if="gwMsg" class="small" :style="{ color: gwOk ? 'var(--ok)' : 'var(--danger)' }">
          {{ gwMsg }}
        </span>
      </div>

      <div v-if="gwTested?.sample?.length" class="mt-3">
        <div class="tiny muted mb-1">网关返回的模型（前 8 个）</div>
        <div class="row wrap" style="gap: 5px">
          <span v-for="m in gwTested.sample" :key="m" class="tag tag-gray mono tiny">{{ m }}</span>
        </div>
      </div>

      <div class="divider" />
      <div class="tiny muted">
        配置文件位置：<span class="mono">{{ gw.env_file }}</span><br />
        令牌签名密钥保存在 <span class="mono">backend/data/secret.key</span>，
        每台机器首次启动自动生成 —— 删除它会让所有已登录用户强制重新登录。
      </div>
    </div>

    <!-- 模型路由 -->
    <div v-if="tab === 'model'" class="card">
      <div class="card-head">
        <span class="card-title">模型自动选型</span>
        <span class="card-desc">平台按能力档位自动选择模型，此处可手动锁定</span>
      </div>

      <div class="alert alert-info mb-3">
        <span class="alert-icon">◆</span>
        <div>
          平台从网关拉取可用模型列表，按家族特征与档位自动匹配，不硬编码模型名。
          分级路由的动机是成本：解析与格式化用小模型，打分与出题用中模型，成本相差约一个数量级。
        </div>
      </div>

      <div v-for="(mid, tier) in cfg.model_routing?.auto_selected" :key="tier" class="model-row">
        <div style="width: 130px">
          <div class="small bold">{{ TIER[String(tier)] || tier }}</div>
          <div class="tiny muted">{{ TIER_DESC[String(tier)] }}</div>
        </div>
        <div style="flex: 1">
          <select class="select" :value="override[String(tier)] ?? ''" @change="lockModel(String(tier), ($event.target as HTMLSelectElement).value)">
            <option value="">自动选型 → {{ mid }}</option>
            <option v-for="c in (cfg.model_routing?.candidates?.[tier] || [])" :key="c.id" :value="c.id">
              {{ c.id }}（{{ c.vendor }}）
            </option>
          </select>
        </div>
      </div>
    </div>

    <!-- 提示词 -->
    <div v-if="tab === 'prompt'" class="card">
      <div class="card-head">
        <span class="card-title">提示词库</span>
        <span class="card-desc">独立于代码管理，按能力拆分，支持按能力粒度独立回滚</span>
        <VButton size="sm" class="ml-auto" icon="⟳" @click="loadPrompts">刷新</VButton>
      </div>

      <div class="alert alert-warn mb-3">
        <span class="alert-icon">⚠</span>
        <div>
          提示词变更上线前必须跑全量离线回归，核心指标下降超过 2 个百分点即阻止上线。
          此机制由发布流程强制卡点，非人工自觉。
        </div>
      </div>

      <div v-for="p in prompts" :key="p.prompt_id + p.version" class="prompt-row">
        <div class="row-between">
          <div class="row" style="gap: 8px">
            <span class="mono small bold">{{ p.prompt_id }}</span>
            <span class="tag tag-gray">{{ p.version }}</span>
            <span v-if="p.active" class="tag tag-ok">生效中</span>
            <span v-if="p.tier" class="tag tag-blue tiny">档位 {{ p.tier }}</span>
            <span v-if="p.source === '代码种子'" class="tag tag-purple tiny">代码种子</span>
          </div>
          <VButton v-if="!p.active" size="sm" @click="activatePrompt(p)">启用</VButton>
        </div>
        <div v-if="p.note" class="tiny muted mt-1">{{ p.note }}</div>
        <div v-if="p.canary_version" class="tiny mt-1" style="color: var(--brand-text)">
          灰度中：{{ p.canary_version }} 占 {{ (p.canary_ratio * 100).toFixed(0) }}%
        </div>
      </div>
    </div>

    <!-- 知识库 -->
    <div v-if="tab === 'knowledge'" class="card">
      <div class="card-head">
        <span class="card-title">知识库</span>
        <span class="card-desc">{{ kb.indexed_chunks || 0 }} 个索引片段</span>
        <VButton size="sm" class="ml-auto" icon="+" @click="kbOpen = true">新增文档</VButton>
      </div>

      <div class="alert alert-info mb-3">
        <span class="alert-icon">◆</span>
        <div>
          知识走检索，判断走微调：岗位标准、题库、SOP 文档变更频繁，走知识库检索，
          改一次文档全平台即时生效、不需重新训练；什么算好候选人的隐性判断只能从结论反推，走历史样本微调。
        </div>
      </div>

      <div class="row mb-2" style="gap: 8px">
        <input v-model="kq" class="input" style="max-width: 320px" placeholder="检索知识库，验证召回效果" @keyup.enter="doSearch" />
        <VButton size="sm" :loading="searching" @click="doSearch">检索</VButton>
        <span v-if="searchScope" class="tiny muted">权限范围：{{ searchScope }}</span>
      </div>

      <div v-if="hits.length" class="mb-3">
        <div class="tiny muted mb-1">检索结果（受与业务数据同一套权限约束）</div>
        <div v-for="(h, i) in hits" :key="i" class="hit-row">
          <div class="row-between">
            <span class="small bold">{{ h.ref }}</span>
            <span class="tag tag-blue tiny">相关度 {{ h.score }}</span>
          </div>
          <div class="tiny dim mt-1">{{ h.text.slice(0, 220) }}…</div>
        </div>
      </div>

      <table class="table">
        <thead>
          <tr><th>文档</th><th style="width: 90px">分类</th><th style="width: 110px">业务线</th>
          <th style="width: 80px">片段</th><th style="width: 70px"></th></tr>
        </thead>
        <tbody>
          <tr v-for="d in kb.docs" :key="d.id">
            <td>
              <div class="bold small">{{ d.title }}</div>
              <div class="tiny muted truncate" style="max-width: 380px">{{ d.source_path }}</div>
            </td>
            <td><span class="tag tag-gray">{{ d.category }}</span></td>
            <td class="tiny">{{ d.business_line }}</td>
            <td class="num">{{ d.chunk_count }}</td>
            <td>
              <VButton size="sm" variant="ghost" @click="removeDoc(d)">删除</VButton>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 日志 -->
    <div v-if="tab === 'log'" class="card">
      <div class="card-head">
        <span class="card-title">日志与审计</span>
        <span class="card-desc">保留期不短于 3 年，只追加不可编辑</span>
        <div class="ml-auto row">
          <select v-model="logKind" class="select" style="width: 140px" @change="loadLogs">
            <option value="">全部类型</option>
            <option value="ai">AI 决策</option>
            <option value="human">人工操作</option>
            <option value="config">配置变更</option>
            <option value="injection">注入拦截</option>
          </select>
          <VButton size="sm" :loading="loadingLogs" @click="loadLogs">刷新</VButton>
        </div>
      </div>

      <div class="grid grid-4 mb-3">
        <div class="stat">
          <div class="stat-label">日志总数</div>
          <div class="stat-value sm">{{ logs.summary?.total ?? 0 }}</div>
        </div>
        <div class="stat">
          <div class="stat-label">降级次数</div>
          <div class="stat-value sm" :style="{ color: (logs.summary?.degraded_count ?? 0) > 0 ? 'var(--warn)' : '' }">
            {{ logs.summary?.degraded_count ?? 0 }}
          </div>
          <div class="stat-hint">任何降级都会在界面明示，禁止静默降级</div>
        </div>
        <div class="stat">
          <div class="stat-label">注入拦截</div>
          <div class="stat-value sm" :style="{ color: (logs.summary?.injection_count ?? 0) > 0 ? 'var(--danger)' : '' }">
            {{ logs.summary?.injection_count ?? 0 }}
          </div>
          <div class="stat-hint">命中特征后剥离并标记待审，不直接拒绝</div>
        </div>
        <div class="stat">
          <div class="stat-label">保留要求</div>
          <div class="stat-value sm">≥3 年</div>
          <div class="stat-hint">满足劳动争议举证需要</div>
        </div>
      </div>

      <div v-for="l in logs.items" :key="l.id" class="log-item">
        <div class="row-between">
          <div class="row" style="gap: 7px">
            <span class="tag" :class="KIND_TAG[l.kind] || 'tag-gray'">{{ KIND[l.kind] || l.kind }}</span>
            <span class="small">{{ l.summary }}</span>
          </div>
          <span class="tiny muted nowrap">{{ fmtTime(l.at) }}</span>
        </div>
        <div v-if="l.model || l.degrade !== 'none'" class="tiny muted mt-1">
          <span v-if="l.model">模型 {{ l.model }}</span>
          <span v-if="l.prompt_version"> · 提示词 {{ l.prompt_version }}</span>
          <span v-if="l.confidence"> · 置信度 {{ l.confidence }}</span>
          <span v-if="l.latency_ms"> · {{ l.latency_ms }}ms</span>
          <span v-if="l.degrade && l.degrade !== 'none'" style="color: var(--warn)"> · 降级 {{ l.degrade }}</span>
        </div>
      </div>
    </div>

    <!-- 权限矩阵 -->
    <div v-if="tab === 'role'" class="card">
      <div class="card-head">
        <span class="card-title">角色权限矩阵</span>
        <span class="card-desc">数据可见范围在查询层强制隔离，不依赖前端隐藏</span>
      </div>

      <div class="alert alert-info mb-3">
        <span class="alert-icon">◆</span>
        <div>
          <b>两条由数据合规评估意见明确要求的约束</b>：<br />
          一、面试官只能看到被指派的候选人，避免简历在组织内无边界流动。<br />
          二、系统管理员可配置阈值但不可查看简历正文，防止运维角色成为数据合规的缺口。
        </div>
      </div>

      <div style="overflow-x: auto">
        <table class="table">
          <thead>
            <tr><th>角色</th><th>M1 能力模型</th><th>M2 筛选</th><th>M3 面试</th><th>M4 培训</th><th>M5 看板</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in cfg.role_matrix || []" :key="r.role">
              <td class="bold small nowrap">{{ r.role }}</td>
              <td class="tiny">{{ r['M1 能力模型'] }}</td>
              <td class="tiny">{{ r['M2 筛选'] }}</td>
              <td class="tiny">{{ r['M3 面试'] }}</td>
              <td class="tiny">{{ r['M4 培训'] }}</td>
              <td class="tiny">{{ r['M5 看板'] }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <VModal v-if="kbOpen" title="新增知识库文档" wide @close="kbOpen = false">
      <div class="grid grid-3">
        <div class="field">
          <label class="field-label">标题<span class="req">*</span></label>
          <input v-model="doc.title" class="input" />
        </div>
        <div class="field">
          <label class="field-label">分类</label>
          <select v-model="doc.category" class="select">
            <option>SOP</option><option>合规</option><option>规范</option><option>方法论</option>
          </select>
        </div>
        <div class="field">
          <label class="field-label">业务线</label>
          <input v-model="doc.business_line" class="input" placeholder="通用" />
        </div>
      </div>
      <div class="field">
        <label class="field-label">正文<span class="req">*</span></label>
        <textarea v-model="doc.content" class="textarea" rows="10"
                  placeholder="粘贴文档正文。系统会切分成片段并建立索引，入库后全平台即时生效。" />
      </div>
      <template #foot>
        <VButton @click="kbOpen = false">取消</VButton>
        <VButton variant="primary" :disabled="!doc.title || !doc.content" :loading="saving"
                 @click="addDoc">入库并重建索引</VButton>
      </template>
    </VModal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import http, { api } from '../api'
import VButton from '../components/VButton.vue'
import VModal from '../components/VModal.vue'
import { fmtTime } from '../api/labels'
import { toast } from '../components/toast'

const tab = ref('threshold')
const cfg = ref<any>({})
const draft = ref<Record<string, unknown>>({})
const saving = ref(false)
const override = ref<Record<string, string>>({})

// ---- 模型网关 ----
const gw = ref<any>({})
const gwForm = ref({ base_url: '', token: '' })
const gwTesting = ref(false)
const gwSaving = ref(false)
const gwMsg = ref('')
const gwOk = ref(false)
const gwTested = ref<any>(null)
const gwModelCount = ref(0)
const gwAuto = ref<Record<string, string>>({})

async function loadGateway() {
  try {
    gw.value = await http.get('/system/gateway').then(r => r.data)
    gwForm.value.base_url = gw.value.base_url || ''
    if (gw.value.llm_enabled) {
      const s = await api.systemStatus()
      gwModelCount.value = s.llm?.available_models || 0
      gwAuto.value = s.llm?.auto_selected || {}
    }
  } catch (e) { /* 无权限时不阻塞页面 */ }
}

async function testGateway() {
  gwTesting.value = true; gwMsg.value = ''; gwTested.value = null
  try {
    const r = await http.post('/system/gateway/test', gwForm.value).then(r => r.data)
    gwOk.value = r.ok
    gwMsg.value = r.ok ? r.message : (r.message + ' ' + (r.error || ''))
    if (r.ok) gwTested.value = r
  } catch (e: any) {
    gwOk.value = false
    gwMsg.value = e?.friendly || '测试失败'
  } finally {
    gwTesting.value = false
  }
}

async function saveGateway() {
  gwSaving.value = true; gwMsg.value = ''
  try {
    const r = await http.post('/system/gateway', gwForm.value).then(r => r.data)
    gwOk.value = r.llm_enabled
    gwMsg.value = r.message
    gwForm.value.token = ''
    await loadGateway()
    await load()
    toast.ok('网关配置已保存', r.llm_enabled ? `可用模型 ${r.available_models} 个` : '')
  } catch (e: any) {
    gwOk.value = false
    gwMsg.value = e?.friendly || '保存失败'
  } finally {
    gwSaving.value = false
  }
}

const prompts = ref<any[]>([])
const kb = ref<any>({})
const kq = ref('')
const hits = ref<any[]>([])
const searching = ref(false)
const searchScope = ref('')
const kbOpen = ref(false)
const doc = ref({ title: '', category: 'SOP', business_line: '通用', content: '' })

const logs = ref<{ items: any[]; summary?: any }>({ items: [] })
const loadingLogs = ref(false)
const logKind = ref('')

const GROUP: Record<string, string> = {
  threshold: '分层阈值', pool: '待定池', cost: '成本约束',
  model: '模型档位用途', review: '复核策略', interview: '面试',
}
const TIER: Record<string, string> = {
  small: '小模型档', medium: '中模型档', large: '大模型档',
  vision: '视觉档', embedding: '向量档',
}
const TIER_DESC: Record<string, string> = {
  small: '解析、格式化', medium: '打分、出题、判卷',
  large: '复杂归因', vision: '图片简历', embedding: '向量检索',
}
const KIND: Record<string, string> = {
  ai: 'AI 决策', human: '人工操作', config: '配置变更',
  access: '权限访问', injection: '注入拦截',
}
const KIND_TAG: Record<string, string> = {
  ai: 'tag-ai', human: 'tag-blue', config: 'tag-purple',
  access: 'tag-gray', injection: 'tag-danger',
}

const dirty = computed(() => {
  const out: Record<string, unknown> = {}
  const all: any[] = Object.values(cfg.value.groups || {}).flat()
  for (const it of all) {
    const d = draft.value[it.key]
    if (d !== undefined && d !== it.value) out[it.key] = d
  }
  return out
})

function setDraft(k: string, v: unknown) { draft.value[k] = v }

const forbidden = ref(false)

/** 从无权限页一键切到有权限的身份，省去用户自己找下拉框 */
function switchToAdmin() {
  localStorage.setItem('userid', 'admin1')
  window.location.reload()
}

async function load() {
  try {
    cfg.value = await api.config()
    forbidden.value = false
  } catch (e: any) {
    // 该页仅 HR负责人 / 法务审计 / 系统管理员可访问。
    // 侧边栏已按角色隐藏入口，但 URL 仍可直达，这里给一个明确的说明页，
    // 而不是让多个接口各自弹一遍 403。
    if (e?.response?.status === 403) { forbidden.value = true; return }
    toast.err(e, '加载配置失败')
  }
}

async function loadPrompts() {
  try { prompts.value = (await api.prompts()).prompts } catch (e) { toast.err(e) }
}

async function loadKb() {
  try { kb.value = await api.knowledge() } catch (e) { toast.err(e) }
}

async function loadLogs() {
  loadingLogs.value = true
  try {
    const [items, summary] = await Promise.all([
      api.logs({ kind: logKind.value || undefined, limit: 200 }),
      api.logsSummary(),
    ])
    logs.value = { items, summary }
  } catch (e) {
    toast.err(e)
  } finally {
    loadingLogs.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const r = await api.updateConfig(dirty.value)
    toast.ok(`已更新 ${r.changed.length} 项配置`, r.note)
    draft.value = {}
    await load()
  } catch (e) {
    toast.err(e)
  } finally {
    saving.value = false
  }
}

async function lockModel(tier: string, modelId: string) {
  try {
    const r = await api.setModelRoute(tier, modelId || null)
    override.value[tier] = modelId
    cfg.value.model_routing.auto_selected = r.auto_selected
    toast.ok(modelId ? `已锁定 ${tier} 档位为 ${modelId}` : '已恢复自动选型')
  } catch (e) {
    toast.err(e)
  }
}

async function activatePrompt(p: any) {
  if (!confirm(`确定启用 ${p.prompt_id}@${p.version}？\n\n启用前应已跑完全量离线回归。`)) return
  try {
    const r = await api.activatePrompt({ prompt_id: p.prompt_id, version: p.version })
    toast.ok(r.message)
    await loadPrompts()
  } catch (e) {
    toast.err(e)
  }
}

async function doSearch() {
  if (!kq.value) return
  searching.value = true
  try {
    const r = await api.searchKnowledge(kq.value, 5)
    hits.value = r.hits || []
    searchScope.value = r.scoped_business_line
    if (!hits.value.length) toast.warn('知识库中没有找到相关内容')
  } catch (e) {
    toast.err(e)
  } finally {
    searching.value = false
  }
}

async function addDoc() {
  saving.value = true
  try {
    const r = await http.post('/knowledge', doc.value)
    toast.ok((r as any).data?.message || '已入库并重建索引')
    kbOpen.value = false
    doc.value = { title: '', category: 'SOP', business_line: '通用', content: '' }
    await loadKb()
  } catch (e) {
    toast.err(e)
  } finally {
    saving.value = false
  }
}

async function removeDoc(d: any) {
  if (!confirm(`确定删除文档「${d.title}」？删除后会重建索引。`)) return
  try {
    await http.delete(`/knowledge/${d.id}`)
    toast.ok('已删除并重建索引')
    await loadKb()
  } catch (e) { toast.err(e) }
}

onMounted(async () => {
  await load()
  if (forbidden.value) return
  await loadGateway()
  await loadPrompts()
  await loadKb()
  await loadLogs()
})
</script>

<style scoped>
.tabs { display: flex; gap: 4px; margin-bottom: 14px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.tab {
  padding: 8px 15px; background: none; border: none; cursor: pointer;
  color: var(--text-1); font-size: 13px; font-family: inherit;
  border-bottom: 2px solid transparent; margin-bottom: -1px;
}
.tab:hover { color: var(--text-0); }
.tab.on { color: var(--brand-text); border-bottom-color: var(--brand); font-weight: 570; }

.group-title {
  font-size: 12px; font-weight: 620; color: var(--brand-text);
  padding-bottom: 7px; border-bottom: 1px solid var(--border); margin-bottom: 9px;
}
.cfg-row {
  display: flex; gap: 14px; align-items: center;
  padding: 9px 0; border-bottom: 1px dashed var(--border);
}
.cfg-row:last-child { border-bottom: none; }
.model-row {
  display: flex; gap: 14px; align-items: center;
  padding: 11px 0; border-bottom: 1px solid var(--border);
}
.prompt-row {
  padding: 11px 13px; border: 1px solid var(--border);
  border-radius: var(--radius); margin-bottom: 9px; background: var(--bg-0);
}
.hit-row {
  padding: 9px 11px; border: 1px solid var(--border);
  border-left: 3px solid var(--brand);
  border-radius: var(--radius); margin-bottom: 7px; background: var(--bg-0);
}
.log-item { padding: 10px 0; border-bottom: 1px solid var(--border); }
.log-item:last-child { border-bottom: none; }
</style>
