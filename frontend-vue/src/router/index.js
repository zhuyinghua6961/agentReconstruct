import { createRouter, createWebHistory } from 'vue-router'
import Login from '../views/Login.vue'
import AdminDashboard from '../views/AdminDashboard.vue'
import Home from '../views/Home.vue'
import UserProfile from '../views/UserProfile.vue'
import ForgotPassword from '../views/ForgotPassword.vue'
import QuotaManagement from '../views/QuotaManagement.vue'
import { authApi } from '../services/auth'

const routes = [
  { path: '/', component: Home, meta: { requiresAuth: true } },
  { path: '/login', component: Login },
  { path: '/forgot-password', component: ForgotPassword },
  { path: '/admin', component: AdminDashboard, meta: { requiresAuth: true, requiresAdmin: true } },
  { path: '/profile', component: UserProfile, meta: { requiresAuth: true } },
  { path: '/quota-management', component: QuotaManagement, meta: { requiresAuth: true, requiresAdmin: true } }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// Token 验证缓存（避免每次路由都验证）
let tokenValidated = false
let lastValidationTime = 0
const VALIDATION_CACHE_TIME = 5 * 60 * 1000 // 5分钟缓存

function readStoredToken() {
  return localStorage.getItem('token')
    || localStorage.getItem('agentcode.auth.token.v1')
    || ''
}

function readStoredUser() {
  const raw = localStorage.getItem('user') || localStorage.getItem('agentcode.auth.user.v1')
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

function clearStoredAuth() {
  localStorage.removeItem('token')
  localStorage.removeItem('user')
  localStorage.removeItem('agentcode.auth.token.v1')
  localStorage.removeItem('agentcode.auth.user.v1')
}

function persistStoredUser(user) {
  const serialized = JSON.stringify(user)
  localStorage.setItem('user', serialized)
  localStorage.setItem('agentcode.auth.user.v1', serialized)
}

router.beforeEach(async (to, from, next) => {
  const token = readStoredToken()
  const user = readStoredUser()
  
  // 如果需要认证但没有 token
  if (to.meta.requiresAuth && !token) {
    next('/login')
    return
  }
  
  // 如果有 token 且需要认证，验证 token 是否有效
  if (to.meta.requiresAuth && token) {
    const now = Date.now()
    const shouldValidate = !tokenValidated || (now - lastValidationTime > VALIDATION_CACHE_TIME)
    
    if (shouldValidate) {
      try {
        const result = await authApi.getMe()
        if (!result.success) {
          // Token 无效，清除登录状态
          clearStoredAuth()
          tokenValidated = false
          next('/login')
          return
        }
        
        // 检查是否首次登录，需要强制修改密码
        if (result.data && result.data.is_first_login) {
          // 如果不是去个人中心页面，强制跳转到修改密码
          if (to.path !== '/profile') {
            next('/profile?change_password=required')
            return
          }
        }
        // 检查是否需要强制设置安全问题
        if (result.data && result.data.require_security_questions_setup) {
          if (to.path !== '/profile') {
            next('/profile?security_questions=required')
            return
          }
        }

        // 同步最新用户标记到本地缓存，供缓存分支读取
        if (user) {
          const mergedUser = {
            ...user,
            is_first_login: Boolean(result.data?.is_first_login),
            require_security_questions_setup: Boolean(result.data?.require_security_questions_setup),
            has_security_questions: Boolean(result.data?.has_security_questions),
          }
          persistStoredUser(mergedUser)
        }
        
        // Token 有效，更新缓存
        tokenValidated = true
        lastValidationTime = now
      } catch (e) {
        // 验证失败，清除登录状态
        console.error('Token 验证失败:', e)
        clearStoredAuth()
        tokenValidated = false
        next('/login')
        return
      }
    } else {
      // 使用缓存时也要检查首次登录状态
      if (user && user.is_first_login && to.path !== '/profile') {
        next('/profile?change_password=required')
        return
      }
      if (user && user.require_security_questions_setup && to.path !== '/profile') {
        next('/profile?security_questions=required')
        return
      }
    }
  }
  
  // 检查管理员权限
  if (to.meta.requiresAdmin && user?.role !== 'admin') {
    next('/')
    return
  }
  
  // 管理员访问根路径时，自动跳转到管理后台
  if (to.path === '/' && token && user?.role === 'admin') {
    next('/admin')
    return
  }
  
  // 已登录用户访问登录页，跳转到首页
  if (to.path === '/login' && token) {
    next(user?.role === 'admin' ? '/admin' : '/')
    return
  }
  
  next()
})

export default router
