<template>
  <div>
    <div class="grid grid-4 mb-3">
      <div class="stat">
        <div class="stat-label">候选人总数</div>
        <div class="stat-value">{{ o.total_candidates ?? 0 }}</div>
        <div class="stat-hint">全部业务线</div>
      </div>
      <div class="stat">
        <div class="stat-label">
          结论一致率
          <span class="tag tiny tag-gray" title="人类两名资深 HR 双盲一致率为 78%，是该指标的天然参照上限">?</span>
        </div>
        <div class="stat-value"
             :style="{ color: o.consistency_rate?.insufficient ? 'var(--text-2)' : rateColor(o.consistency_rate?.rate, 0.85) }">
          {{ o.consistency_rate?.insufficient ? '数据不足' : pct(o.consistency_rate?.rate) }}
        </div>
        <div class="stat-hint">
          门槛 85% · 样本 {{ o.consistency_rate?.samples ?? 0 }} 份
          <template v-if="o.consistency_rate?.insufficient"> · 不足 30 条不展示比例</template>
          <template v-else-if="o.consistency_rate?.note"> · {{ o.consistency_rate.note }}</template>
        </div>
      </div>
      <div class="stat">
        <div class="stat-label">
          误杀率
          <span class="tag tiny tag-danger" title="一票否决指标">一票否决</span>
        </div>
        <div class="stat-value"
             :style="{ color: o.kill_rate?.insufficient ? 'var(--text-2)' : ((o.kill_rate?.rate ?? 0) <= 2 ? 'var(--ok)' : 'var(--danger)') }">
          <template v-if="o.kill_rate?.insufficient">数据不足</template>
          <template v-else>{{ num(o.kill_rate?.rate, 2) }}<span class="stat-unit">%</span></template>
        </div>
        <div class="stat-hint">
          门槛 ≤2% · 分母仅含自动分流样本，不含人工复核区
          <template v-if="o.kill_rate?.insufficient"> · 分流样本仅 {{ o.kill_rate?.samples ?? 0 }} 条</template>
        </div>
      </div>
      <div class="stat">
        <div class="stat-label">引用准确率</div>
        <div class="stat-value"
             :style="{ color: o.citation_accuracy?.insufficient ? 'var(--text-2)' : rateColor(o.citation_accuracy?.rate, 0.92) }">
          {{ o.citation_accuracy?.insufficient ? '数据不足' : pct(o.citation_accuracy?.rate) }}
        </div>
        <div class="stat-hint">
          门槛 92% · 抽检 {{ o.citation_accuracy?.samples ?? 0 }} 条
          <template v-if="o.citation_accuracy?.insufficient"> · 不足 30 条不展示比例</template>
        </div>
      </div>
    </div>

    <div class="grid grid-4 mb-3">
      <div class="stat">
        <div class="stat-label">面试题采纳率</div>
        <div class="stat-value"
             :style="{ color: o.question_adopt_rate?.insufficient ? 'var(--text-2)' : rateColor(o.question_adopt_rate?.rate, 70) }">
          <template v-if="o.question_adopt_rate?.insufficient">数据不足</template>
          <template v-else>{{ o.question_adopt_rate?.rate ?? '—' }}<span v-if="o.question_adopt_rate?.rate !== null && o.question_adopt_rate?.rate !== undefined" class="stat-unit">%</span></template>
        </div>
        <div class="stat-hint">
          门槛 ≥70% · 可灰度观察
          <template v-if="o.question_adopt_rate?.insufficient"> · 生成题量 {{ o.question_adopt_rate?.samples ?? 0 }}</template>
        </div>
      </div>
      <div class="stat">
        <div class="stat-label">HR 均摊耗时</div>
        <div class="stat-value">
          {{ o.hr_avg_seconds ?? '—' }}<span class="stat-unit">秒/份</span>
        </div>
        <div class="stat-hint">总耗时除以总到岸量，非除以人工复核量</div>
      </div>
      <div class="stat">
        <div class="stat-label">单份处理成本</div>
        <div class="stat-value" :style="{ color: costColor }">
          ¥{{ num(o.cost?.avg_cost_cny, 4) }}
        </div>
        <div class="stat-hint">目标 ≤¥0.16 · 连续 3 天超 ¥0.25 告警</div>
      </div>
      <div class="stat">
        <div class="stat-label">模型调用</div>
        <div class="stat-value sm">{{ o.cost?.calls ?? 0 }}<span class="stat-unit">次</span></div>
        <div class="stat-hint">
          平均 {{ o.cost?.avg_latency_ms ?? 0 }} ms
          <span v-if="o.cost?.degraded" style="color: var(--warn)"> · 降级 {{ o.cost.degraded }} 次</span>
        </div>
      </div>
    </div>

    <div v-if="o.alerts?.length" class="alert alert-warn mb-3">
      <span class="alert-icon">⚠</span>
      <div>
        <b>分层占比偏离告警</b>
        <div v-for="(a, i) in o.alerts" :key="i" class="tiny mt-1">{{ a }}</div>
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <div class="card-head">
          <span class="card-title">分层占比趋势</span>
          <span class="card-desc">标注模型版本与阈值变更时间点，便于归因</span>
        </div>
        <div ref="trendEl" class="chart" />
        <div class="tiny muted mt-1">
          三档预期占比：高分档 9%、中间档 34%、低分档 57%。指标波动可与变更点对齐做归因，
          避免把配置调整误判为模型退化。
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <span class="card-title">招聘漏斗</span>
        </div>
        <div ref="funnelEl" class="chart" />
      </div>

      <div class="card">
        <div class="card-head">
          <span class="card-title">当前分层分布</span>
        </div>
        <div ref="tierEl" class="chart" />
      </div>

      <div class="card">
        <div class="card-head">
          <span class="card-title">埋点事件</span>
          <span class="card-desc">只埋能驱动决策的事件</span>
        </div>
        <div v-if="ev.verdict" class="alert mb-2" :class="ev.verdict.includes('危险') || ev.verdict.includes('风险') ? 'alert-warn' : 'alert-ok'">
          <span class="alert-icon">◆</span>
          <div>
            <div><b>evidence_click 设计假设验证</b></div>
            <div class="mt-1">{{ ev.verdict }}</div>
          </div>
        </div>
        <div v-for="(v, k) in ev.counts" :key="k" class="ev-row">
          <span class="mono tiny">{{ k }}</span>
          <span class="num small bold">{{ v }}</span>
        </div>
        <div v-if="!Object.keys(ev.counts || {}).length" class="empty" style="padding: 20px">
          <div class="tiny">暂无埋点数据。执行复核、查看证据等操作后会产生事件。</div>
        </div>
      </div>
    </div>

    <div class="card mt-3">
      <div class="card-head">
        <span class="card-title">回流样本与误杀线索</span>
        <span class="card-desc">捞回后通过面试的案例单独成列表，建议每周由 HR 负责人复盘</span>
      </div>

      <div class="grid grid-2">
        <div>
          <div class="tiny muted mb-1">回流样本构成</div>
          <div v-for="(v, k) in rf.counts" :key="k" class="ev-row">
            <span class="tiny">{{ KIND[k] || k }}</span>
            <span class="num small bold">{{ v }}</span>
          </div>
          <div v-if="Object.keys(rf.reject_reasons || {}).length" class="mt-2">
            <div class="tiny muted mb-1">否决原因分布（用于优化样本池）</div>
            <div v-for="(v, k) in rf.reject_reasons" :key="k" class="ev-row">
              <span class="tiny">{{ k }}</span>
              <span class="num small bold">{{ v }}</span>
            </div>
          </div>
        </div>
        <div>
          <div class="tiny muted mb-1">误杀线索（捞回案例）</div>
          <div v-if="rf.rescue_cases?.length">
            <div v-for="(r, i) in rf.rescue_cases" :key="i" class="rescue-row">
              <div class="row-between">
                <span class="small">候选人 #{{ r.candidate_id }}</span>
                <span class="tag tag-warn tiny">原分档 {{ r.original_tier || '—' }}</span>
              </div>
              <div v-if="r.reason" class="tiny muted mt-1">{{ r.reason }}</div>
            </div>
          </div>
          <div v-else class="tiny muted">暂无捞回记录。捞回后该列表会显示误杀线索。</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import * as echarts from 'echarts'
import { boardApi } from '../api'
import { num, pct } from '../api/labels'
import { toast } from '../components/toast'

const o = ref<any>({})
const ev = ref<any>({})
const rf = ref<any>({})
const trendEl = ref<HTMLElement | null>(null)
const funnelEl = ref<HTMLElement | null>(null)
const tierEl = ref<HTMLElement | null>(null)
const charts: echarts.ECharts[] = []

const KIND: Record<string, string> = {
  review: '复核动作', rescue: '待定池捞回', interview_score: '面试评分',
  exam: '考核成绩', probation: '试用期表现',
}

const costColor = computed(() => {
  const c = o.value.cost?.avg_cost_cny || 0
  if (c > 0.25) return 'var(--danger)'
  if (c > 0.16) return 'var(--warn)'
  return 'var(--ok)'
})

function rateColor(v: number | null | undefined, threshold: number) {
  if (v === null || v === undefined) return 'var(--text-2)'
  const n = threshold > 1 ? v : v * 100
  const t = threshold > 1 ? threshold : threshold * 100
  return n >= t ? 'var(--ok)' : 'var(--danger)'
}

function theme() {
  const cs = getComputedStyle(document.documentElement)
  const g = (k: string) => cs.getPropertyValue(k).trim()
  return {
    text: g('--text-1'), text2: g('--text-2'), border: g('--border'),
    bg: g('--bg-1'), brand: g('--brand'), ok: g('--ok'),
    warn: g('--warn'), danger: g('--danger'), purple: g('--purple'),
  }
}

function base() {
  const t = theme()
  return {
    t,
    grid: { left: 42, right: 16, top: 30, bottom: 28 },
    textStyle: { color: t.text, fontSize: 11.5 },
    tip: {
      trigger: 'axis' as const,
      backgroundColor: t.bg, borderColor: t.border,
      textStyle: { color: t.text, fontSize: 12 },
    },
  }
}

async function renderCharts() {
  await nextTick()
  charts.forEach((c) => c.dispose())
  charts.length = 0
  const b = base()

  // 趋势
  if (trendEl.value) {
    const c = echarts.init(trendEl.value)
    const trend = await boardApi.trend(30)
    const line = trend.find((x: any) => x.type === 'outline')?.data || []
    const markers = trend.find((x: any) => x.type === 'markers')?.data || []
    c.setOption({
      grid: b.grid, textStyle: b.textStyle, tooltip: b.tip,
      legend: { data: ['高分档', '中间档', '低分档'], textStyle: { color: b.t.text, fontSize: 11 }, top: 0 },
      xAxis: {
        type: 'category', data: line.map((d: any) => d.day.slice(5)),
        axisLine: { lineStyle: { color: b.t.border } },
        axisLabel: { color: b.t.text2, fontSize: 10 },
      },
      yAxis: {
        type: 'value', splitLine: { lineStyle: { color: b.t.border, type: 'dashed' } },
        axisLabel: { color: b.t.text2, fontSize: 10 },
      },
      series: [
        { name: '高分档', type: 'line', smooth: true, data: line.map((d: any) => d['高分档']), itemStyle: { color: b.t.ok }, areaStyle: { opacity: 0.08 } },
        { name: '中间档', type: 'line', smooth: true, data: line.map((d: any) => d['中间档']), itemStyle: { color: b.t.warn }, areaStyle: { opacity: 0.08 } },
        { name: '低分档', type: 'line', smooth: true, data: line.map((d: any) => d['低分档']), itemStyle: { color: b.t.text2 }, areaStyle: { opacity: 0.08 } },
      ],
      ...(markers.length ? {
        series: [
          { name: '高分档', type: 'line', smooth: true, data: line.map((d: any) => d['高分档']), itemStyle: { color: b.t.ok }, markPoint: { data: markers.map((m: any) => ({ name: m.label, xAxis: m.day.slice(5), yAxis: 0, value: '变更' })), symbolSize: 26, itemStyle: { color: b.t.purple }, label: { fontSize: 9, color: '#fff' } } },
          { name: '中间档', type: 'line', smooth: true, data: line.map((d: any) => d['中间档']), itemStyle: { color: b.t.warn } },
          { name: '低分档', type: 'line', smooth: true, data: line.map((d: any) => d['低分档']), itemStyle: { color: b.t.text2 } },
        ],
      } : {}),
    })
    charts.push(c)
  }

  // 漏斗
  if (funnelEl.value) {
    const c = echarts.init(funnelEl.value)
    const f = await boardApi.funnel()
    c.setOption({
      grid: { left: 78, right: 30, top: 10, bottom: 20 },
      textStyle: b.textStyle, tooltip: { ...b.tip, trigger: 'item' },
      xAxis: { type: 'value', splitLine: { lineStyle: { color: b.t.border, type: 'dashed' } }, axisLabel: { color: b.t.text2, fontSize: 10 } },
      yAxis: {
        type: 'category', inverse: true, data: f.map((x: any) => x.stage),
        axisLine: { lineStyle: { color: b.t.border } }, axisLabel: { color: b.t.text, fontSize: 11 },
      },
      series: [{
        type: 'bar', data: f.map((x: any) => x.value), barWidth: '52%',
        itemStyle: { color: b.t.brand, borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', color: b.t.text, fontSize: 11 },
      }],
    })
    charts.push(c)
  }

  // 分层分布
  if (tierEl.value) {
    const c = echarts.init(tierEl.value)
    const tc = o.value.tier_counts || {}
    const data = Object.keys(tc).map((k) => ({ name: k, value: tc[k] }))
    c.setOption({
      textStyle: b.textStyle,
      tooltip: { ...b.tip, trigger: 'item' },
      legend: { bottom: 0, textStyle: { color: b.t.text, fontSize: 11 } },
      series: [{
        type: 'pie', radius: ['46%', '70%'], center: ['50%', '44%'],
        data,
        label: { color: b.t.text, fontSize: 11, formatter: '{b}\n{c} 人' },
        itemStyle: { borderColor: b.t.bg, borderWidth: 2 },
        color: [b.t.ok, b.t.warn, b.t.text2, b.t.purple],
      }],
    })
    charts.push(c)
  }
}

function onResize() { charts.forEach((c) => c.resize()) }

async function load() {
  try {
    const [ov, evd, rfd] = await Promise.all([
      boardApi.overview(), boardApi.events(14), boardApi.reflow(),
    ])
    o.value = ov; ev.value = evd; rf.value = rfd
    await renderCharts()
  } catch (e) {
    toast.err(e, '加载看板失败')
  }
}

onMounted(async () => {
  await load()
  window.addEventListener('resize', onResize)
})
onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  charts.forEach((c) => c.dispose())
})
</script>

<style scoped>
.chart { height: 250px; width: 100%; }
.ev-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 5px 0; border-bottom: 1px dashed var(--border);
}
.ev-row:last-child { border-bottom: none; }
.rescue-row {
  padding: 8px 10px; border: 1px solid var(--border);
  border-left: 3px solid var(--warn);
  border-radius: var(--radius); margin-bottom: 7px; background: var(--bg-0);
}
</style>
