<template>
  <!-- 登录页与考试页全屏，不套外壳 —— 考试中不该被侧边导航打断 -->
  <router-view v-if="isLogin || isFullscreen" />

  <div v-else class="layout">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-title">
          <span class="brand-dot" />
          <span class="brand-text">AI 招聘平台</span>
        </div>
        <div class="brand-sub brand-text">人才发展 · V1.0</div>
      </div>

      <nav class="nav">
        <template v-for="g in visibleNav" :key="g.group">
          <div class="nav-group">{{ g.group }}</div>
          <button
            v-for="it in g.items" :key="it.path"
            class="nav-item" :class="{ active: isActive(it.path) }"
            @click="go(it.path)"
          >
            <span class="ico">{{ it.icon }}</span>
            <span class="nav-label">{{ it.label }}</span>
            <span v-if="badge(it)" class="nav-badge">{{ badge(it) }}</span>
          </button>
        </template>
      </nav>

      <div class="sidebar-foot">
        <button class="user-box" @click="menuOpen = !menuOpen">
          <div class="user-avatar">{{ (user?.name || '?').slice(0, 1) }}</div>
          <div style="flex: 1; min-width: 0; text-align: left">
            <div class="user-name truncate">{{ user?.name || '未登录' }}</div>
            <div class="user-role truncate">{{ user?.role }}</div>
          </div>
          <span class="user-caret">⋯</span>
        </button>

        <div v-if="menuOpen" class="user-menu">
          <div class="um-head">
            <div class="small bold">{{ user?.name }}</div>
            <div class="tiny muted mono">{{ user?.userid }}</div>
            <div v-if="user?.business_line" class="tiny muted">{{ user.business_line }}</div>
          </div>
          <button class="um-item" @click="pwdOpen = true; menuOpen = false">修改密码</button>
          <button v-if="canManage" class="um-item" @click="go('/accounts'); menuOpen = false">
            账号管理
          </button>
          <button class="um-item danger" @click="doLogout">退出登录</button>
        </div>

        <VButton variant="ghost" size="sm" class="theme-btn" @click="toggleTheme">
          {{ theme === 'dark' ? '☀ 浅色主题' : '☾ 深色主题' }}
        </VButton>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div><h1>{{ route.meta.title || 'AI 招聘与人才发展平台' }}</h1></div>
        <span class="topbar-sub">{{ route.meta.sub }}</span>
        <div class="topbar-right">
          <span v-if="status && !status.llm?.enabled" class="tag tag-danger" title="模型网关不可用，平台已降级">
            ⚠ 模型不可用
          </span>
          <span v-else-if="status" class="tag tag-ai" :title="`自动选型：${status.llm?.auto_selected?.medium}`">
            ◆ 模型已连接 · {{ status.llm?.available_models }} 个可选
          </span>
          <span v-if="status" class="tag tag-gray" title="当前会话累计调用成本">
            ¥{{ Number(status.usage?.cost_cny || 0).toFixed(2) }}
          </span>
        </div>
      </header>

      <main class="content" :class="{ flush: route.path.startsWith('/candidate/') }">
        <router-view v-slot="{ Component }">
          <component :is="Component" :user="user" @refresh-status="loadStatus" />
        </router-view>
      </main>
    </div>

    <VModal v-if="pwdOpen" title="修改密码" @close="pwdOpen = false">
      <div class="field">
        <label class="field-label">原密码</label>
        <input v-model="pwd.old" type="password" class="input" />
      </div>
      <div class="field">
        <label class="field-label">新密码</label>
        <input v-model="pwd.new1" type="password" class="input" placeholder="至少 6 位" />
      </div>
      <div class="field">
        <label class="field-label">确认新密码</label>
        <input v-model="pwd.new2" type="password" class="input" />
      </div>
      <div v-if="pwdErr" class="alert alert-danger"><span class="alert-icon">✕</span><div>{{ pwdErr }}</div></div>
      <template #foot>
        <VButton @click="pwdOpen = false">取消</VButton>
        <VButton variant="primary" :loading="pwdBusy" @click="doChangePwd">确认修改</VButton>
      </template>
    </VModal>

    <VToast />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import VButton from './components/VButton.vue'
import VModal from './components/VModal.vue'
import VToast from './components/VToast.vue'
import { api, currentUser } from './api'
import { auth } from './api/auth'
import { toast } from './components/toast'

const route = useRoute()
const router = useRouter()

const status = ref<any>(null)
const theme = ref(localStorage.getItem('theme') || 'dark')
const menuOpen = ref(false)
const pwdOpen = ref(false)
const pwdBusy = ref(false)
const pwdErr = ref('')
const pwd = ref({ old: '', new1: '', new2: '' })

const isLogin = computed(() => route.path === '/login')
const isFullscreen = computed(() => route.meta.fullscreen === true)
const user = computed(() => currentUser.value)
const canManage = computed(() => !!user.value?.is_super || user.value?.role === 'HR负责人')

const NAV = [
  {
    group: '招聘流程',
    items: [
      { path: '/workbench', label: '候选人工作台', icon: '▤', roles: ['招聘HR', 'HR负责人', '用人经理', '业务面试官'] },
      { path: '/pool', label: '待定池', icon: '◲', roles: ['招聘HR', 'HR负责人'] },
      { path: '/interviews', label: '面试日程', icon: '◷', roles: ['招聘HR', 'HR负责人', '用人经理', '业务面试官'] },
      { path: '/training', label: '培训考核', icon: '◈', roles: ['带教人', '招聘HR', 'HR负责人'] },
      { path: '/training', label: '我的培训', icon: '◈', roles: ['新人'] },
    ],
  },
  {
    group: '配置与洞察',
    items: [
      { path: '/models', label: '岗位能力模型', icon: '◆', roles: ['招聘HR', 'HR负责人', '用人经理', '法务审计', '系统管理员'] },
      { path: '/board', label: '效果看板', icon: '◪', roles: ['招聘HR', 'HR负责人', '用人经理', '法务审计', '系统管理员'] },
      { path: '/settings', label: '阈值与参数', icon: '⚙', roles: ['HR负责人', '法务审计', '系统管理员'] },
      { path: '/accounts', label: '账号管理', icon: '◉', roles: ['HR负责人', '系统管理员'] },
    ],
  },
  {
    group: '工具',
    items: [
      { path: '/assistant', label: '智能体助手', icon: '✦', roles: ['招聘HR', 'HR负责人', '用人经理', '业务面试官', '带教人', '系统管理员'] },
    ],
  },
]

const visibleNav = computed(() => {
  const role = user.value?.role || ''
  // 超管账号可访问全部模块 —— 它是平台所有者，需要能检查任何一处
  if (user.value?.is_super) return NAV
  const visible = NAV.map((g) => ({
    ...g, items: g.items.filter((i) => i.roles.includes(role)),
  })).filter((g) => g.items.length)
  // 新人只看得到「我的培训」，其余分组没必要出现
  if (role === '新人') return visible.filter((g) => g.group === '招聘流程')
  return visible
})

function isActive(p: string) {
  if (p === '/workbench') return route.path.startsWith('/candidate') || route.path === '/workbench'
  if (p === '/interviews') return route.path.startsWith('/interview')
  if (p === '/training') return route.path.startsWith('/training')
  if (p === '/models') return route.path.startsWith('/model')
  return route.path === p
}

function badge(_it: any) { return '' }
function go(p: string) { router.push(p) }

function applyTheme(t: string) {
  theme.value = t
  document.documentElement.setAttribute('data-theme', t)
  localStorage.setItem('theme', t)
}
function toggleTheme() { applyTheme(theme.value === 'dark' ? 'light' : 'dark') }

async function loadStatus() {
  if (!auth.token) return
  try { status.value = await api.systemStatus() } catch { /* 拦截器会处理 401 */ }
}

function doLogout() {
  menuOpen.value = false
  auth.clear()
  currentUser.value = null
  toast.info('已退出登录')
  router.replace('/login')
}

async function doChangePwd() {
  pwdErr.value = ''
  if (pwd.value.new1.length < 6) { pwdErr.value = '新密码至少 6 位'; return }
  if (pwd.value.new1 !== pwd.value.new2) { pwdErr.value = '两次输入的新密码不一致'; return }
  pwdBusy.value = true
  try {
    await auth.changePassword(pwd.value.old, pwd.value.new1)
    pwdOpen.value = false
    pwd.value = { old: '', new1: '', new2: '' }
    toast.ok('密码已修改', '请用新密码重新登录')
    setTimeout(doLogout, 1200)
  } catch (e: any) {
    pwdErr.value = e?.friendly || '修改失败'
  } finally {
    pwdBusy.value = false
  }
}

// 点击外部关闭用户菜单
function onDocClick(e: MouseEvent) {
  if (menuOpen.value && !(e.target as HTMLElement)?.closest('.sidebar-foot')) menuOpen.value = false
}

watch(() => route.path, () => {
  if (!isLogin.value) loadStatus()
})

onMounted(async () => {
  applyTheme(theme.value)
  document.addEventListener('click', onDocClick)
  if (auth.token) {
    try {
      currentUser.value = await auth.me()
    } catch {
      // 令牌失效：拦截器已跳到登录页
    }
  }
})

onUnmounted(() => document.removeEventListener('click', onDocClick))
</script>

<style scoped>
.sidebar-foot {
  padding: 12px; border-top: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 8px; position: relative;
}
.user-box {
  display: flex; gap: 9px; align-items: center; width: 100%;
  padding: 7px 8px; border-radius: var(--radius);
  background: none; border: none; cursor: pointer;
  font-family: inherit; transition: background 0.12s;
}
.user-box:hover { background: var(--bg-hover); }
.user-avatar {
  width: 28px; height: 28px; border-radius: 7px; flex-shrink: 0;
  background: var(--brand); color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 620;
}
.user-name { font-size: 13px; font-weight: 600; color: var(--text-0); }
.user-role { font-size: 11px; color: var(--text-2); margin-top: 1px; }
.user-caret { color: var(--text-2); font-size: 14px; }

.user-menu {
  position: absolute; bottom: 84px; left: 12px; right: 12px;
  background: var(--bg-2); border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg); box-shadow: var(--shadow-lg);
  overflow: hidden; z-index: 50;
}
.um-head { padding: 11px 13px; border-bottom: 1px solid var(--border); }
.um-item {
  display: block; width: 100%; text-align: left;
  padding: 9px 13px; background: none; border: none;
  color: var(--text-1); font-size: 13px; font-family: inherit;
  cursor: pointer; transition: all 0.12s;
}
.um-item:hover { background: var(--bg-hover); color: var(--text-0); }
.um-item.danger { color: var(--danger); border-top: 1px solid var(--border); }
.um-item.danger:hover { background: var(--danger-bg); }
.theme-btn { width: 100%; }
</style>
