import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import { createPinia } from 'pinia'

import App from './App.vue'
import './styles/main.css'
import { auth } from './api/auth'
import { currentUser } from './api'

import Login from './views/Login.vue'
import Workbench from './views/Workbench.vue'
import Review from './views/Review.vue'
import Pool from './views/Pool.vue'
import ModelList from './views/ModelList.vue'
import ModelEditor from './views/ModelEditor.vue'
import InterviewList from './views/InterviewList.vue'
import InterviewPrepare from './views/InterviewPrepare.vue'
import InterviewReport from './views/InterviewReport.vue'
import TrainingList from './views/TrainingList.vue'
import Training from './views/Training.vue'
import Exam from './views/Exam.vue'
import TrainingPlan from './views/TrainingPlan.vue'
import Board from './views/Board.vue'
import Settings from './views/Settings.vue'
import Assistant from './views/Assistant.vue'
import Accounts from './views/Accounts.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', component: Login, meta: { public: true } },
    { path: '/', redirect: '/workbench' },
    { path: '/workbench', component: Workbench, meta: { title: '候选人工作台', sub: 'P-01' } },
    { path: '/candidate/:id', component: Review, meta: { title: '简历复核详情', sub: 'P-02' } },
    { path: '/pool', component: Pool, meta: { title: '待定池', sub: 'P-03' } },
    { path: '/models', component: ModelList, meta: { title: '岗位能力模型', sub: 'P-05' } },
    { path: '/model/:id', component: ModelEditor, meta: { title: '能力模型编辑器', sub: 'P-06' } },
    { path: '/interviews', component: InterviewList, meta: { title: '面试日程', sub: 'P-07' } },
    { path: '/interview/:id', component: InterviewPrepare, meta: { title: '面试准备', sub: 'P-07' } },
    { path: '/interview/:id/report', component: InterviewReport, meta: { title: '面试评估报告', sub: 'P-08' } },
    { path: '/training', component: Training, meta: { title: '培训考核', sub: 'P-09' } },
    { path: '/training/plans', component: TrainingList, meta: { title: '培训方案', sub: 'P-09' } },
    { path: '/exam/:id', component: Exam, meta: { title: '在线考试', sub: 'P-14', fullscreen: true } },
    { path: '/training/:id', component: TrainingPlan, meta: { title: '培训方案', sub: 'P-10' } },
    { path: '/board', component: Board, meta: { title: '效果看板', sub: 'P-11' } },
    { path: '/settings', component: Settings, meta: { title: '阈值与参数配置', sub: 'P-12' } },
    { path: '/accounts', component: Accounts, meta: { title: '账号管理', sub: 'P-13' } },
    { path: '/assistant', component: Assistant, meta: { title: '智能体助手', sub: 'Agent' } },
  ],
})

// 未登录一律拦到登录页。前端拦截只是体验优化，
// 真正的边界在后端 —— 每个接口都会校验令牌。
//
// 这里有两次 await：进受保护页前先把当前用户加载出来，
// 否则侧边栏会在登录后的一瞬间按「无角色」渲染，导航项全部缺失。
router.beforeEach(async (to) => {
  const loggedIn = !!auth.token
  if (!to.meta.public && !loggedIn) return { path: '/login' }
  if (to.path === '/login' && loggedIn) return { path: '/workbench' }
  if (loggedIn && !currentUser.value) {
    try {
      currentUser.value = await auth.me()
    } catch {
      // 令牌失效：清理并回登录页
      auth.clear()
      currentUser.value = null
      if (!to.meta.public) return { path: '/login' }
    }
  }
  return true
})

createApp(App).use(router).use(createPinia()).mount('#app')
