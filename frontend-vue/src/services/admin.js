const API_BASE = '/api/admin'

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

async function safeJson(response) {
  try {
    return await response.json()
  } catch {
    return {}
  }
}

// 全局错误处理函数
function handleAdminError(error, response) {
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
    
    // 跳转到登录页
    window.location.href = '/login'
    
    return
  }
}

// 封装 fetch 请求，添加错误处理
async function fetchWithErrorHandling(url, options = {}) {
  const response = await fetch(url, options)
  const data = await safeJson(response)
  
  // 如果响应不成功，处理错误
  if (!response.ok || !data.success) {
    handleAdminError(data, response)
  }
  
  return data?.success !== undefined
    ? data
    : { success: response.ok, error: response.ok ? '' : `HTTP ${response.status}` }
}

export const adminApi = {
  async getUsers(page = 1, pageSize = 10) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/users?page=${page}&page_size=${pageSize}`, {
      headers: { 'Authorization': `Bearer ${token}` }
    })
  },

  async createUser(username, password, userType = 'common') {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/users`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ username, password, user_type: userType })
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async changeUserPassword(userId, newPassword) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/users/${userId}/password`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ new_password: newPassword })
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async changeUserStatus(userId, status) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/users/${userId}/status`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ status })
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async changeUserType(userId, userType) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/users/${userId}/type`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ user_type: userType })
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async deleteUser(userId) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/users/${userId}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${token}` }
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async batchImportUsers(file) {
    const token = readStoredToken()
    const formData = new FormData()
    formData.append('file', file)
    
    const response = await fetch(`${API_BASE}/users/batch-import`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}` },
      body: formData
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async downloadImportTemplate(format = 'xlsx') {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/users/import-template?format=${format}`, {
      headers: { 'Authorization': `Bearer ${token}` }
    })
    
    if (!response.ok) {
      throw new Error('下载模板失败')
    }
    
    const blob = await response.blob()
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `user_import_template.${format}`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
  }
}
