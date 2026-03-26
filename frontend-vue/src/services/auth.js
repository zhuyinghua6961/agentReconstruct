/**
 * 认证API服务
 */
const API_BASE = '/api/auth'

function readStoredToken() {
  return localStorage.getItem('token')
    || localStorage.getItem('agentcode.auth.token.v1')
    || ''
}

function clearStoredAuth() {
  localStorage.removeItem('token')
  localStorage.removeItem('user')
  localStorage.removeItem('agentcode.auth.token.v1')
  localStorage.removeItem('agentcode.auth.user.v1')
}

// 全局错误处理函数
function handleAuthError(error, response) {
  // 检查是否是账号停用错误
  if (error.code === 'ACCOUNT_DISABLED') {
    // 清除本地存储
    clearStoredAuth()
    
    // 显示友好提示
    alert('您的账号已被停用，请联系管理员')
    
    // 跳转到登录页
    window.location.href = '/login'
    
    return
  }
  
  // 检查是否是 token 失效
  if (error.code === 'TOKEN_INVALID' || error.code === 'TOKEN_MISSING' || response?.status === 401) {
    // 清除本地存储
    clearStoredAuth()
    
    // 跳转到登录页（除非已经在登录页）
    if (!window.location.pathname.includes('/login')) {
      window.location.href = '/login'
    }
    
    return
  }
}

// 封装 fetch 请求，添加错误处理
async function fetchWithErrorHandling(url, options = {}) {
  const response = await fetch(url, options)
  const data = await response.json()
  
  // 如果响应不成功，处理错误
  if (!response.ok || !data.success) {
    handleAuthError(data, response)
  }
  
  return data
}

export const authApi = {
  /**
   * 登录
   */
  async login(username, password) {
    const response = await fetch(`${API_BASE}/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ username, password })
    })
    return response.json()
  },

  /**
   * 注册
   */
  async register(username, password) {
    const response = await fetch(`${API_BASE}/register`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ username, password })
    })
    return response.json()
  },

  /**
   * 获取当前用户信息
   */
  async getMe() {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/me`, {
      headers: {
        'Authorization': `Bearer ${token}`
      }
    })
  },

  /**
   * 修改密码
   */
  async changePassword(oldPassword, newPassword) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/password`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword })
    })
    return response.json()
  },

  /**
   * 登出
   */
  async logout() {
    // Current backend has no dedicated logout endpoint.
    // Keep API shape for caller while performing local logout.
    return { success: true }
  },

  /**
   * 发起密码重置 - 检查安全问题
   */
  async initiatePasswordReset(username) {
    const response = await fetch(`${API_BASE}/forgot-password/initiate`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ username })
    })
    try {
      return await response.json()
    } catch {
      return { success: false, error: '接口暂不可用' }
    }
  },

  /**
   * 验证安全问题并重置密码
   */
  async verifyAndResetPassword(username, answers, newPassword) {
    const response = await fetch(`${API_BASE}/forgot-password/verify`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ username, answers, new_password: newPassword })
    })
    try {
      return await response.json()
    } catch {
      return { success: false, error: '接口暂不可用' }
    }
  },

  /**
   * 获取当前用户设置的安全问题
   */
  async getSecurityQuestions() {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/security-questions`, {
      headers: {
        'Authorization': `Bearer ${token}`
      }
    })
    try {
      return await response.json()
    } catch {
      return { success: false, error: '接口暂不可用' }
    }
  },

  /**
   * 设置/更新安全问题
   */
  async setSecurityQuestions(questions) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/security-questions`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify({ questions })
    })
    try {
      return await response.json()
    } catch {
      return { success: false, error: '接口暂不可用' }
    }
  }
}
