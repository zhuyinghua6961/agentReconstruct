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

function formatDisabledPersonnelMessage(error) {
  const personnel = error?.data?.personnel || {}
  const employeeNo = String(personnel.employee_no || '').trim()
  const fullName = String(personnel.full_name || '').trim()
  const departmentDisplay = String(personnel.department_display || '').trim()
  const details = []
  if (employeeNo) details.push(`工号：${employeeNo}`)
  if (fullName) details.push(`姓名：${fullName}`)
  if (departmentDisplay) details.push(`部门：${departmentDisplay}`)
  return ['账号所属人员已停用，请联系管理员', ...details].join('\n')
}

function formatDisabledDepartmentMessage(error) {
  const personnel = error?.data?.personnel || {}
  const employeeNo = String(personnel.employee_no || '').trim()
  const fullName = String(personnel.full_name || '').trim()
  const departmentDisplay = String(personnel.department_display || '').trim()
  const details = []
  if (employeeNo) details.push(`工号：${employeeNo}`)
  if (fullName) details.push(`姓名：${fullName}`)
  if (departmentDisplay) details.push(`部门：${departmentDisplay}`)
  return ['账号所属部门已停用，请联系管理员', ...details].join('\n')
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

  if (error.code === 'PERSONNEL_DISABLED') {
    clearStoredAuth()
    alert(formatDisabledPersonnelMessage(error))
    window.location.href = '/login'
    return
  }

  if (error.code === 'DEPARTMENT_DISABLED') {
    clearStoredAuth()
    alert(formatDisabledDepartmentMessage(error))
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
  let response
  try {
    response = await fetch(url, options)
  } catch (error) {
    return {
      success: false,
      error: error instanceof Error && error.message ? error.message : '网络请求失败'
    }
  }
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
  async getModelStatus() {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/model-status`, {
      headers: { 'Authorization': `Bearer ${token}` }
    })
  },

  async testModelStatus(id) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/model-status/test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ id })
    })
  },

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
      body: JSON.stringify({
        username,
        password,
        user_type: userType,
      })
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async updateUserUsername(userId, username) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/users/${userId}/username`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ username })
    })
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

  async bindUserPersonnel(userId, personnelId) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/users/${userId}/personnel-binding`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ personnel_id: personnelId })
    })
  },

  async unbindUserPersonnel(userId) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/users/${userId}/personnel-binding`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${token}` }
    })
  },

  async getPersonnel(params = {}) {
    const token = readStoredToken()
    const searchParams = new URLSearchParams()
    Object.entries(params || {}).forEach(([key, value]) => {
      if (value === null || value === undefined || value === '') {
        return
      }
      searchParams.set(key, String(value))
    })
    const query = searchParams.toString()
    return fetchWithErrorHandling(
      `${API_BASE}/personnel${query ? `?${query}` : ''}`,
      {
        headers: { 'Authorization': `Bearer ${token}` }
      }
    )
  },

  async createPersonnel(payload) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/personnel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify(payload || {})
    })
  },

  async updatePersonnel(personnelId, payload) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/personnel/${personnelId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify(payload || {})
    })
  },

  async updatePersonnelStatus(personnelId, status) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/personnel/${personnelId}/status`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ status })
    })
  },

  async deletePersonnel(personnelId) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/personnel/${personnelId}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${token}` }
    })
  },

  async forceDeletePersonnel(personnelId, adminPassword) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/personnel/${personnelId}/force-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ admin_password: adminPassword })
    })
  },

  async batchDeletePersonnel(personnelIds) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/personnel/batch-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ personnel_ids: personnelIds })
    })
  },

  async batchUpdatePersonnelStatus(personnelIds, status) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/personnel/batch-status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ personnel_ids: personnelIds, status })
    })
  },

  async batchUpdatePersonnelDepartment(personnelIds, departmentPayload) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/personnel/batch-department`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({
        personnel_ids: personnelIds,
        primary_department_id: departmentPayload?.primary_department_id ?? null,
        secondary_department_id: departmentPayload?.secondary_department_id ?? null,
        tertiary_department_id: departmentPayload?.tertiary_department_id ?? null
      })
    })
  },

  async batchForceDeletePersonnel(personnelIds, adminPassword) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/personnel/batch-force-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ personnel_ids: personnelIds, admin_password: adminPassword })
    })
  },

  async getPersonnelBindings(personnelId) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/personnel/${personnelId}/bindings`, {
      headers: { 'Authorization': `Bearer ${token}` }
    })
  },

  async batchImportPersonnel(file) {
    const token = readStoredToken()
    return uploadFile(`${API_BASE}/personnel/batch-import`, file, token)
  },

  async downloadPersonnelImportTemplate(format = 'xlsx') {
    const token = readStoredToken()
    await downloadFile(
      `${API_BASE}/personnel/import-template?format=${format}`,
      token,
      `personnel_import_template.${format}`
    )
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

  async deletePrimaryDepartment(primaryId) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/departments/primary/${primaryId}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${token}` }
    })
  },

  async updatePrimaryDepartmentStatus(primaryId, status) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/departments/primary/${primaryId}/status`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ status })
    })
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

  async deleteSecondaryDepartment(secondaryId) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/departments/secondary/${secondaryId}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${token}` }
    })
  },

  async updateSecondaryDepartmentStatus(secondaryId, status) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/departments/secondary/${secondaryId}/status`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ status })
    })
  },

  async createTertiaryDepartment(secondaryDepartmentId, name) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/departments/tertiary`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ secondary_department_id: secondaryDepartmentId, name })
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async renameTertiaryDepartment(tertiaryId, name) {
    const token = readStoredToken()
    const response = await fetch(`${API_BASE}/departments/tertiary/${tertiaryId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ name })
    })
    const data = await safeJson(response)
    return data?.success !== undefined ? data : { success: false, error: `HTTP ${response.status}` }
  },

  async deleteTertiaryDepartment(tertiaryId) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/departments/tertiary/${tertiaryId}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${token}` }
    })
  },

  async updateTertiaryDepartmentStatus(tertiaryId, status) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/departments/tertiary/${tertiaryId}/status`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ status })
    })
  },

  async batchDeleteDepartments(items) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/departments/batch-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ items })
    })
  },

  async batchUpdateDepartmentStatus(items, status) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/departments/batch-status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ items, status })
    })
  },

  async forceDeleteDepartment(level, departmentId, adminPassword) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/departments/${level}/${departmentId}/force-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ admin_password: adminPassword })
    })
  },

  async batchForceDeleteDepartments(items, adminPassword) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/departments/batch-force-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ items, admin_password: adminPassword })
    })
  },

  async getTertiaryDepartmentUsers(tertiaryId) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/departments/tertiary/${tertiaryId}/users`, {
      headers: { 'Authorization': `Bearer ${token}` }
    })
  },

  async getPrimaryDirectDepartmentUsers(primaryId) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/departments/primary/${primaryId}/direct-users`, {
      headers: { 'Authorization': `Bearer ${token}` }
    })
  },

  async getSecondaryDirectDepartmentUsers(secondaryId) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/departments/secondary/${secondaryId}/direct-users`, {
      headers: { 'Authorization': `Bearer ${token}` }
    })
  },

  async getSecondaryLegacyDepartmentUsers(secondaryId) {
    const token = readStoredToken()
    return fetchWithErrorHandling(`${API_BASE}/departments/secondary/${secondaryId}/legacy-users`, {
      headers: { 'Authorization': `Bearer ${token}` }
    })
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

  async getUsageStats(params = {}) {
    const token = readStoredToken()
    const search = new URLSearchParams()
    if (params.from) search.set('from', params.from)
    if (params.to) search.set('to', params.to)
    if (params.page) search.set('page', String(params.page))
    if (params.page_size) search.set('page_size', String(params.page_size))
    if (params.keyword) search.set('keyword', params.keyword)
    if (params.primary_department_id) search.set('primary_department_id', String(params.primary_department_id))
    if (params.secondary_department_id) search.set('secondary_department_id', String(params.secondary_department_id))
    if (params.tertiary_department_id) search.set('tertiary_department_id', String(params.tertiary_department_id))
    if (params.sort_by) search.set('sort_by', params.sort_by)
    if (params.sort_order) search.set('sort_order', params.sort_order)
    const query = search.toString()
    const url = query ? `${API_BASE}/usage-stats?${query}` : `${API_BASE}/usage-stats`
    return fetchWithErrorHandling(url, {
      headers: { Authorization: `Bearer ${token}` },
    })
  },

  async exportUsageStats(params = {}) {
    const token = readStoredToken()
    const search = new URLSearchParams()
    if (params.from) search.set('from', params.from)
    if (params.to) search.set('to', params.to)
    if (params.keyword) search.set('keyword', params.keyword)
    if (params.primary_department_id) search.set('primary_department_id', String(params.primary_department_id))
    if (params.secondary_department_id) search.set('secondary_department_id', String(params.secondary_department_id))
    if (params.tertiary_department_id) search.set('tertiary_department_id', String(params.tertiary_department_id))
    if (params.sort_by) search.set('sort_by', params.sort_by)
    if (params.sort_order) search.set('sort_order', params.sort_order)
    const format = params.format === 'csv' ? 'csv' : 'xlsx'
    search.set('format', format)
    const filename = `usage_stats_${params.from || 'from'}_${params.to || 'to'}.${format}`
    await downloadFile(`${API_BASE}/usage-stats/export?${search.toString()}`, token, filename)
  },
}
