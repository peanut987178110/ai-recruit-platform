/** 展示层映射：把后端枚举转成中文与配色，集中一处便于统一口径。 */

export const STATE_LABEL: Record<string, string> = {
  hit: '命中',
  partial: '部分命中',
  absent: '未体现',
  mismatch: '不符合',
}

/** 四态配色。未体现用中性灰、不符合用红 —— 两者必须在视觉上明确区分（验收 A2-4）。 */
export const STATE_TAG: Record<string, string> = {
  hit: 'tag-ok',
  partial: 'tag-warn',
  absent: 'tag-gray',
  mismatch: 'tag-danger',
}

export const STATE_HINT: Record<string, string> = {
  hit: '简历中有明确证据支撑',
  partial: '有部分证据，深度需在面试中确认',
  absent: '简历未提及，属信息缺失，应留到面试验证',
  mismatch: '简历中写了但明显不符合标准',
}

export const TIER_TAG: Record<string, string> = {
  高分档: 'tag-ok',
  中间档: 'tag-warn',
  低分档: 'tag-gray',
}

export const TIER_DESC: Record<string, string> = {
  高分档: '置信度与总分达标，直接转待安排面试，HR 可退回复核',
  中间档: '强制人工复核，不允许批量一键通过',
  低分档: '转待定池，保留 7 天，到期归档',
}

export const STATUS_TAG: Record<string, string> = {
  待解析: 'tag-gray',
  解析异常: 'tag-danger',
  待打分: 'tag-gray',
  待安排面试: 'tag-ok',
  待复核: 'tag-warn',
  待定池: 'tag-purple',
  已归档: 'tag-gray',
}

export const ROLE_LABEL: Record<string, string> = {
  招聘HR: '招聘 HR',
  HR负责人: 'HR 负责人',
  用人经理: '用人经理',
  业务面试官: '业务面试官',
  带教人: '带教人',
  法务审计: '法务审计',
  系统管理员: '系统管理员',
}

export const EVIDENCE_LABEL: Record<string, string> = {
  project: '项目经历',
  tenure: '任职履历',
  skill: '技能标签',
  metric: '量化成果',
  education: '教育背景',
}

export const REJECT_REASONS = ['能力不匹配', '经验不足', '简历造假疑似', '岗位已关闭', '其他']

export function tierColor(tier: string) {
  return TIER_TAG[tier] || 'tag-gray'
}

export function stateColor(state: string) {
  return STATE_TAG[state] || 'tag-gray'
}

export function scoreColor(score: number) {
  if (score >= 75) return 'var(--ok)'
  if (score >= 45) return 'var(--warn)'
  return 'var(--text-2)'
}

export function pct(v: number | null | undefined, digits = 1) {
  if (v === null || v === undefined) return '—'
  return `${(v * 100).toFixed(digits)}%`
}

export function num(v: number | null | undefined, digits = 1) {
  if (v === null || v === undefined) return '—'
  return Number(v).toFixed(digits)
}

/** 降级状态必须显式展示，禁止静默降级（PRD 4.3） */
export const DEGRADE_LABEL: Record<string, string> = {
  none: '',
  retry: '一级降级·重试',
  switch_model: '二级降级·已切备用模型',
  human: '三级降级·转人工',
}

export function degradeText(meta: Record<string, unknown> | null | undefined) {
  if (!meta) return ''
  const d = String(meta.degrade || 'none')
  if (d === 'none' || !d) return ''
  return DEGRADE_LABEL[d] || d
}

export function fmtTime(v: string | null | undefined) {
  if (!v) return '—'
  const s = String(v).replace('T', ' ')
  return s.length > 16 ? s.slice(0, 16) : s
}

export function fmtDate(v: string | null | undefined) {
  if (!v) return '—'
  return String(v).slice(0, 10)
}
