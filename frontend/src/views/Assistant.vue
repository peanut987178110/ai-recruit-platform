<template>
  <div class="asst-wrap">
    <div class="card chat-card">
      <div class="card-head">
        <span class="card-title">智能体助手</span>
        <span class="card-desc">用自然语言描述需求，助手会自己选择并串联技能</span>
        <VButton size="sm" variant="ghost" class="ml-auto" @click="clear">清空对话</VButton>
      </div>

      <div ref="listEl" class="chat-list">
        <div v-if="!messages.length" class="chat-intro">
          <div class="intro-icon">✦</div>
          <div class="intro-title">我可以帮你做什么</div>
          <div class="intro-desc">
            我会调用平台里的技能完成任务，而不是凭空回答。<br />
            涉及改变候选人状态的操作（如捞回），我只生成待确认动作，需你在界面上点击确认。
          </div>
          <div class="sugg">
            <button v-for="(s, i) in SUGGESTIONS" :key="i" class="sugg-item" @click="send(s)">
              {{ s }}
            </button>
          </div>
        </div>

        <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
          <div class="msg-avatar">{{ m.role === 'user' ? '你' : '✦' }}</div>
          <div class="msg-body">
            <div v-if="m.steps?.length" class="steps">
              <div v-for="(s, si) in m.steps" :key="si" class="step">
                <span class="step-dot" />{{ s }}
              </div>
            </div>
            <div class="msg-content" v-html="render(m.content)" />
            <div v-if="m.pending?.length" class="pending">
              <div v-for="(p, pi) in m.pending" :key="pi" class="pending-item">
                <span class="tag tag-warn">待确认动作</span>
                <span class="small">{{ p.label }}</span>
                <div class="tiny muted">{{ p.note }}</div>
              </div>
            </div>
            <div v-if="m.degraded" class="tiny" style="color: var(--warn)">⚠ 本次回答走了降级路径</div>
          </div>
        </div>

        <div v-if="thinking" class="msg assistant">
          <div class="msg-avatar">✦</div>
          <div class="msg-body">
            <div class="row" style="gap: 8px">
              <span class="spinner" /><span class="small muted">正在调用技能…</span>
            </div>
          </div>
        </div>
      </div>

      <div class="chat-input">
        <textarea
          v-model="text" class="textarea" rows="2"
          placeholder="例如：帮我看一下张伟这个候选人怎么样（Enter 发送，Shift+Enter 换行）"
          @keydown.enter.exact.prevent="send()"
        />
        <VButton variant="primary" :disabled="!text.trim() || thinking" :loading="thinking" @click="send()">
          发送
        </VButton>
      </div>
    </div>

    <div class="side">
      <div class="card">
        <div class="card-head">
          <span class="card-title">可用技能</span>
          <span class="tag tag-gray ml-auto">{{ skills.length }}</span>
        </div>
        <div class="tiny muted mb-2">
          每个技能都有两个入口：被我自动调用，或由界面按钮直接触发。
        </div>
        <div v-for="s in skills" :key="s.name" class="skill" @click="send(skillPrompt(s))">
          <div class="row-between">
            <span class="small bold">{{ s.label }}</span>
            <span class="tag tag-gray tiny">{{ s.category }}</span>
          </div>
          <div class="tiny muted mt-1">{{ s.description }}</div>
          <div v-if="s.needs_human" class="tiny mt-1" style="color: var(--warn)">
            该技能会改变数据状态，需人工确认后生效
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><span class="card-title">能力边界</span></div>
        <div class="bound">
          <div class="bound-item ok">
            <span>✓</span>
            <div>
              <div class="small bold">把信息结构化</div>
              <div class="tiny muted">解析简历、抽取能力项证据</div>
            </div>
          </div>
          <div class="bound-item ok">
            <span>✓</span>
            <div>
              <div class="small bold">给出带证据的建议</div>
              <div class="tiny muted">每条结论都能回指原文位置</div>
            </div>
          </div>
          <div class="bound-item ok">
            <span>✓</span>
            <div>
              <div class="small bold">把不确定的交给人</div>
              <div class="tiny muted">置信度不足时转人工复核</div>
            </div>
          </div>
          <div class="bound-item no">
            <span>✕</span>
            <div>
              <div class="small bold">不做终局决策</div>
              <div class="tiny muted">
                不替 HR 拒绝候选人、不替面试官打分、不替带教人给终评。
                自动拒信、情绪推断、终面录用决策均不提供。
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { marked } from 'marked'
import VButton from '../components/VButton.vue'
import { agentApi } from '../api'
import { toast } from '../components/toast'

interface Msg {
  role: 'user' | 'assistant'
  content: string
  steps?: string[]
  pending?: any[]
  degraded?: boolean
}

const SUGGESTIONS = [
  '现在有多少候选人在待复核？',
  '解释一下张伟的打分依据',
  '我们的订单幂等规范是怎么要求的？',
  '帮我统计各岗位的分层占比',
]

const messages = ref<Msg[]>([])
const text = ref('')
const thinking = ref(false)
const skills = ref<any[]>([])
const listEl = ref<HTMLElement | null>(null)

function render(md: string) {
  try {
    return marked.parse(md || '', { breaks: true, async: false }) as string
  } catch {
    return md
  }
}

function skillPrompt(s: any) {
  return `请执行「${s.label}」`
}

async function send(preset?: string) {
  const content = (preset ?? text.value).trim()
  if (!content || thinking.value) return

  messages.value.push({ role: 'user', content })
  text.value = ''
  thinking.value = true
  await scroll()

  try {
    const history = messages.value.slice(0, -1).map((m) => ({ role: m.role, content: m.content }))
    const r = await agentApi.chat(content, history)
    messages.value.push({
      role: 'assistant',
      content: r.reply || '（无内容）',
      steps: r.steps || [],
      pending: r.pending_actions || [],
      degraded: r.degraded,
    })
  } catch (e) {
    toast.err(e, '助手调用失败')
    messages.value.push({
      role: 'assistant',
      content: '调用失败。你可以直接使用左侧的功能页完成对应任务。',
    })
  } finally {
    thinking.value = false
    await scroll()
  }
}

async function scroll() {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}

function clear() {
  messages.value = []
}

onMounted(async () => {
  try { skills.value = (await agentApi.skills()).skills } catch { /* ignore */ }
})
</script>

<style scoped>
.asst-wrap { display: grid; grid-template-columns: 1fr 340px; gap: 14px; height: 100%; }
@media (max-width: 1100px) { .asst-wrap { grid-template-columns: 1fr; } }

.chat-card { display: flex; flex-direction: column; height: calc(100vh - 130px); }
.chat-list { flex: 1; overflow-y: auto; padding: 6px 0; }

.chat-intro { text-align: center; padding: 40px 20px; }
.intro-icon { font-size: 34px; color: var(--brand); margin-bottom: 12px; }
.intro-title { font-size: 15px; font-weight: 620; margin-bottom: 7px; }
.intro-desc { font-size: 12.5px; color: var(--text-2); line-height: 1.85; max-width: 440px; margin: 0 auto; }
.sugg { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-top: 20px; }
.sugg-item {
  padding: 7px 13px; border: 1px solid var(--border); border-radius: 16px;
  background: var(--bg-0); color: var(--text-1); font-size: 12.5px;
  cursor: pointer; font-family: inherit; transition: all 0.13s;
}
.sugg-item:hover { border-color: var(--brand); color: var(--brand-text); background: var(--ai-bg); }

.msg { display: flex; gap: 10px; padding: 11px 4px; }
.msg-avatar {
  width: 27px; height: 27px; border-radius: 7px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 620;
}
.msg.user .msg-avatar { background: var(--bg-3); color: var(--text-1); }
.msg.assistant .msg-avatar { background: var(--brand); color: #fff; }
.msg-body { flex: 1; min-width: 0; }
.msg-content { font-size: 13.5px; line-height: 1.85; word-break: break-word; }
.msg-content :deep(p) { margin: 0 0 8px; }
.msg-content :deep(ul), .msg-content :deep(ol) { margin: 6px 0; padding-left: 20px; }
.msg-content :deep(code) {
  background: var(--bg-3); padding: 1px 5px; border-radius: 4px;
  font-family: Consolas, monospace; font-size: 12px;
}
.msg-content :deep(table) { border-collapse: collapse; font-size: 12.5px; margin: 8px 0; }
.msg-content :deep(th), .msg-content :deep(td) {
  border: 1px solid var(--border); padding: 5px 9px;
}
.msg-content :deep(strong) { color: var(--text-0); font-weight: 620; }

.steps { display: flex; flex-direction: column; gap: 3px; margin-bottom: 8px; }
.step {
  display: flex; align-items: center; gap: 7px;
  font-size: 11.5px; color: var(--text-2);
}
.step-dot {
  width: 5px; height: 5px; border-radius: 50%; background: var(--brand); flex-shrink: 0;
}
.pending {
  margin-top: 9px; padding: 10px 12px; background: var(--warn-bg);
  border: 1px solid var(--warn); border-radius: var(--radius);
}
.pending-item + .pending-item { margin-top: 8px; }

.chat-input {
  display: flex; gap: 9px; padding-top: 12px;
  border-top: 1px solid var(--border); align-items: flex-end;
}
.chat-input .textarea { flex: 1; min-height: 52px; }

.side { display: flex; flex-direction: column; gap: 14px; overflow-y: auto; }
.skill {
  padding: 9px 11px; border: 1px solid var(--border);
  border-radius: var(--radius); margin-bottom: 7px; cursor: pointer;
  background: var(--bg-0); transition: all 0.13s;
}
.skill:hover { border-color: var(--brand); background: var(--bg-2); }

.bound { display: flex; flex-direction: column; gap: 9px; }
.bound-item {
  display: flex; gap: 9px; padding: 9px 11px;
  border-radius: var(--radius); border: 1px solid var(--border);
}
.bound-item.ok { border-left: 3px solid var(--ok); }
.bound-item.no { border-left: 3px solid var(--danger); }
.bound-item > span { flex-shrink: 0; margin-top: 1px; }
.bound-item.ok > span { color: var(--ok); }
.bound-item.no > span { color: var(--danger); }
</style>
