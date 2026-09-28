<template>
  <div>
    <div class="card">
      <div class="card-head">
        <span class="card-title">账号管理</span>
        <span class="card-desc">初始账号 admin 拥有全部权限，其它账号由它创建</span>
        <div class="ml-auto row">
          <VButton size="sm" icon="⟳" :loading="loading" @click="load">刷新</VButton>
          <VButton size="sm" variant="primary" icon="+" @click="createOpen = true">新建账号</VButton>
        </div>
      </div>

      <div class="grid grid-4 mb-3">
        <div class="stat">
          <div class="stat-label">账号总数</div>
          <div class="stat-value sm">{{ rows.length }}</div>
        </div>
        <div class="stat">
          <div class="stat-label">启用中</div>
          <div class="stat-value sm" style="color: var(--ok)">{{ rows.filter(r => r.active).length }}</div>
        </div>
        <div class="stat">
          <div class="stat-label">已停用</div>
          <div class="stat-value sm" :style="{ color: rows.filter(r=>!r.active).length ? 'var(--warn)' : '' }">
            {{ rows.filter(r => !r.active).length }}
          </div>
        </div>
        <div class="stat">
          <div class="stat-label">可自助注册的角色</div>
          <div class="stat-value sm">{{ signupCount }}</div>
          <div class="stat-hint">管理类角色只能由超管创建</div>
        </div>
      </div>

      <VState :items="rows" :loading="loading" title="暂无账号" icon="◉">
        <table class="table">
          <thead>
            <tr>
              <th>账号</th>
              <th style="width: 120px">姓名</th>
              <th style="width: 110px">角色</th>
              <th style="width: 130px">业务线</th>
              <th style="width: 130px">部门</th>
              <th style="width: 90px">状态</th>
              <th style="width: 140px">最后登录</th>
              <th style="width: 200px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in rows" :key="u.id">
              <td>
                <span class="mono bold">{{ u.userid }}</span>
                <span v-if="u.is_super" class="tag tag-purple tiny" style="margin-left: 6px">超管</span>
              </td>
              <td class="small">{{ u.name }}</td>
              <td><span class="tag tag-blue">{{ u.role }}</span></td>
              <td class="tiny">{{ u.business_line || '—' }}</td>
              <td class="tiny">{{ u.department || '—' }}</td>
              <td>
                <span class="tag" :class="u.active ? 'tag-ok' : 'tag-gray'">
                  {{ u.active ? '启用' : '停用' }}
                </span>
              </td>
              <td class="tiny muted">{{ u.last_login_at ? fmtTime(u.last_login_at) : '从未登录' }}</td>
              <td>
                <VButton size="sm" variant="ghost" @click="openReset(u)">重置密码</VButton>
                <VButton
                  size="sm" variant="ghost"
                  :disabled="u.is_super || u.userid === me?.userid"
                  @click="toggle(u)"
                >{{ u.active ? '停用' : '启用' }}</VButton>
                <VButton
                  size="sm" variant="ghost"
                  :disabled="!me?.is_super || u.is_super || u.userid === me?.userid"
                  @click="remove(u)"
                >删除</VButton>
              </td>
            </tr>
          </tbody>
        </table>
      </VState>
    </div>

    <!-- 角色说明 -->
    <div class="card mt-3">
      <div class="card-head">
        <span class="card-title">角色与数据范围</span>
        <span class="card-desc">数据可见范围在查询层强制隔离，不依赖前端隐藏</span>
      </div>
      <div class="grid grid-3">
        <div v-for="r in roles" :key="r.role" class="role-card">
          <div class="row-between">
            <span class="bold small">{{ r.role }}</span>
            <span class="tag tiny" :class="r.self_signup ? 'tag-ok' : 'tag-gray'">
              {{ r.self_signup ? '可自助注册' : '仅超管创建' }}
            </span>
          </div>
          <div class="tiny muted mt-1">{{ r.description }}</div>
        </div>
      </div>
    </div>

    <!-- 新建账号 -->
    <VModal v-if="createOpen" title="新建账号" @close="createOpen = false">
      <div class="field">
        <label class="field-label">账号<span class="req">*</span></label>
        <input v-model.trim="form.userid" class="input" placeholder="字母、数字、下划线" />
      </div>
      <div class="field">
        <label class="field-label">姓名<span class="req">*</span></label>
        <input v-model.trim="form.name" class="input" />
      </div>
      <div class="field">
        <label class="field-label">初始密码<span class="req">*</span></label>
        <input v-model="form.password" class="input" placeholder="至少 6 位" />
      </div>
      <div class="field">
        <label class="field-label">角色<span class="req">*</span></label>
        <select v-model="form.role" class="select">
          <option v-for="r in assignable" :key="r" :value="r">{{ r }}</option>
        </select>
        <div v-if="!me?.is_super" class="field-hint">
          你是 HR 负责人，只能创建非管理类角色。管理类角色请用 admin 账号创建。
        </div>
      </div>
      <div class="field">
        <label class="field-label">业务线</label>
        <select v-model="form.business_line" class="select">
          <option value="">不限</option>
          <option v-for="b in LINES" :key="b">{{ b }}</option>
        </select>
      </div>
      <div class="field">
        <label class="field-label">部门</label>
        <input v-model.trim="form.department" class="input" />
      </div>
      <div v-if="error" class="alert alert-danger"><span class="alert-icon">✕</span><div>{{ error }}</div></div>
      <template #foot>
        <VButton @click="createOpen = false">取消</VButton>
        <VButton variant="primary" :loading="busy" @click="doCreate">创建</VButton>
      </template>
    </VModal>

    <!-- 重置密码 -->
    <VModal v-if="resetting" :title="`重置「${resetting.userid}」的密码`" @close="resetting = null">
      <div class="field">
        <label class="field-label">新密码<span class="req">*</span></label>
        <input v-model="newPwd" class="input" placeholder="至少 6 位" />
      </div>
      <div class="alert alert-warn">
        <span class="alert-icon">⚠</span>
        <div>重置后对方需用新密码重新登录，已签发的令牌在 12 小时内仍然有效。</div>
      </div>
      <template #foot>
        <VButton @click="resetting = null">取消</VButton>
        <VButton variant="primary" :loading="busy" @click="doReset">确认重置</VButton>
      </template>
    </VModal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import VButton from '../components/VButton.vue'
import VModal from '../components/VModal.vue'
import VState from '../components/VState.vue'
import { auth } from '../api/auth'
import { currentUser } from '../api'
import { fmtTime } from '../api/labels'
import { toast } from '../components/toast'

const LINES = ['电商业务线', '供应链业务线', '职能线', '通用']

const rows = ref<any[]>([])
const roles = ref<any[]>([])
const assignable = ref<string[]>([])
const loading = ref(false)
const busy = ref(false)
const createOpen = ref(false)
const resetting = ref<any>(null)
const newPwd = ref('')
const error = ref('')

const me = computed(() => currentUser.value)
const signupCount = computed(() => roles.value.filter((r) => r.self_signup).length)

const form = ref({
  userid: '', name: '', password: '', role: '招聘HR', business_line: '', department: '',
})

async function load() {
  loading.value = true
  try {
    const d = await auth.accounts()
    rows.value = d.users || []
    assignable.value = d.assignable_roles || []
    if (!assignable.value.includes(form.value.role)) {
      form.value.role = assignable.value[0] || '招聘HR'
    }
  } catch (e) {
    toast.err(e, '加载账号失败')
  } finally {
    loading.value = false
  }
}

async function doCreate() {
  error.value = ''
  if (!form.value.userid || !form.value.name) { error.value = '请填写账号和姓名'; return }
  if (form.value.password.length < 6) { error.value = '密码至少 6 位'; return }
  busy.value = true
  try {
    await auth.createAccount(form.value)
    toast.ok(`已创建账号 ${form.value.userid}`, `角色：${form.value.role}`)
    createOpen.value = false
    form.value = { userid: '', name: '', password: '', role: assignable.value[0] || '招聘HR', business_line: '', department: '' }
    await load()
  } catch (e: any) {
    error.value = e?.friendly || '创建失败'
  } finally {
    busy.value = false
  }
}

function openReset(u: any) { resetting.value = u; newPwd.value = '' }

async function doReset() {
  if (newPwd.value.length < 6) { toast.warn('密码至少 6 位'); return }
  busy.value = true
  try {
    const r = await auth.resetPassword(resetting.value.userid, newPwd.value)
    toast.ok(r.message)
    resetting.value = null
  } catch (e) {
    toast.err(e)
  } finally {
    busy.value = false
  }
}

async function toggle(u: any) {
  try {
    const r = await auth.toggleAccount(u.userid)
    toast.ok(`${u.userid} 已${r.active ? '启用' : '停用'}`)
    await load()
  } catch (e) {
    toast.err(e)
  }
}

async function remove(u: any) {
  if (!confirm(`确定删除账号「${u.userid}」？此操作不可撤销。\n\n注意：该账号创建的数据（候选人、面试记录等）会保留。`)) return
  try {
    await auth.deleteAccount(u.userid)
    toast.ok(`已删除 ${u.userid}`)
    await load()
  } catch (e) {
    toast.err(e)
  }
}

onMounted(async () => {
  try { roles.value = await auth.roles() } catch { /* ignore */ }
  await load()
})
</script>

<style scoped>
.role-card {
  padding: 12px 13px; border: 1px solid var(--border);
  border-radius: var(--radius); background: var(--bg-0);
}
</style>
