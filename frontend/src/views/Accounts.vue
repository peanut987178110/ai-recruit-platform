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
              <th style="width: 250px">操作</th>
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
                <VButton size="sm" variant="ghost" :disabled="u.is_super && !me?.is_super"
                         @click="openEdit(u)">编辑</VButton>
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

    <!-- 业务线：数据隔离的边界，必须是受控字典 -->
    <div v-if="canManage" class="card mt-3">
      <div class="card-head">
        <span class="card-title">业务线</span>
        <span class="card-desc">
          用人经理按业务线查看候选人、知识库按业务线检索 —— 账号、岗位、知识库的业务线都从这里选
        </span>
        <VButton size="sm" variant="primary" icon="+" class="ml-auto" @click="openLine(null)">新增业务线</VButton>
      </div>
      <table class="table">
        <thead>
          <tr>
            <th>名称</th>
            <th>说明</th>
            <th style="width: 240px">正在使用</th>
            <th style="width: 80px">状态</th>
            <th style="width: 200px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in lineRows" :key="b.id" :class="{ dim: !b.active }">
            <td class="bold small">{{ b.name }}</td>
            <td class="tiny muted">{{ b.description || '—' }}</td>
            <td class="tiny">
              <template v-if="b.in_use">
                <span v-for="(n, k) in b.usage" v-show="n" :key="k" class="use">{{ k }} {{ n }}</span>
              </template>
              <span v-else class="muted">未被使用</span>
            </td>
            <td>
              <span class="tag" :class="b.active ? 'tag-ok' : 'tag-gray'">{{ b.active ? '启用' : '停用' }}</span>
            </td>
            <td>
              <VButton size="sm" variant="ghost" @click="openLine(b)">编辑</VButton>
              <VButton size="sm" variant="ghost" @click="toggleLine(b)">{{ b.active ? '停用' : '启用' }}</VButton>
              <VButton size="sm" variant="ghost" @click="openDelete(b)">删除</VButton>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="tiny muted mt-2">
        改名会同步到所有使用它的账号、岗位与知识库；停用后不能再被新选择，但已有数据不受影响。
        删除仍在使用的业务线时，需要先指定把这些数据迁移到哪条业务线。
      </div>
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
          <option v-for="b in lines" :key="b.name" :value="b.name">{{ b.name }}</option>
        </select>
        <div class="field-hint">
          决定该账号能看到哪条业务线的数据。
          <template v-if="canManage">没有需要的业务线？先在下方「业务线」中新增。</template>
          <template v-else>没有需要的业务线请联系 HR 负责人新增。</template>
        </div>
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

    <!-- 编辑账号 -->
    <VModal v-if="editingUser" :title="`编辑账号「${editingUser.userid}」`" @close="editingUser = null">
      <div class="field">
        <label class="field-label">姓名<span class="req">*</span></label>
        <input v-model.trim="edit.name" class="input" />
      </div>
      <div class="field">
        <label class="field-label">角色</label>
        <select v-model="edit.role" class="select" :disabled="editingUser.is_super">
          <option v-for="r in editRoles" :key="r" :value="r">{{ r }}</option>
        </select>
      </div>
      <div class="field">
        <label class="field-label">业务线</label>
        <select v-model="edit.business_line" class="select">
          <option value="">不限</option>
          <option v-for="b in lines" :key="b.name" :value="b.name">{{ b.name }}</option>
          <!-- 原值若已停用，仍要显示出来，否则下拉框会显示成「不限」误导人 -->
          <option v-if="edit.business_line && !lines.some((l) => l.name === edit.business_line)"
                  :value="edit.business_line">{{ edit.business_line }}（已停用）</option>
        </select>
      </div>
      <div class="field">
        <label class="field-label">部门</label>
        <input v-model.trim="edit.department" class="input" />
      </div>
      <div v-if="error" class="alert alert-danger"><span class="alert-icon">✕</span><div>{{ error }}</div></div>
      <template #foot>
        <VButton @click="editingUser = null">取消</VButton>
        <VButton variant="primary" :loading="busy" @click="doEdit">保存</VButton>
      </template>
    </VModal>

    <!-- 新增 / 编辑业务线 -->
    <VModal v-if="lineOpen" :title="lineForm.id ? `编辑业务线「${lineForm.origName}」` : '新增业务线'"
            @close="lineOpen = false">
      <div class="field">
        <label class="field-label">名称<span class="req">*</span></label>
        <input v-model.trim="lineForm.name" class="input" maxlength="32" placeholder="例如：本地生活业务线" />
        <div v-if="lineForm.id && lineForm.name !== lineForm.origName" class="field-hint warn-text">
          改名会同步到所有使用「{{ lineForm.origName }}」的账号、岗位与知识库。
        </div>
      </div>
      <div class="field">
        <label class="field-label">说明</label>
        <input v-model.trim="lineForm.description" class="input" maxlength="200" />
      </div>
      <div class="field">
        <label class="field-label">排序</label>
        <input v-model.number="lineForm.sort" type="number" class="input" style="max-width: 120px" />
        <div class="field-hint">数字越小越靠前</div>
      </div>
      <div v-if="error" class="alert alert-danger"><span class="alert-icon">✕</span><div>{{ error }}</div></div>
      <template #foot>
        <VButton @click="lineOpen = false">取消</VButton>
        <VButton variant="primary" :loading="busy" :disabled="!lineForm.name" @click="saveLine">保存</VButton>
      </template>
    </VModal>

    <!-- 删除业务线 -->
    <VModal v-if="deleting" :title="`删除业务线「${deleting.name}」`" @close="deleting = null">
      <template v-if="deleting.in_use">
        <div class="alert alert-warn">
          <span class="alert-icon">⚠</span>
          <div>
            该业务线仍被
            <b v-for="(n, k) in deleting.usage" v-show="n" :key="k"> {{ k }} {{ n }} 个 </b>
            使用。直接删除会让这些数据属于一个不存在的业务线，用人经理将看不到相应候选人。
          </div>
        </div>
        <div class="field">
          <label class="field-label">把这些数据迁移到<span class="req">*</span></label>
          <select v-model="migrateTo" class="select">
            <option value="" disabled>请选择</option>
            <option v-for="b in migrateTargets" :key="b.name" :value="b.name">{{ b.name }}</option>
          </select>
        </div>
        <div class="tiny muted">不想迁移？可以改用「停用」：已有数据保持不变，只是不能再被新选择。</div>
      </template>
      <div v-else class="small">该业务线没有被任何账号、岗位或知识库使用，可以直接删除。</div>
      <div v-if="error" class="alert alert-danger mt-2"><span class="alert-icon">✕</span><div>{{ error }}</div></div>
      <template #foot>
        <VButton @click="deleting = null">取消</VButton>
        <VButton variant="danger" :loading="busy" :disabled="deleting.in_use > 0 && !migrateTo" @click="doDelete">
          {{ deleting.in_use ? '迁移并删除' : '删除' }}
        </VButton>
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
import { currentUser, lineApi } from '../api'
import { fmtTime } from '../api/labels'
import { toast } from '../components/toast'

// 业务线来自字典接口，不再写死在前端 —— 写死的列表与后端一旦不一致，
// 新增的业务线在这里选不到，删掉的却还能选到。
const lines = ref<any[]>([])
const lineRows = ref<any[]>([])

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
const canManage = computed(() => !!me.value && (me.value.is_super || me.value.role === 'HR负责人'))

const editingUser = ref<any>(null)
const edit = ref({ name: '', role: '', business_line: '', department: '' })
const lineOpen = ref(false)
const lineForm = ref({ id: 0, name: '', origName: '', description: '', sort: 0 })
const deleting = ref<any>(null)
const migrateTo = ref('')

const editRoles = computed(() => {
  const base = assignable.value || []
  // 被编辑账号的现有角色即便不在可授予范围内，也要能显示
  return editingUser.value && !base.includes(editingUser.value.role)
    ? [editingUser.value.role, ...base] : base
})
const migrateTargets = computed(() =>
  lines.value.filter((l) => !deleting.value || l.name !== deleting.value.name))

async function loadLines() {
  try { lines.value = await lineApi.list() } catch { lines.value = [] }
  if (canManage.value) {
    try { lineRows.value = await lineApi.manage() } catch { lineRows.value = [] }
  }
}

function openEdit(u: any) {
  error.value = ''
  editingUser.value = u
  edit.value = { name: u.name, role: u.role, business_line: u.business_line || '', department: u.department || '' }
}

async function doEdit() {
  error.value = ''
  busy.value = true
  try {
    const r = await auth.updateAccount(editingUser.value.userid, edit.value)
    toast.ok(`已保存 ${editingUser.value.userid}`, (r.changes || []).join('；') || '无变更')
    editingUser.value = null
    await Promise.all([load(), loadLines()])
  } catch (e: any) {
    error.value = e?.friendly || '保存失败'
  } finally {
    busy.value = false
  }
}

function openLine(b: any) {
  error.value = ''
  lineForm.value = b
    ? { id: b.id, name: b.name, origName: b.name, description: b.description, sort: b.sort }
    : { id: 0, name: '', origName: '', description: '', sort: lineRows.value.length + 1 }
  lineOpen.value = true
}

async function saveLine() {
  error.value = ''
  busy.value = true
  try {
    const { id, name, description, sort } = lineForm.value
    if (id) {
      const r = await lineApi.update(id, { name, description, sort })
      toast.ok('已保存业务线', (r.changes || []).join('；'))
    } else {
      await lineApi.create({ name, description, sort })
      toast.ok(`已新增业务线「${name}」`)
    }
    lineOpen.value = false
    await Promise.all([load(), loadLines()])
  } catch (e: any) {
    error.value = e?.friendly || '保存失败'
  } finally {
    busy.value = false
  }
}

async function toggleLine(b: any) {
  try {
    await lineApi.update(b.id, { active: !b.active })
    toast.ok(`已${b.active ? '停用' : '启用'}「${b.name}」`)
    await loadLines()
  } catch (e) {
    toast.err(e)
  }
}

function openDelete(b: any) {
  error.value = ''
  migrateTo.value = ''
  deleting.value = b
}

async function doDelete() {
  error.value = ''
  busy.value = true
  try {
    const target = migrateTo.value
    const r = await lineApi.remove(deleting.value.id, target)
    toast.ok(`已删除「${deleting.value.name}」`, r.moved ? `${r.moved} 条数据迁移至「${target}」` : '')
    deleting.value = null
    await Promise.all([load(), loadLines()])
  } catch (e: any) {
    error.value = e?.friendly || '删除失败'
  } finally {
    busy.value = false
  }
}

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
  await Promise.all([load(), loadLines()])
})
</script>

<style scoped>
.use {
  display: inline-block; margin: 0 6px 2px 0; padding: 1px 7px; border-radius: 10px;
  background: var(--bg-hover); color: var(--text-1);
}
tr.dim td { opacity: 0.55; }
.role-card {
  padding: 12px 13px; border: 1px solid var(--border);
  border-radius: var(--radius); background: var(--bg-0);
}
</style>
