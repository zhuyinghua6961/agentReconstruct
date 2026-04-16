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

async function uploadFile(url, file, token) {
  const formData = new FormData()
  formData.append('file', file)

  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}` },
    body: formData
  })
  const data = await safeJson(response)
  return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
}

async function downloadFile(url, token, filename) {
  const response = await fetch(url, {
    headers: { 'Authorization': `Bearer ${token}` }
  })

  if (!response.ok) {
    throw new Error('下载模板失败')
  }

  const blob = await response.blob()
  const objectUrl = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(objectUrl)
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

  async createUser(username, password, userType = 'common', department = {}) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/users`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({
        username,
        password,
        user_type: userType,
        primary_department_id: department.primary_department_id ?? null,
        secondary_department_id: department.secondary_department_id ?? null,
      })
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async updateUserDepartment(userId, department = {}) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/users/${userId}/department`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({
        primary_department_id: department.primary_department_id ?? null,
        secondary_department_id: department.secondary_department_id ?? null,
      })
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

  async batchDeleteUsers(userIds) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/users/batch-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ user_ids: userIds })
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async batchChangeUserType(userIds, userType) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/users/batch-type`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ user_ids: userIds, user_type: userType })
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async batchImportUsers(file) {
    const token = readStoredToken()
    return uploadFile(`${API_BASE}/users/batch-import`, file, token)
  },

  async downloadImportTemplate(format = 'xlsx') {
    const token = readStoredToken()
    await downloadFile(`${API_BASE}/users/import-template?format=${format}`, token, `user_import_template.${format}`)
  },

  async getDepartmentTree() {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/departments/tree`, {
      headers: { 'Authorization': `Bearer ${token}` }
    })
  },

  async createPrimaryDepartment(name) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/departments/primary`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ name })
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async renamePrimaryDepartment(primaryId, name) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/departments/primary/${primaryId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ name })
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async updatePrimaryDepartmentStatus(primaryId, status) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/departments/primary/${primaryId}/status`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ status })
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async createSecondaryDepartment(primaryDepartmentId, name) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/departments/secondary`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ primary_department_id: primaryDepartmentId, name })
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async renameSecondaryDepartment(secondaryId, name) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/departments/secondary/${secondaryId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ name })
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async updateSecondaryDepartmentStatus(secondaryId, status) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/departments/secondary/${secondaryId}/status`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ status })
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async batchImportDepartments(file) {
    const token = readStoredToken()
    return uploadFile(`${API_BASE}/departments/batch-import`, file, token)
  },

  async downloadDepartmentImportTemplate(format = 'xlsx') {
    const token = readStoredToken()
    await downloadFile(
      `${API_BASE}/departments/import-template?format=${format}`,
      token,
      `department_import_template.${format}`
    )
  },
}
