import axios from 'axios'
import { ref } from 'vue'

export const currentUser = ref<any>(null)

const http = axios.create({ baseURL: '/api', timeout: 300000 })

// 用登录拿到的令牌鉴权
http.interceptors.request.use((cfg) => {
  const token = localStorage.getItem('auth_token')
  if (token) cfg.headers['Authorization'] = `Bearer ${token}`
  return cfg
})

http.interceptors.response.use(
  (r) => r,
  (e) => {
    e.friendly = friendlyError(e)
    // 令牌失效（过期或密钥变更）时清理本地状态并回到登录页，
    // 否则用户会停在一个每点一次都报错的界面上，不知道该怎么办。
    if (e?.response?.status === 401 && !location.hash.startsWith('#/login')) {
      localStorage.removeItem('auth_token')
      localStorage.removeItem('auth_user')
      location.hash = '#/login'
    }
    return Promise.reject(e)
  },
)

/** 把各种后端错误转成一句人能看懂的话。

 * FastAPI 的 422 校验错误的 detail 是一个数组，直接 String() 会得到
 * "[object Object]"，之前用户只能看到 "Request failed with status code 422"
 * 这种毫无信息量的提示。这里逐层降级，保证总能给出一句可读的话。
 */
function friendlyError(e: any): string {
  const res = e?.response
  if (!res) {
    if (e?.code === 'ECONNABORTED') return '请求超时。AI 生成耗时较长，请稍后重试。'
    return e?.message || '网络请求失败，请确认后端服务已启动'
  }

  const d = res.data?.detail
  if (typeof d === 'string' && d) return d

  // 422：字段校验失败
  if (Array.isArray(d) && d.length) {
    const parts = d.slice(0, 3).map((x: any) => {
      const loc = Array.isArray(x?.loc) ? x.loc.filter((s: any) => s !== 'body').join('.') : ''
      return loc ? `${loc}：${x?.msg || '校验失败'}` : (x?.msg || '校验失败')
    })
    return `请求参数有误（${parts.join('；')}）`
  }

  if (res.status === 403) return '当前角色无权执行该操作'
  if (res.status === 404) return '请求的资源不存在'
  if (res.status === 500) return `服务端错误${d ? '：' + String(d).slice(0, 120) : ''}`
  if (res.status === 503) return `服务暂时不可用${d ? '：' + String(d).slice(0, 120) : ''}`
  return `请求失败（HTTP ${res.status}）`
}

export default http

/* ---------------- 平台 ---------------- */
export const api = {
  systemStatus: () => http.get('/system/status').then((r) => r.data),
  users: () => http.get('/users').then((r) => r.data),
  logs: (p?: Record<string, unknown>) => http.get('/logs', { params: p }).then((r) => r.data),
  logsSummary: () => http.get('/logs/summary').then((r) => r.data),
  config: () => http.get('/config').then((r) => r.data),
  updateConfig: (updates: Record<string, unknown>) => http.put('/config', { updates }).then((r) => r.data),
  setModelRoute: (tier: string, model_id: string | null) =>
    http.post('/config/model-route', { tier, model_id }).then((r) => r.data),
  resetDemo: () => http.post('/system/reset-demo').then((r) => r.data),

  prompts: () => http.get('/prompts').then((r) => r.data),
  activatePrompt: (p: Record<string, unknown>) => http.post('/prompts/activate', p).then((r) => r.data),

  knowledge: () => http.get('/knowledge').then((r) => r.data),
  searchKnowledge: (query: string, top_k = 6) =>
    http.post('/knowledge/search', { query, top_k }).then((r) => r.data),
}

/* ---------------- M1 能力模型 ---------------- */
export const modelApi = {
  list: () => http.get('/models').then((r) => r.data),
  positions: () => http.get('/models/positions').then((r) => r.data),
  get: (id: number) => http.get(`/models/${id}`).then((r) => r.data),
  byPosition: (pid: number) => http.get(`/models/positions/${pid}`).then((r) => r.data),
  versions: (id: number) => http.get(`/models/${id}/versions`).then((r) => r.data),
  create: (p: Record<string, unknown>) => http.post('/models', p).then((r) => r.data),
  update: (id: number, items: unknown[]) => http.put(`/models/${id}`, { items }).then((r) => r.data),
  toggle: (id: number) => http.post(`/models/${id}/toggle`).then((r) => r.data),
  remove: (id: number) => http.delete(`/models/${id}`).then((r) => r.data),
  trial: (id: number, p: Record<string, unknown>) => http.post(`/models/${id}/trial`, p).then((r) => r.data),
  samples: (seq = '') => http.get('/models/samples/list', { params: { seq } }).then((r) => r.data),
  analyzeJd: (jd_text: string, seq: string) =>
    http.post('/models/analyze-jd', { jd_text, seq }).then((r) => r.data),
}

/* ---------------- M2 简历筛选 ---------------- */
export const candApi = {
  list: (p?: Record<string, unknown>) => http.get('/candidates', { params: p }).then((r) => r.data),
  get: (id: number) => http.get(`/candidates/${id}`).then((r) => r.data),
  review: (id: number, p: Record<string, unknown>) =>
    http.post(`/candidates/${id}/review`, p).then((r) => r.data),
  rescue: (id: number, reason: string) =>
    http.post(`/candidates/${id}/rescue`, { reason }).then((r) => r.data),
  archive: (id: number) => http.post(`/candidates/${id}/archive`).then((r) => r.data),
  reparse: (id: number) => http.post(`/candidates/${id}/reparse`).then((r) => r.data),
  confirmFields: (id: number, fields: Record<string, unknown>) =>
    http.post(`/candidates/${id}/confirm-fields`, { fields }).then((r) => r.data),
  evidence: (id: number, abilityId: string) =>
    http.get(`/candidates/${id}/evidence/${abilityId}`).then((r) => r.data),
  poolSummary: () => http.get('/candidates/pool/summary').then((r) => r.data),
  importOne: (form: FormData) =>
    http.post('/candidates/import', form, { headers: { 'Content-Type': 'multipart/form-data' } })
      .then((r) => r.data),
  importBatch: (form: FormData) =>
    http.post('/candidates/import/batch', form, { headers: { 'Content-Type': 'multipart/form-data' } })
      .then((r) => r.data),
}

/* ---------------- M3 面试助手 ---------------- */
export const interviewApi = {
  schedules: () => http.get('/interview/schedules').then((r) => r.data),
  create: (p: Record<string, unknown>) => http.post('/interview/schedules', p).then((r) => r.data),
  prepare: (sid: number, plan_minutes = 0) =>
    http.get(`/interview/${sid}/prepare`, { params: { plan_minutes } }).then((r) => r.data),
  generate: (sid: number, plan_minutes: number) =>
    http.post(`/interview/${sid}/generate`, { plan_minutes }).then((r) => r.data),
  questionAction: (qid: number, action: string, content = '') =>
    http.post(`/interview/questions/${qid}/action`, { action, content }).then((r) => r.data),
  buildReport: (sid: number, p: Record<string, unknown>) =>
    http.post(`/interview/${sid}/report`, p).then((r) => r.data),
  getReport: (sid: number) => http.get(`/interview/${sid}/report`).then((r) => r.data),
  submitScores: (sid: number, p: Record<string, unknown>) =>
    http.post(`/interview/${sid}/scores`, p).then((r) => r.data),
}

/* ---------------- M4 培训考核 ---------------- */
export const trainingApi = {
  plans: () => http.get('/training/plans').then((r) => r.data),
  create: (p: Record<string, unknown>) => http.post('/training/plans', p).then((r) => r.data),
  get: (id: number) => http.get(`/training/plans/${id}`).then((r) => r.data),
  publish: (id: number, allow = false) =>
    http.post(`/training/plans/${id}/publish`, { allow_missing_ref: allow }).then((r) => r.data),
  remove: (id: number) => http.delete(`/training/plans/${id}`).then((r) => r.data),
  submit: (id: number, p: Record<string, unknown>) =>
    http.post(`/training/plans/${id}/submit`, p).then((r) => r.data),
  confirm: (sid: number, final_score: number) =>
    http.post(`/training/submissions/${sid}/confirm`, { final_score }).then((r) => r.data),
  probation: (sid: number, p: Record<string, unknown>) =>
    http.post(`/training/submissions/${sid}/probation`, p).then((r) => r.data),
}

/* ---------------- M5 看板 ---------------- */
export const boardApi = {
  overview: () => http.get('/board/overview').then((r) => r.data),
  trend: (days = 30) => http.get('/board/trend', { params: { days } }).then((r) => r.data),
  funnel: () => http.get('/board/funnel').then((r) => r.data),
  events: (days = 14) => http.get('/board/events', { params: { days } }).then((r) => r.data),
  reflow: () => http.get('/board/reflow').then((r) => r.data),
}

/* ---------------- 智能体 ---------------- */
export const agentApi = {
  skills: () => http.get('/agent/skills').then((r) => r.data),
  chat: (message: string, history: unknown[]) =>
    http.post('/agent/chat', { message, history }).then((r) => r.data),
  invoke: (skill: string, args: Record<string, unknown>) =>
    http.post(`/agent/invoke/${skill}`, args).then((r) => r.data),
}

/* ---------------- 培训考核（含考试与反作弊） ---------------- */
export const examApi = {
  // 元信息
  meta: () => http.get('/training/meta').then((r) => r.data),

  // 公司资料（发布者上传，作为出题语料）
  materials: (planId = 0) =>
    http.get('/training/materials', { params: { plan_id: planId } }).then((r) => r.data),
  uploadMaterial: (form: FormData) =>
    http.post('/training/materials', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then((r) => r.data),
  deleteMaterial: (id: number) => http.delete(`/training/materials/${id}`).then((r) => r.data),

  // 方案与指派
  plans: () => http.get('/training/plans').then((r) => r.data),
  createPlan: (p: Record<string, unknown>) => http.post('/training/plans', p).then((r) => r.data),
  plan: (id: number) => http.get(`/training/plans/${id}`).then((r) => r.data),
  // 用当前资料与能力项重新生成，保留方案 ID 与指派关系
  regenerate: (id: number, force = false) =>
    http.post(`/training/plans/${id}/regenerate`, { force }).then((r) => r.data),
  publishPlan: (id: number, allowMissingRef = false) =>
    http.post(`/training/plans/${id}/publish`, { allow_missing_ref: allowMissingRef })
      .then((r) => r.data),
  newcomers: () => http.get('/training/newcomers').then((r) => r.data),
  assign: (p: Record<string, unknown>) => http.post('/training/assign', p).then((r) => r.data),
  assignments: (planId = 0) =>
    http.get('/training/assignments', { params: { plan_id: planId } }).then((r) => r.data),
  deleteAssignment: (id: number) => http.delete(`/training/assignments/${id}`).then((r) => r.data),

  // 新人视角
  my: () => http.get('/training/my').then((r) => r.data),

  // 考试（服务端计时与试卷冻结）
  start: (planId: number, minutes = 60) =>
    http.post(`/training/plans/${planId}/start`, { minutes }).then((r) => r.data),
  // 取回进行中的试卷（刷新后恢复，服务端保证是同一份）
  resume: (attemptId: number) =>
    http.get(`/training/attempts/${attemptId}/paper`).then((r) => r.data),
  save: (attemptId: number, answers: Record<string, string>) =>
    http.post(`/training/attempts/${attemptId}/save`, { answers }).then((r) => r.data),
  heartbeat: (attemptId: number, signals: unknown[] = []) =>
    http.post(`/training/attempts/${attemptId}/heartbeat`, { signals }).then((r) => r.data),
  submit: (attemptId: number, answers: Record<string, string>, signals: unknown[] = []) =>
    http.post(`/training/attempts/${attemptId}/submit`, { answers, signals }).then((r) => r.data),
  attempt: (attemptId: number) =>
    http.get(`/training/attempts/${attemptId}/result`).then((r) => r.data),

  // 带教人复核
  reviewQueue: (planId = 0) =>
    http.get('/training/review/queue', { params: { plan_id: planId } }).then((r) => r.data),
  voidAttempt: (attemptId: number, note: string) =>
    http.post(`/training/attempts/${attemptId}/void`, { note }).then((r) => r.data),
  confirm: (attemptId: number, finalScore: number) =>
    http.post(`/training/submissions/${attemptId}/confirm`, { final_score: finalScore })
      .then((r) => r.data),
  probation: (submissionId: number, p: Record<string, unknown>) =>
    http.post(`/training/submissions/${submissionId}/probation`, p).then((r) => r.data),
}

/* ---------------- 培训方案（含资料绑定与设置）---------------- */
export const planApi = {
  /** 新建方案：名称、周期、考试规则、选定资料 */
  create: (p: Record<string, unknown>) => http.post('/training/plans', p).then((r) => r.data),
  /** 修改方案设置 */
  update: (id: number, p: Record<string, unknown>) =>
    http.put(`/training/plans/${id}`, p).then((r) => r.data),
  /** 资料库：planId=0 取全部未绑定资料 */
  materials: (planId = 0) =>
    http.get('/training/materials', { params: { plan_id: planId } }).then((r) => r.data),
  uploadMaterial: (form: FormData) =>
    http.post('/training/materials', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then((r) => r.data),
  deleteMaterial: (id: number) => http.delete(`/training/materials/${id}`).then((r) => r.data),
}
