<template>
  <div class="auth-wrap">
    <!-- 左：品牌与说明 -->
    <div class="auth-hero">
      <div class="hero-inner">
        <div class="hero-brand">
          <span class="brand-dot" />
          <span>AI 招聘与人才发展平台</span>
        </div>
        <h1 class="hero-title">以岗位能力模型为底座的<br />人机协作招聘平台</h1>
        <p class="hero-desc">
          在简历筛选、面试提问、培训考核三个环节提供<b>带证据的判断建议</b>，
          人保留最终决策权。
        </p>

        <div class="hero-points">
          <div v-for="p in POINTS" :key="p.t" class="point">
            <span class="point-icon">{{ p.i }}</span>
            <div>
              <div class="point-title">{{ p.t }}</div>
              <div class="point-desc">{{ p.d }}</div>
            </div>
          </div>
        </div>

        <div class="hero-demo">
          <div class="hero-demo-title">演示账号（密码均为 123456）</div>
          <div class="demo-grid">
            <button
              v-for="d in DEMO" :key="d.u"
              class="demo-chip" :class="{ on: form.userid === d.u }"
              @click="fillDemo(d.u)"
            >
              <span class="demo-role">{{ d.r }}</span>
              <span class="demo-user mono">{{ d.u }}</span>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 右：表单 -->
    <div class="auth-panel">
      <div class="panel-inner">
        <div class="tabs">
          <button class="tab" :class="{ on: mode === 'login' }" @click="mode = 'login'">登录</button>
          <button class="tab" :class="{ on: mode === 'register' }" @click="mode = 'register'">注册</button>
        </div>

        <!-- 登录 -->
        <form v-if="mode === 'login'" @submit.prevent="doLogin">
          <div class="field">
            <label class="field-label">账号</label>
            <input v-model.trim="form.userid" class="input" autocomplete="username"
                   placeholder="例如 admin" @keyup.enter="doLogin" />
          </div>
          <div class="field">
            <label class="field-label">密码</label>
            <div class="pwd-wrap">
              <input v-model="form.password" :type="showPwd ? 'text' : 'password'"
                     class="input" autocomplete="current-password"
                     placeholder="演示账号密码 123456" @keyup.enter="doLogin" />
              <button type="button" class="pwd-eye" @click="showPwd = !showPwd">
                {{ showPwd ? '隐藏' : '显示' }}
              </button>
            </div>
          </div>

          <div v-if="error" class="alert alert-danger mb-2">
            <span class="alert-icon">✕</span><div>{{ error }}</div>
          </div>

          <VButton variant="primary" size="lg" style="width: 100%" :loading="busy" @click="doLogin">
            登录
          </VButton>

          <div class="auth-hint">
            初始超管账号 <b class="mono">admin</b> / <b class="mono">123456</b>，
            它拥有全部权限并负责创建其它账号。
          </div>
        </form>

        <!-- 注册 -->
        <form v-else @submit.prevent="doRegister">
          <div class="field">
            <label class="field-label">账号<span class="req">*</span></label>
            <input v-model.trim="form.userid" class="input" placeholder="字母、数字、下划线，如 zhangsan" />
            <div class="field-hint">登录用，创建后不可修改</div>
          </div>
          <div class="field">
            <label class="field-label">姓名<span class="req">*</span></label>
            <input v-model.trim="form.name" class="input" placeholder="真实姓名" />
          </div>
          <div class="field">
            <label class="field-label">密码<span class="req">*</span></label>
            <input v-model="form.password" type="password" class="input" placeholder="至少 6 位" />
          </div>
          <div class="field">
            <label class="field-label">确认密码<span class="req">*</span></label>
            <input v-model="form.password2" type="password" class="input" placeholder="再输一次" />
          </div>

          <div class="field">
            <label class="field-label">角色<span class="req">*</span></label>
            <div class="role-list">
              <label
                v-for="r in signupRoles" :key="r.role"
                class="role-item" :class="{ on: form.role === r.role }"
              >
                <input type="radio" :value="r.role" v-model="form.role" hidden />
                <div class="row-between">
                  <span class="bold small">{{ r.role }}</span>
                  <span v-if="form.role === r.role" class="tag tag-blue tiny">已选</span>
                </div>
                <div class="tiny muted">{{ r.description }}</div>
              </label>
            </div>
            <div class="field-hint">
              系统管理员等管理类角色不支持自助注册，需由 admin 账号创建。
            </div>
          </div>

          <div class="field">
            <label class="field-label">业务线</label>
            <select v-model="form.business_line" class="select">
              <option value="">不限</option>
              <option v-for="b in lines" :key="b.name" :value="b.name">{{ b.name }}</option>
            </select>
            <div class="field-hint">决定你能看到哪些候选人（跨业务线不可见）</div>
          </div>

          <div class="field">
            <label class="field-label">部门</label>
            <input v-model.trim="form.department" class="input" placeholder="例如 交易研发部" />
          </div>

          <div v-if="error" class="alert alert-danger mb-2">
            <span class="alert-icon">✕</span><div>{{ error }}</div>
          </div>

          <VButton variant="primary" size="lg" style="width: 100%" :loading="busy" @click="doRegister">
            注册并登录
          </VButton>
        </form>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import VButton from '../components/VButton.vue'
import { auth } from '../api/auth'
import { lineApi } from '../api'
import { toast } from '../components/toast'

const router = useRouter()

const POINTS = [
  { i: '◆', t: '带证据的判断，不是黑箱分数', d: '每条 AI 结论都能点开看引用的简历原文' },
  { i: '⚖', t: '人保留最终决策权', d: '系统不提供「已拒绝」动作，低分只进待定池' },
  { i: '🔒', t: '权限与数据隔离', d: '面试官仅见被指派候选人，管理员不可看简历正文' },
  { i: '◪', t: '可度量、可追溯', d: '全链路决策日志与效果看板，模型版本可回溯' },
]

const DEMO = [
  { r: '超级管理员', u: 'admin' },
  { r: '招聘 HR', u: 'hr1' },
  { r: 'HR 负责人', u: 'hr_lead' },
  { r: '业务面试官', u: 'interviewer1' },
  { r: '用人经理', u: 'mgr1' },
  { r: '带教人', u: 'mentor1' },
  { r: '法务审计', u: 'legal1' },
]

// 业务线来自字典接口（注册前即可访问），不在前端写死
const lines = ref<{ name: string }[]>([])

const mode = ref<'login' | 'register'>('login')
const busy = ref(false)
const error = ref('')
const showPwd = ref(false)
const roles = ref<any[]>([])

const form = ref({
  userid: '', password: '', password2: '', name: '', role: '招聘HR',
  business_line: '', department: '',
})

const signupRoles = computed(() => roles.value.filter((r) => r.self_signup))

function fillDemo(u: string) {
  form.value.userid = u
  form.value.password = '123456'
  error.value = ''
}

async function doLogin() {
  error.value = ''
  if (!form.value.userid || !form.value.password) {
    error.value = '请输入账号和密码'
    return
  }
  busy.value = true
  try {
    const u = await auth.login(form.value.userid, form.value.password)
    toast.ok(`欢迎回来，${u.name}`, `当前身份：${u.role}`)
    router.replace('/workbench')
  } catch (e: any) {
    error.value = e?.friendly || '登录失败'
  } finally {
    busy.value = false
  }
}

async function doRegister() {
  error.value = ''
  if (!form.value.userid || !form.value.name) {
    error.value = '请填写账号和姓名'
    return
  }
  if (form.value.password.length < 6) {
    error.value = '密码至少 6 位'
    return
  }
  if (form.value.password !== form.value.password2) {
    error.value = '两次输入的密码不一致'
    return
  }
  busy.value = true
  try {
    const u = await auth.register({
      userid: form.value.userid,
      name: form.value.name,
      password: form.value.password,
      role: form.value.role,
      business_line: form.value.business_line,
      department: form.value.department,
    })
    toast.ok(`注册成功，${u.name}`, `当前身份：${u.role}`)
    router.replace('/workbench')
  } catch (e: any) {
    error.value = e?.friendly || '注册失败'
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  lineApi.list().then((r: any[]) => { lines.value = r }).catch(() => { lines.value = [] })
  try {
    roles.value = await auth.roles()
  } catch {
    // 后端未就绪时用兜底列表，至少让界面可用
    roles.value = [
      { role: '招聘HR', description: '日常复核与面试安排', self_signup: true },
      { role: '业务面试官', description: '仅被指派候选人，出题与评分', self_signup: true },
      { role: '用人经理', description: '本部门在招岗位候选人', self_signup: true },
      { role: '带教人', description: '培训方案与考核终评', self_signup: true },
    ]
  }
})
</script>

<style scoped>
.auth-wrap { display: flex; min-height: 100vh; }

/* 左侧品牌区 */
.auth-hero {
  flex: 1.15; padding: 54px 52px;
  background:
    radial-gradient(1000px 500px at 12% -8%, var(--brand-dim), transparent 62%),
    var(--bg-1);
  border-right: 1px solid var(--border);
  display: flex; align-items: center;
  overflow-y: auto;
}
.hero-inner { width: 100%; max-width: 560px; margin: 0 auto; }
.hero-brand {
  display: flex; align-items: center; gap: 9px;
  font-size: 14px; font-weight: 620; margin-bottom: 34px;
}
.hero-title {
  font-size: 29px; line-height: 1.42; font-weight: 680;
  letter-spacing: -0.4px; margin: 0 0 14px;
}
.hero-desc { font-size: 14px; color: var(--text-1); line-height: 1.85; margin: 0 0 30px; }

.hero-points { display: flex; flex-direction: column; gap: 15px; margin-bottom: 34px; }
.point { display: flex; gap: 12px; align-items: flex-start; }
.point-icon {
  width: 26px; height: 26px; flex-shrink: 0; border-radius: 7px;
  background: var(--brand-dim); color: var(--brand-text);
  display: flex; align-items: center; justify-content: center; font-size: 13px;
}
.point-title { font-size: 13.5px; font-weight: 570; margin-bottom: 2px; }
.point-desc { font-size: 12.5px; color: var(--text-2); line-height: 1.6; }

.hero-demo {
  padding: 16px; border: 1px solid var(--border);
  border-radius: var(--radius-lg); background: var(--bg-0);
}
.hero-demo-title { font-size: 12px; color: var(--text-2); margin-bottom: 11px; }
.demo-grid { display: flex; flex-wrap: wrap; gap: 7px; }
.demo-chip {
  display: flex; flex-direction: column; gap: 2px; align-items: flex-start;
  padding: 6px 11px; border: 1px solid var(--border);
  border-radius: var(--radius); background: var(--bg-1);
  cursor: pointer; font-family: inherit; transition: all 0.13s;
}
.demo-chip:hover { border-color: var(--brand); background: var(--bg-2); }
.demo-chip.on { border-color: var(--brand); background: var(--ai-bg); }
.demo-role { font-size: 11.5px; color: var(--text-2); }
.demo-user { font-size: 11.5px; color: var(--text-0); }

/* 右侧表单区 */
.auth-panel {
  width: 470px; flex-shrink: 0; padding: 54px 44px;
  display: flex; align-items: center; background: var(--bg-0);
  overflow-y: auto;
}
.panel-inner { width: 100%; }

.tabs {
  display: flex; gap: 4px; margin-bottom: 22px;
  border-bottom: 1px solid var(--border);
}
.tab {
  flex: 1; padding: 10px 0; background: none; border: none;
  color: var(--text-2); font-size: 14px; font-family: inherit;
  cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -1px;
}
.tab:hover { color: var(--text-0); }
.tab.on { color: var(--brand-text); border-bottom-color: var(--brand); font-weight: 580; }

.pwd-wrap { position: relative; }
.pwd-eye {
  position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
  background: none; border: none; color: var(--text-2);
  font-size: 11.5px; cursor: pointer; font-family: inherit; padding: 3px 6px;
}
.pwd-eye:hover { color: var(--text-0); }

.role-list { display: flex; flex-direction: column; gap: 7px; }
.role-item {
  padding: 9px 11px; border: 1px solid var(--border);
  border-radius: var(--radius); cursor: pointer; transition: all 0.13s;
}
.role-item:hover { background: var(--bg-2); }
.role-item.on { border-color: var(--brand); background: var(--ai-bg); }

.auth-hint {
  margin-top: 18px; padding: 11px 13px; font-size: 12px;
  color: var(--text-2); line-height: 1.75;
  background: var(--bg-1); border: 1px solid var(--border);
  border-radius: var(--radius); text-align: center;
}

@media (max-width: 1020px) {
  .auth-hero { display: none; }
  .auth-panel { width: 100%; }
}
</style>
