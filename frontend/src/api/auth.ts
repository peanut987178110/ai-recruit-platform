import http from './index'

export interface Account {
  id: number
  userid: string
  name: string
  role: string
  business_line: string
  department: string
  email: string
  active: boolean
  can_view_resume: boolean
  mask_contact: boolean
  is_super: boolean
  created_by: string
  last_login_at: string
}

const TOKEN_KEY = 'auth_token'
const USER_KEY = 'auth_user'

export const auth = {
  get token() { return localStorage.getItem(TOKEN_KEY) || '' },

  get user(): Account | null {
    try {
      const raw = localStorage.getItem(USER_KEY)
      return raw ? JSON.parse(raw) : null
    } catch { return null }
  },

  save(token: string, user: Account) {
    localStorage.setItem(TOKEN_KEY, token)
    localStorage.setItem(USER_KEY, JSON.stringify(user))
  },

  clear() {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  },

  async login(userid: string, password: string) {
    const d = await http.post('/auth/login', { userid, password }).then((r) => r.data)
    auth.save(d.token, d.user)
    return d.user as Account
  },

  async register(p: {
    userid: string; name: string; password: string; role: string
    business_line?: string; department?: string; email?: string
  }) {
    const d = await http.post('/auth/register', p).then((r) => r.data)
    auth.save(d.token, d.user)
    return d.user as Account
  },

  me: () => http.get('/auth/me').then((r) => r.data as Account),
  roles: () => http.get('/auth/roles').then((r) => r.data),
  accounts: () => http.get('/auth/users').then((r) => r.data),
  createAccount: (p: Record<string, unknown>) => http.post('/auth/users', p).then((r) => r.data),
  toggleAccount: (uid: string) => http.post(`/auth/users/${uid}/toggle`).then((r) => r.data),
  resetPassword: (uid: string, new_password: string) =>
    http.post(`/auth/users/${uid}/reset-password`, { new_password }).then((r) => r.data),
  deleteAccount: (uid: string) => http.delete(`/auth/users/${uid}`).then((r) => r.data),
  changePassword: (old_password: string, new_password: string) =>
    http.post('/auth/change-password', { old_password, new_password }).then((r) => r.data),
}
