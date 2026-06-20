import { createRouter, createWebHistory } from 'vue-router'
import Login from '../views/Login.vue'
import Register from '../views/Register.vue'
import AdminDashboard from '../views/AdminDashboard.vue'
import Home from '../views/Home.vue'
import LiteratureSearch from '../views/LiteratureSearch.vue'
import UserProfile from '../views/UserProfile.vue'
import ForgotPassword from '../views/ForgotPassword.vue'
import { buildRequiredProfilePath, hasRequiredProfileSetup, mergeValidatedUser } from './profileSetup'
import {
  authApi,
  clearStoredAuth,
  persistStoredUser,
  readStoredToken,
  readStoredUser,
} from '../services/auth'

const routes = [
  { path: '/', component: Home, meta: { requiresAuth: true } },
  { path: '/literature-search', component: LiteratureSearch, meta: { requiresAuth: true } },
  { path: '/login', component: Login },
  { path: '/register', component: Register },
  { path: '/forgot-password', component: ForgotPassword },
  { path: '/admin', component: AdminDashboard, meta: { requiresAuth: true, requiresAdmin: true } },
  { path: '/profile', component: UserProfile, meta: { requiresAuth: true } },
  {
    path: '/quota-management',
    redirect: { path: '/admin', query: { tab: 'quota' } },
    meta: { requiresAuth: true, requiresAdmin: true }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// Token 验证缓存（避免每次路由都验证）
let tokenValidated = false
let lastValidationTime = 0
const VALIDATION_CACHE_TIME = 5 * 60 * 1000 // 5分钟缓存

router.beforeEach(async (to, from, next) => {
  const token = readStoredToken()
  const user = readStoredUser()
  let currentUser = user
  const isGuestOnlyRoute = to.path === '/login' || to.path === '/register'
  
  // 如果需要认证但没有 token
  if (to.meta.requiresAuth && !token) {
    next('/login')
    return
  }
  
  // 如果有 token 且需要认证或访问游客页，验证 token 是否有效
  if ((to.meta.requiresAuth || to.path === '/login' || to.path === '/register') && token) {
    const now = Date.now()
    const shouldValidate = !tokenValidated || (now - lastValidationTime > VALIDATION_CACHE_TIME)
    
    if (shouldValidate) {
      try {
        const result = await authApi.getMe()
        if (!result.success) {
          // Token 无效，清除登录状态
          clearStoredAuth()
          tokenValidated = false
          next(to.path === '/register' ? '/register' : '/login')
          return
        }

        currentUser = mergeValidatedUser(user, result.data)
        if (currentUser) {
          persistStoredUser(currentUser)
        }

        if (currentUser && hasRequiredProfileSetup(currentUser) && to.path !== '/profile') {
          next(buildRequiredProfilePath(currentUser))
          return
        }
        
        // Token 有效，更新缓存
        tokenValidated = true
        lastValidationTime = now
      } catch (e) {
        // 验证失败，清除登录状态
        console.error('Token 验证失败:', e)
        clearStoredAuth()
        tokenValidated = false
        next(to.path === '/register' ? '/register' : '/login')
        return
      }
    } else {
      if (currentUser && hasRequiredProfileSetup(currentUser) && to.path !== '/profile') {
        next(buildRequiredProfilePath(currentUser))
        return
      }
    }
  }
  
  // 检查管理员权限
  if (to.meta.requiresAdmin && currentUser?.role !== 'admin') {
    next('/')
    return
  }
  
  // 管理员访问根路径时，自动跳转到管理后台
  if (to.path === '/' && token && currentUser?.role === 'admin') {
    next('/admin')
    return
  }
  
  // 已登录用户访问游客页，跳转到对应首页
  if (isGuestOnlyRoute && token) {
    if (currentUser && hasRequiredProfileSetup(currentUser)) {
      next(buildRequiredProfilePath(currentUser))
      return
    }
    next(currentUser?.role === 'admin' ? '/admin' : '/')
    return
  }
  
  next()
})

export default router
