<script setup>
import { computed, ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authApi } from '../services/auth'
import { adminApi } from '../services/admin'
import BatchImportDialog from '../components/BatchImportDialog.vue'
import DepartmentManagementPanel from '../components/DepartmentManagementPanel.vue'
import ImportResultDialog from '../components/ImportResultDialog.vue'
import PersonnelLookupSelect from '../components/PersonnelLookupSelect.vue'
import PersonnelManagementPanel from '../components/PersonnelManagementPanel.vue'
import QuotaManagementPanel from '../components/QuotaManagementPanel.vue'
import { runPersonnelManagementRefresh } from '../utils/personnelManagementSync'

const route = useRoute()
const router = useRouter()

const DEFAULT_ADMIN_TAB = 'users'
const ADMIN_TAB_ITEMS = [
  { key: 'models', label: '模型状态' },
  { key: 'quota', label: '配额管理' },
  { key: 'users', label: '用户管理' },
  { key: 'departments', label: '部门管理' },
]

const currentUser = ref(null)
const users = ref([])
const loading = ref(false)
const error = ref('')
const success = ref('')
const pagination = ref({ page: 1, pageSize: 10, total: 0 })
const activeUserManagementTab = ref('accounts')
const modelStatus = ref(null)
const modelStatusLoading = ref(false)
const modelStatusError = ref('')
const modelTestStates = ref({})

const showPasswordModal = ref(false)
const showStatusModal = ref(false)
const showDeleteModal = ref(false)
const showCreateModal = ref(false)
const showResetPasswordModal = ref(false)
const showBatchImportDialog = ref(false)
const showImportResultDialog = ref(false)
const showUsernameModal = ref(false)
const showPersonnelModal = ref(false)
const importResult = ref(null)
const operationResultTitle = ref('操作结果')
const selectedUserIds = ref([])
const showBatchTypeModal = ref(false)
const batchTargetType = ref('super')
const selectedUser = ref(null)
const newPassword = ref('')
const newUsername = ref('')
const editUsernameValue = ref('')
const newUserPassword = ref('')
const newUserType = ref('common')  // 默认为普通用户
const showPassword = ref(false)
const showCreatePassword = ref(false)
const resetPasswordValue = ref('')
const selectedPersonnelId = ref(null)
const selectedPersonnelSummary = ref('')
const personnelLookupOptions = ref([])

const currentPageUserIds = computed(() => users.value.map(user => user.id))
const hasSelectedUsers = computed(() => selectedUserIds.value.length > 0)
const allCurrentPageSelected = computed(() => (
  currentPageUserIds.value.length > 0
  && currentPageUserIds.value.every(id => selectedUserIds.value.includes(id))
))
const activeAdminTab = computed(() => {
  const rawTab = Array.isArray(route.query.tab) ? route.query.tab[0] : route.query.tab
  const normalized = String(rawTab || '').trim().toLowerCase()
  return ADMIN_TAB_ITEMS.some(item => item.key === normalized) ? normalized : DEFAULT_ADMIN_TAB
})
const modelStatusEndpoints = computed(() => {
  const endpoints = modelStatus.value?.endpoints
  return Array.isArray(endpoints) ? endpoints : []
})
const modelStatusSummary = computed(() => modelStatus.value?.summary || {})

async function setAdminTab(tab) {
  if (!ADMIN_TAB_ITEMS.some(item => item.key === tab) || activeAdminTab.value === tab) {
    return
  }
  await router.replace({
    path: '/admin',
    query: {
      ...route.query,
      tab,
    },
  })
}

async function ensureAdminTab() {
  const rawTab = Array.isArray(route.query.tab) ? route.query.tab[0] : route.query.tab
  const normalized = String(rawTab || '').trim().toLowerCase()
  if (ADMIN_TAB_ITEMS.some(item => item.key === normalized)) {
    return
  }
  await router.replace({
    path: '/admin',
    query: {
      ...route.query,
      tab: DEFAULT_ADMIN_TAB,
    },
  })
}

function generateTemporaryPassword(length = 14) {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^&*'
  let result = ''
  for (let i = 0; i < length; i += 1) {
    result += chars.charAt(Math.floor(Math.random() * chars.length))
  }
  return result
}

// 获取角色显示名称
function getRoleText(user) {
  // 优先根据 user_type 判断（更准确）
  const userType = user.user_type
  console.log(`getRoleText - username: ${user.username}, user_type: ${userType}, role: ${user.role}`)
  
  // user_type = 1: 管理员
  if (userType === 1 || user.role === 'admin') {
    return '管理员'
  }
  
  // user_type = 2: 超级用户
  if (userType === 2) {
    return '超级用户'
  }
  
  // user_type = 3 或其他: 普通用户
  return '普通用户'
}

// 获取角色样式类名
function getRoleClass(user) {
  const userType = user.user_type
  
  // user_type = 1: 管理员
  if (userType === 1 || user.role === 'admin') {
    return 'admin'
  }
  
  // user_type = 2: 超级用户
  if (userType === 2) {
    return 'super'
  }
  
  // user_type = 3 或其他: 普通用户
  return 'common'
}

function isAdminIdentity(user) {
  return user?.user_type === 1 || user?.role === 'admin'
}

function getPersonnelDisplay(user) {
  return user?.personnel_display || '未绑定'
}

function getTargetUserType(user) {
  if (isAdminIdentity(user)) return ''
  return user?.user_type === 2 ? 'common' : 'super'
}

async function fetchCurrentUser() {
  const result = await authApi.getMe()
  if (result.success) currentUser.value = result.data
}

async function fetchUsers() {
  loading.value = true
  error.value = ''
  try {
    const result = await adminApi.getUsers(pagination.value.page, pagination.value.pageSize)
    console.log('fetchUsers - API result:', result)
    if (result.success) {
      users.value = result.data
      selectedUserIds.value = selectedUserIds.value.filter(id => users.value.some(user => user.id === id))
      console.log('fetchUsers - users.value:', users.value)
      pagination.value.total = result.pagination.total
    } else {
      error.value = result.error
    }
  } catch (e) {
    error.value = '获取用户列表失败'
  } finally {
    loading.value = false
  }
}

async function fetchModelStatus() {
  modelStatusLoading.value = true
  modelStatusError.value = ''
  try {
    const result = await adminApi.getModelStatus()
    if (result.success) {
      modelStatus.value = result.data || {}
      modelTestStates.value = {}
      return
    }
    modelStatusError.value = result.error || '获取模型状态失败'
  } catch {
    modelStatusError.value = '获取模型状态失败'
  } finally {
    modelStatusLoading.value = false
  }
}

function getModelStatusText(status) {
  const normalized = String(status || '').trim().toLowerCase()
  if (normalized === 'configured') return '已配置'
  if (normalized === 'unconfigured') return '未配置'
  if (normalized === 'disabled') return '已停用'
  return '未知'
}

function getModelKindText(kind) {
  const normalized = String(kind || '').trim().toLowerCase()
  if (normalized === 'chat') return '对话'
  if (normalized === 'embedding') return '向量'
  if (normalized === 'rerank') return '重排'
  return normalized || '其他'
}

function getModelTestState(id) {
  return modelTestStates.value[id] || {}
}

function setModelTestState(id, value) {
  modelTestStates.value = {
    ...modelTestStates.value,
    [id]: {
      ...(modelTestStates.value[id] || {}),
      ...value,
    },
  }
}

async function testModelEndpoint(item) {
  if (!item?.id || !item.test_supported) {
    return
  }
  setModelTestState(item.id, { loading: true, result: null, error: '' })
  try {
    const result = await adminApi.testModelStatus(item.id)
    if (result.success) {
      setModelTestState(item.id, { loading: false, result: result.data || {}, error: '' })
      return
    }
    setModelTestState(item.id, { loading: false, result: null, error: result.error || '测试失败' })
  } catch {
    setModelTestState(item.id, { loading: false, result: null, error: '测试失败' })
  }
}

function getModelTestText(item) {
  const state = getModelTestState(item.id)
  if (state.loading) return '测试中...'
  return '测试'
}

function formatModelDimensionSummary(result) {
  const detected = Number(result?.detected_dimension)
  const expected = Number(result?.expected_dimension)
  const hasDetected = Number.isFinite(detected) && detected > 0
  const hasExpected = Number.isFinite(expected) && expected > 0
  if (hasDetected && hasExpected) {
    return detected === expected ? `维度 ${detected}` : `维度 ${detected} / 期望 ${expected}`
  }
  if (hasDetected) return `维度 ${detected}`
  if (hasExpected) return `期望维度 ${expected}`
  return ''
}

function getModelTestResultText(item) {
  const state = getModelTestState(item.id)
  if (state.loading) return '测试中'
  if (state.error) return state.error
  const result = state.result
  if (!result) return '未测试'
  const dimensionSummary = formatModelDimensionSummary(result)
  if (result.ok) {
    const elapsed = result.elapsed_ms !== null && result.elapsed_ms !== undefined ? `，${result.elapsed_ms} ms` : ''
    const detail = dimensionSummary ? `，${dimensionSummary}` : ''
    return `响应正常${detail}${elapsed}`
  }
  if (dimensionSummary && result.message && !String(result.message).includes(dimensionSummary)) {
    return `${result.message}（${dimensionSummary}）`
  }
  return result.message || '测试失败'
}

function getModelTestResultClass(item) {
  const state = getModelTestState(item.id)
  if (state.loading) return 'pending'
  if (state.error) return 'failed'
  if (state.result?.ok) return 'ok'
  if (state.result) return 'failed'
  return 'idle'
}

function toggleUserSelection(userId) {
  const normalized = Number(userId)
  if (selectedUserIds.value.includes(normalized)) {
    selectedUserIds.value = selectedUserIds.value.filter(id => id !== normalized)
    return
  }
  selectedUserIds.value = [...selectedUserIds.value, normalized]
}

function toggleSelectAllCurrentPage() {
  if (allCurrentPageSelected.value) {
    selectedUserIds.value = selectedUserIds.value.filter(id => !currentPageUserIds.value.includes(id))
    return
  }
  selectedUserIds.value = Array.from(new Set([...selectedUserIds.value, ...currentPageUserIds.value]))
}

function clearSelectedUsers() {
  selectedUserIds.value = []
}

function openOperationResult(title, resultData) {
  operationResultTitle.value = title
  importResult.value = resultData
  showImportResultDialog.value = true
}

function openPasswordModal(user) {
  selectedUser.value = user
  newPassword.value = ''
  showPasswordModal.value = true
}

function openUsernameModal(user) {
  selectedUser.value = user
  editUsernameValue.value = user?.username || ''
  error.value = ''
  showUsernameModal.value = true
}

async function submitUsernameChange() {
  error.value = ''
  if (!selectedUser.value) {
    error.value = '未选择要编辑的用户'
    return
  }
  const normalizedUsername = String(editUsernameValue.value || '').trim()
  if (!normalizedUsername) {
    error.value = '用户名不能为空'
    return
  }
  if (normalizedUsername.length < 3 || normalizedUsername.length > 50) {
    error.value = '用户名长度必须在3-50之间'
    return
  }
  if (normalizedUsername.toLowerCase().startsWith('admin')) {
    error.value = '不能以 admin 为前缀'
    return
  }

  const previousUsername = selectedUser.value.username
  const result = await adminApi.updateUserUsername(selectedUser.value.id, normalizedUsername)
  if (result.success) {
    success.value = `用户 ${previousUsername} 的用户名已更新为 ${normalizedUsername}`
    showUsernameModal.value = false
    await fetchUsers()
    setTimeout(() => success.value = '', 3000)
    return
  }
  error.value = result.error || '修改用户名失败'
}

async function submitPasswordChange() {
  if (!newPassword.value || newPassword.value.length < 6) {
    error.value = '密码长度不能少于6位'
    return
  }
  const result = await adminApi.changeUserPassword(selectedUser.value.id, newPassword.value)
  if (result.success) {
    success.value = `用户 ${selectedUser.value.username} 的密码已修改`
    showPasswordModal.value = false
    setTimeout(() => success.value = '', 3000)
  } else {
    error.value = result.error
  }
}

function openStatusModal(user) {
  selectedUser.value = user
  showStatusModal.value = true
}

async function submitStatusChange(status) {
  const result = await adminApi.changeUserStatus(selectedUser.value.id, status)
  if (result.success) {
    success.value = `用户 ${selectedUser.value.username} 已${status === 'disabled' ? '停用' : '启用'}`
    showStatusModal.value = false
    await fetchUsers()
    setTimeout(() => success.value = '', 3000)
  } else {
    error.value = result.error
  }
}

async function toggleUserType(user) {
  const targetType = getTargetUserType(user)
  if (!targetType) {
    error.value = '管理员身份不支持切换'
    return
  }
  const targetText = targetType === 'super' ? '超级用户' : '普通用户'
  if (!window.confirm(`确定将用户 ${user.username} 切换为${targetText}吗？`)) {
    return
  }

  const result = await adminApi.changeUserType(user.id, targetType)
  if (result.success) {
    success.value = `用户 ${user.username} 已切换为${targetText}`
    await fetchUsers()
    setTimeout(() => success.value = '', 3000)
    return
  }
  error.value = result.error || '切换用户身份失败'
}

function openDeleteModal(user) {
  selectedUser.value = user
  showDeleteModal.value = true
}

async function submitDelete() {
  const result = await adminApi.deleteUser(selectedUser.value.id)
  if (result.success) {
    success.value = `用户 ${selectedUser.value.username} 已删除`
    showDeleteModal.value = false
    await fetchUsers()
    setTimeout(() => success.value = '', 3000)
  } else {
    error.value = result.error
  }
}

async function submitBatchDelete() {
  error.value = ''
  if (!hasSelectedUsers.value) {
    error.value = '请至少选择一个用户'
    return
  }
  if (!window.confirm(`确定批量删除选中的 ${selectedUserIds.value.length} 个用户吗？此操作不可恢复。`)) {
    return
  }

  const result = await adminApi.batchDeleteUsers(selectedUserIds.value)
  if (result.success) {
    openOperationResult('批量删除结果', result.data)
    success.value = `批量删除完成：成功 ${result.data?.summary?.success || 0} 条，失败 ${result.data?.summary?.failed || 0} 条`
    clearSelectedUsers()
    await fetchUsers()
    setTimeout(() => success.value = '', 5000)
  } else {
    error.value = result.error || '批量删除失败'
  }
}

function openBatchTypeModal() {
  error.value = ''
  if (!hasSelectedUsers.value) {
    error.value = '请至少选择一个用户'
    return
  }
  batchTargetType.value = 'super'
  showBatchTypeModal.value = true
}

async function submitBatchTypeChange() {
  error.value = ''
  const result = await adminApi.batchChangeUserType(selectedUserIds.value, batchTargetType.value)
  if (result.success) {
    const targetText = batchTargetType.value === 'super' ? '超级用户' : '普通用户'
    openOperationResult('批量修改用户类型结果', result.data)
    success.value = `批量修改完成：已尝试切换为${targetText}，成功 ${result.data?.summary?.success || 0} 条`
    showBatchTypeModal.value = false
    clearSelectedUsers()
    await fetchUsers()
    setTimeout(() => success.value = '', 5000)
  } else {
    error.value = result.error || '批量修改用户类型失败'
  }
}

async function logout() {
  await authApi.logout()
  localStorage.removeItem('token')
  localStorage.removeItem('user')
  localStorage.removeItem('agentcode.auth.token.v1')
  localStorage.removeItem('agentcode.auth.user.v1')
  window.location.href = '/login'
}

function changePage(page) {
  pagination.value.page = page
  fetchUsers()
}

function openCreateModal() {
  newUsername.value = ''
  newUserPassword.value = ''
  newUserType.value = 'common'  // 重置为默认值
  error.value = ''
  showCreateModal.value = true
}

function openPersonnelModal(user) {
  selectedUser.value = user
  selectedPersonnelId.value = user?.personnel_id ?? null
  selectedPersonnelSummary.value = user?.personnel_display || '未绑定'
  error.value = ''
  showPersonnelModal.value = true
  void loadPersonnelLookupOptions()
}

function handlePersonnelSelected(item) {
  selectedPersonnelId.value = item?.id ?? null
  selectedPersonnelSummary.value = item ? `${item.employee_no} / ${item.full_name}` : ''
}

async function loadPersonnelLookupOptions(keyword = '') {
  const result = await adminApi.getPersonnel({
    keyword,
    status: 'active',
    page_size: 100,
  })
  if (result.success) {
    personnelLookupOptions.value = Array.isArray(result.data?.items) ? result.data.items : []
    return
  }
  personnelLookupOptions.value = []
}

async function submitPersonnelBinding() {
  error.value = ''
  if (!selectedUser.value) {
    error.value = '未选择要编辑的用户'
    return
  }
  if (!selectedPersonnelId.value) {
    error.value = '请选择要绑定的人员'
    return
  }

  const result = await adminApi.bindUserPersonnel(selectedUser.value.id, selectedPersonnelId.value)
  if (result.success) {
    success.value = `用户 ${selectedUser.value.username} 的人员信息已更新`
    showPersonnelModal.value = false
    await fetchUsers()
    setTimeout(() => success.value = '', 3000)
    return
  }
  error.value = result.error || '设置人员失败'
}

async function submitPersonnelUnbind() {
  error.value = ''
  if (!selectedUser.value) {
    error.value = '未选择要编辑的用户'
    return
  }

  const result = await adminApi.unbindUserPersonnel(selectedUser.value.id)
  if (result.success) {
    success.value = `用户 ${selectedUser.value.username} 的人员绑定已解除`
    showPersonnelModal.value = false
    await fetchUsers()
    setTimeout(() => success.value = '', 3000)
    return
  }
  error.value = result.error || '解绑人员失败'
}

async function openResetPasswordModal(user) {
  selectedUser.value = user
  const tempPassword = generateTemporaryPassword()
  const result = await adminApi.changeUserPassword(user.id, tempPassword)
  if (result.success) {
    resetPasswordValue.value = tempPassword
    showResetPasswordModal.value = true
    success.value = `用户 ${user.username} 的密码已重置`
    setTimeout(() => success.value = '', 3000)
  } else {
    error.value = result.error || '重置密码失败'
  }
}

async function copyResetPassword() {
  const text = String(resetPasswordValue.value || '')
  if (!text) {
    error.value = '没有可复制的密码'
    return
  }

  try {
    // Clipboard API 仅在安全上下文(https/localhost)可靠可用
    if (window.isSecureContext && navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      success.value = '临时密码已复制到剪贴板'
      setTimeout(() => success.value = '', 2000)
      return
    }

    // 降级方案：使用临时 textarea + execCommand
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.setAttribute('readonly', 'readonly')
    textarea.style.position = 'fixed'
    textarea.style.top = '-9999px'
    textarea.style.left = '-9999px'
    document.body.appendChild(textarea)
    textarea.focus()
    textarea.select()
    const copied = document.execCommand('copy')
    document.body.removeChild(textarea)

    if (copied) {
      success.value = '临时密码已复制到剪贴板'
      setTimeout(() => success.value = '', 2000)
      return
    }

    throw new Error('copy command failed')
  } catch {
    // 最后兜底：让用户手动复制
    try {
      window.prompt('浏览器限制自动复制，请手动复制下方临时密码：', text)
    } catch {
      // ignore
    }
    error.value = '自动复制失败，请手动复制'
    setTimeout(() => error.value = '', 2500)
  }
}

async function submitCreateUser() {
  error.value = ''
  
  if (!newUsername.value || !newUserPassword.value) {
    error.value = '用户名和密码不能为空'
    return
  }
  
  if (newUsername.value.length < 3 || newUsername.value.length > 50) {
    error.value = '用户名长度必须在3-50之间'
    return
  }
  
  if (newUsername.value.toLowerCase().startsWith('admin')) {
    error.value = '不能创建以 admin 为前缀的用户名'
    return
  }
  
  const result = await adminApi.createUser(
    newUsername.value,
    newUserPassword.value,
    newUserType.value,
  )
  
  if (result.success) {
    success.value = `用户 ${newUsername.value} 创建成功`
    showCreateModal.value = false
    await fetchUsers()
    setTimeout(() => success.value = '', 3000)
  } else {
    error.value = result.error
  }
}

function openBatchImportDialog() {
  showBatchImportDialog.value = true
}

function handleImportSuccess(result) {
  openOperationResult('批量导入结果', result)
  
  // 显示成功消息
  const { summary } = result
  success.value = `导入完成：成功 ${summary.success} 条，失败 ${summary.failed} 条，跳过 ${summary.skipped} 条`
  setTimeout(() => success.value = '', 5000)
  
  // 刷新用户列表
  fetchUsers()
}

async function handleDepartmentDictionaryUpdated() {
  await fetchUsers()
}

async function handlePersonnelManagementUpdated() {
  await runPersonnelManagementRefresh(async () => {
    await fetchUsers()
  })
}

watch(activeAdminTab, (tab) => {
  if (tab === 'models' && !modelStatus.value && !modelStatusLoading.value) {
    void fetchModelStatus()
  }
})

onMounted(async () => {
  await ensureAdminTab()
  await fetchCurrentUser()
  await fetchUsers()
  if (activeAdminTab.value === 'models') {
    await fetchModelStatus()
  }
})
</script>

<template>
  <div class="admin-container">
    <header class="admin-header">
      <div class="header-left">
        <h1>管理员后台</h1>
        <span class="user-info" v-if="currentUser">管理员: {{ currentUser.username }}</span>
      </div>
      <div class="header-actions">
        <nav class="admin-tabs" aria-label="管理员顶部导航">
          <a href="/profile" class="admin-tab-btn profile-tab-btn">个人中心</a>
          <button
            v-for="item in ADMIN_TAB_ITEMS"
            :key="item.key"
            type="button"
            class="admin-tab-btn"
            :class="{ active: activeAdminTab === item.key }"
            @click="setAdminTab(item.key)"
          >
            {{ item.label }}
          </button>
        </nav>
        <button class="logout-btn" @click="logout">退出登录</button>
      </div>
    </header>

    <main class="admin-main">
      <div v-if="success" class="alert alert-success">{{ success }}</div>
      <div v-if="error" class="alert alert-error">{{ error }}</div>

      <section
        v-if="activeAdminTab === 'quota'"
        class="quota-management-shell"
        aria-label="配额管理"
      >
        <QuotaManagementPanel />
      </section>

      <section
        v-else-if="activeAdminTab === 'models'"
        class="model-status-shell"
        aria-label="模型状态"
      >
        <div class="section-header">
          <div>
            <h2>模型状态</h2>
            <p class="model-status-note">刷新只读取配置；点击测试才发送 hello 最小请求。</p>
          </div>
          <button class="action-btn" :disabled="modelStatusLoading" @click="fetchModelStatus">
            {{ modelStatusLoading ? '刷新中...' : '刷新' }}
          </button>
        </div>

        <div v-if="modelStatusError" class="alert alert-error">{{ modelStatusError }}</div>
        <div v-if="modelStatusLoading && modelStatusEndpoints.length === 0" class="loading">加载中...</div>

        <div v-else class="model-status-panel">
          <div class="model-status-summary">
            <span><strong>{{ modelStatusSummary.total || 0 }}</strong> 总计</span>
            <span><strong>{{ modelStatusSummary.configured || 0 }}</strong> 已配置</span>
            <span><strong>{{ modelStatusSummary.unconfigured || 0 }}</strong> 未配置</span>
            <span><strong>{{ modelStatusSummary.disabled || 0 }}</strong> 已停用</span>
          </div>

          <div v-if="modelStatusEndpoints.length === 0" class="model-status-empty">
            暂无模型配置
          </div>

          <div v-else class="model-endpoint-list">
            <article
              v-for="item in modelStatusEndpoints"
              :key="item.id"
              class="model-endpoint-card"
              :class="'model-endpoint-' + item.status"
            >
              <div class="model-card-main">
                <div class="model-card-head">
                  <div class="model-name-cell">
                    <h3>{{ item.label }}</h3>
                    <div class="model-card-meta">
                      <span>{{ getModelKindText(item.kind) }}</span>
                      <span>{{ item.model || '未填写模型' }}</span>
                    </div>
                  </div>
                  <span class="model-status-badge" :class="'model-status-' + item.status">
                    {{ getModelStatusText(item.status) }}
                  </span>
                </div>

                <div class="model-route-grid">
                  <div class="model-route-item">
                    <span>Base URL</span>
                    <code>{{ item.base_url || '未配置' }}</code>
                  </div>
                  <div class="model-route-item">
                    <span>请求地址</span>
                    <code>{{ item.endpoint_url || '-' }}</code>
                  </div>
                </div>
              </div>

              <aside class="model-card-side">
                <div class="model-auth-cell">
                  <span>鉴权模式：{{ item.auth_mode || 'bearer' }}</span>
                  <span>{{ item.api_key_present ? '已配置 key' : '未配置 key' }}</span>
                  <small v-if="item.api_key_input_has_bearer">配置含 Bearer</small>
                  <small v-if="item.key_fingerprint">fp {{ item.key_fingerprint }}</small>
                </div>
                <div class="model-test-cell">
                  <button
                    class="action-btn"
                    :disabled="!item.test_supported || getModelTestState(item.id).loading"
                    @click="testModelEndpoint(item)"
                  >
                    {{ getModelTestText(item) }}
                  </button>
                  <span class="model-test-result" :class="'model-test-' + getModelTestResultClass(item)">
                    {{ getModelTestResultText(item) }}
                  </span>
                </div>
              </aside>
            </article>
          </div>
          <p v-if="modelStatus?.checked_at" class="model-status-checked">检查时间：{{ modelStatus.checked_at }}</p>
        </div>
      </section>

      <section
        v-else-if="activeAdminTab === 'departments'"
        class="department-management-shell"
        aria-label="部门管理"
      >
        <DepartmentManagementPanel @updated="handleDepartmentDictionaryUpdated" />
      </section>

      <div v-else class="user-section">
        <div class="section-header">
          <h2>用户管理</h2>
          <div class="user-subtabs">
            <button
              type="button"
              class="admin-tab-btn"
              :class="{ active: activeUserManagementTab === 'accounts' }"
              @click="activeUserManagementTab = 'accounts'"
            >
              账号列表
            </button>
            <button
              type="button"
              class="admin-tab-btn"
              :class="{ active: activeUserManagementTab === 'personnel' }"
              @click="activeUserManagementTab = 'personnel'"
            >
              人员表
            </button>
          </div>
        </div>

        <section v-if="activeUserManagementTab === 'personnel'" class="user-management-panel">
          <PersonnelManagementPanel @updated="handlePersonnelManagementUpdated" />
        </section>

        <template v-else>
          <div class="header-actions">
            <button class="action-btn batch-action-btn" :disabled="!hasSelectedUsers" @click="openBatchTypeModal">
              批量改类型
            </button>
            <button class="action-btn btn-danger" :disabled="!hasSelectedUsers" @click="submitBatchDelete">
              批量删除
            </button>
            <button class="action-btn" :disabled="!hasSelectedUsers" @click="clearSelectedUsers">
              清空选择
            </button>
            <button class="add-user-btn batch-import-btn" @click="openBatchImportDialog">批量导入</button>
            <button class="add-user-btn" @click="openCreateModal">添加用户</button>
          </div>

          <div v-if="hasSelectedUsers" class="selection-summary">
            已选择 {{ selectedUserIds.length }} 个用户
          </div>

          <div v-if="loading" class="loading">加载中...</div>

          <table v-else class="user-table">
          <thead>
            <tr>
              <th class="checkbox-col">
                <input
                  type="checkbox"
                  :checked="allCurrentPageSelected"
                  @change="toggleSelectAllCurrentPage"
                >
              </th>
              <th>ID</th>
              <th>用户名</th>
              <th>角色</th>
              <th>部门</th>
              <th>人员信息</th>
              <th>状态</th>
              <th>创建时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in users" :key="user.id">
              <td class="checkbox-col">
                <input
                  type="checkbox"
                  :checked="selectedUserIds.includes(user.id)"
                  @change="toggleUserSelection(user.id)"
                >
              </td>
              <td>{{ user.id }}</td>
              <td>{{ user.username }}</td>
              <td><span class="role-badge" :class="getRoleClass(user)">{{ getRoleText(user) }}</span></td>
              <td>
                <span :class="{ 'department-disabled': user.department_effective_status === 'disabled' }">
                  {{ user.department_display || '未填写' }}
                </span>
              </td>
              <td>{{ user.personnel_display || getPersonnelDisplay(user) }}</td>
              <td><span class="status-badge" :class="user.status">{{ user.status === 'active' ? '正常' : '停用' }}</span></td>
              <td>{{ user.created_at }}</td>
              <td class="actions">
                <button class="action-btn" @click="openPersonnelModal(user)">设置人员</button>
                <button
                  v-if="!isAdminIdentity(user)"
                  class="action-btn"
                  @click="openUsernameModal(user)"
                >
                  修改用户名
                </button>
                <button class="action-btn" @click="openResetPasswordModal(user)">重置密码</button>
                <button
                  v-if="!isAdminIdentity(user)"
                  class="action-btn"
                  @click="toggleUserType(user)"
                >
                  {{ user.user_type === 2 ? '设为普通' : '设为超级' }}
                </button>
                <button class="action-btn" :class="user.status === 'active' ? 'btn-danger' : 'btn-success'" @click="openStatusModal(user)">
                  {{ user.status === 'active' ? '停用' : '启用' }}
                </button>
                <button class="action-btn btn-danger" @click="openDeleteModal(user)">删除</button>
              </td>
            </tr>
          </tbody>
          </table>

          <div v-if="pagination.total > pagination.pageSize" class="pagination">
            <button :disabled="pagination.page === 1" @click="changePage(pagination.page - 1)">上一页</button>
            <span>第 {{ pagination.page }} 页 / 共 {{ Math.ceil(pagination.total / pagination.pageSize) }} 页</span>
            <button :disabled="pagination.page * pagination.pageSize >= pagination.total" @click="changePage(pagination.page + 1)">下一页</button>
          </div>
        </template>
      </div>
    </main>

    <!-- Batch Import Dialogs -->
    <BatchImportDialog 
      :show="showBatchImportDialog" 
      @close="showBatchImportDialog = false"
      @import-success="handleImportSuccess"
    />
    
    <ImportResultDialog 
      :show="showImportResultDialog" 
      :result="importResult"
      :title="operationResultTitle"
      @close="showImportResultDialog = false"
    />

    <!-- Modals -->
    <div v-if="showPasswordModal" class="modal-overlay" @click.self="showPasswordModal = false">
      <div class="modal">
        <h3>修改密码 - {{ selectedUser?.username }}</h3>
        <div class="modal-body">
          <div class="form-group">
            <label>新密码</label>
            <div class="password-input">
              <input :type="showPassword ? 'text' : 'password'" v-model="newPassword" placeholder="请输入新密码（12位以上，包含大小写字母、数字、特殊符号）">
              <button class="toggle-password" @click="showPassword = !showPassword">
                {{ showPassword ? '👁️' : '👁️‍🗨️' }}
              </button>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn-secondary" @click="showPasswordModal = false">取消</button>
          <button class="btn-primary" @click="submitPasswordChange">确认修改</button>
        </div>
      </div>
    </div>

    <div v-if="showUsernameModal" class="modal-overlay" @click.self="showUsernameModal = false">
      <div class="modal">
        <h3>修改用户名 - {{ selectedUser?.username }}</h3>
        <div class="modal-body">
          <div class="form-group">
            <label>新用户名</label>
            <input type="text" v-model="editUsernameValue" placeholder="请输入新用户名（3-50字符）">
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn-secondary" @click="showUsernameModal = false">取消</button>
          <button class="btn-primary" @click="submitUsernameChange">确认修改</button>
        </div>
      </div>
    </div>

    <div v-if="showStatusModal" class="modal-overlay" @click.self="showStatusModal = false">
      <div class="modal">
        <h3>{{ selectedUser?.status === 'active' ? '停用' : '启用' }}用户 - {{ selectedUser?.username }}</h3>
        <div class="modal-body"><p>确定要{{ selectedUser?.status === 'active' ? '停用' : '启用' }}该用户吗？</p></div>
        <div class="modal-footer">
          <button class="btn-secondary" @click="showStatusModal = false">取消</button>
          <button class="btn-danger" @click="submitStatusChange(selectedUser?.status === 'active' ? 'disabled' : 'active')">确认</button>
        </div>
      </div>
    </div>

    <div v-if="showDeleteModal" class="modal-overlay" @click.self="showDeleteModal = false">
      <div class="modal">
        <h3>删除用户 - {{ selectedUser?.username }}</h3>
        <div class="modal-body"><p class="warning">确定要删除该用户吗？此操作不可恢复！</p></div>
        <div class="modal-footer">
          <button class="btn-secondary" @click="showDeleteModal = false">取消</button>
          <button class="btn-danger" @click="submitDelete">确认删除</button>
        </div>
      </div>
    </div>

    <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
      <div class="modal">
        <h3>添加新用户</h3>
        <div class="modal-body">
          <div class="form-group">
            <label>用户名</label>
            <input type="text" v-model="newUsername" placeholder="请输入用户名（3-50字符）">
          </div>
          <div class="form-group">
            <label>密码</label>
            <div class="password-input">
              <input :type="showCreatePassword ? 'text' : 'password'" v-model="newUserPassword" placeholder="请输入密码（注册时无要求，首次登录后需修改）">
              <button class="toggle-password" @click="showCreatePassword = !showCreatePassword">
                {{ showCreatePassword ? '👁️' : '👁️‍🗨️' }}
              </button>
            </div>
          </div>
          <div class="form-group">
            <label>用户类型</label>
            <div class="user-type-selector">
              <label class="radio-option">
                <input type="radio" v-model="newUserType" value="super">
                <span class="radio-label">
                  <span class="role-badge super">超级用户</span>
                </span>
              </label>
              <label class="radio-option">
                <input type="radio" v-model="newUserType" value="common">
                <span class="radio-label">
                  <span class="role-badge common">普通用户</span>
                </span>
              </label>
            </div>
          </div>
          <p class="hint-text">部门信息会在用户绑定人员后自动同步，无需在这里单独填写。</p>
        </div>
        <div class="modal-footer">
          <button class="btn-secondary" @click="showCreateModal = false">取消</button>
          <button class="btn-primary" @click="submitCreateUser">确认添加</button>
        </div>
      </div>
    </div>

    <div v-if="showResetPasswordModal" class="modal-overlay" @click.self="showResetPasswordModal = false">
      <div class="modal">
        <h3>密码已重置 - {{ selectedUser?.username }}</h3>
        <div class="modal-body">
          <div class="password-display">
            <label>临时密码：</label>
            <span class="password-value">{{ resetPasswordValue }}</span>
          </div>
          <p class="hint-text">请尽快通知用户登录后在个人中心修改密码。</p>
        </div>
        <div class="modal-footer">
          <button class="btn-secondary" @click="showResetPasswordModal = false">关闭</button>
          <button class="btn-primary" @click="copyResetPassword">复制密码</button>
        </div>
      </div>
    </div>

    <div v-if="showPersonnelModal" class="modal-overlay" @click.self="showPersonnelModal = false">
      <div class="modal modal-wide">
        <h3>设置人员 - {{ selectedUser?.username }}</h3>
        <div class="modal-body">
          <p class="hint-text">
            当前人员：<strong>{{ selectedUser?.personnel_display || getPersonnelDisplay(selectedUser) }}</strong>
          </p>
          <p class="hint-text">
            已选人员：<strong>{{ selectedPersonnelSummary || '未选择' }}</strong>
          </p>
          <PersonnelLookupSelect
            v-model="selectedPersonnelId"
            :initial-options="personnelLookupOptions"
            :disabled="false"
            @select="handlePersonnelSelected"
          />
        </div>
        <div class="modal-footer">
          <button class="btn-secondary" @click="showPersonnelModal = false">取消</button>
          <button class="btn-danger" @click="submitPersonnelUnbind">解绑</button>
          <button class="btn-primary" @click="submitPersonnelBinding">保存绑定</button>
        </div>
      </div>
    </div>

    <div v-if="showBatchTypeModal" class="modal-overlay" @click.self="showBatchTypeModal = false">
      <div class="modal">
        <h3>批量修改用户类型</h3>
        <div class="modal-body">
          <p>已选择 {{ selectedUserIds.length }} 个用户。</p>
          <div class="form-group">
            <label>目标用户类型</label>
            <div class="user-type-selector">
              <label class="radio-option">
                <input type="radio" v-model="batchTargetType" value="super">
                <span class="radio-label">
                  <span class="role-badge super">超级用户</span>
                </span>
              </label>
              <label class="radio-option">
                <input type="radio" v-model="batchTargetType" value="common">
                <span class="radio-label">
                  <span class="role-badge common">普通用户</span>
                </span>
              </label>
            </div>
          </div>
          <p class="hint-text">管理员账号会在后端校验时自动失败并出现在结果明细中。</p>
        </div>
        <div class="modal-footer">
          <button class="btn-secondary" @click="showBatchTypeModal = false">取消</button>
          <button class="btn-primary" @click="submitBatchTypeChange">确认修改</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.admin-container { min-height: 100vh; background: #f3f4f6; }
.admin-header { background: white; padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.header-left { display: flex; align-items: center; gap: 20px; }
.header-left h1 { font-size: 20px; color: #1f2937; margin: 0; }
.user-info { color: #6b7280; font-size: 14px; }
.header-actions { display: flex; align-items: center; gap: 12px; }
.admin-tabs { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.admin-tab-btn { background: #f3f4f6; color: #374151; text-decoration: none; border: 1px solid #d1d5db; padding: 8px 14px; border-radius: 999px; font-size: 14px; cursor: pointer; transition: all 0.2s; }
.admin-tab-btn:hover { background: #e5e7eb; }
.admin-tab-btn.active { background: #1f2937; border-color: #1f2937; color: white; }
.profile-tab-btn { background: #eef2ff; border-color: #c7d2fe; color: #4338ca; }
.profile-tab-btn:hover { background: #e0e7ff; }
.logout-btn { background: #f3f4f6; border: 1px solid #d1d5db; padding: 8px 16px; border-radius: 6px; cursor: pointer; }
.admin-main { padding: 24px; max-width: 1200px; margin: 0 auto; }
.alert { padding: 12px 16px; border-radius: 8px; margin-bottom: 20px; }
.alert-success { background: #dcfce7; color: #166534; }
.alert-error { background: #fef2f2; color: #dc2626; }
.quota-management-shell { margin-bottom: 24px; }
.user-section { background: white; border-radius: 12px; padding: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.section-header h2 { font-size: 18px; color: #1f2937; margin: 0; }
.section-header .header-actions { display: flex; gap: 12px; }
.selection-summary { margin-bottom: 12px; color: #374151; font-size: 14px; }
.add-user-btn { background: #667eea; color: white; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-size: 14px; }
.add-user-btn:hover { background: #5a67d8; }
.add-user-btn.batch-import-btn { background: #10b981; }
.add-user-btn.batch-import-btn:hover { background: #059669; }
.user-count { color: #6b7280; font-size: 14px; }
.loading { text-align: center; padding: 40px; color: #6b7280; }
.user-table { width: 100%; border-collapse: collapse; }
.user-table th, .user-table td { padding: 12px; text-align: left; border-bottom: 1px solid #e5e7eb; }
.user-table .checkbox-col { width: 48px; text-align: center; }
.user-table th { background: #f9fafb; font-weight: 500; color: #374151; font-size: 14px; }
.user-table td { color: #1f2937; font-size: 14px; }
.role-badge { padding: 4px 8px; border-radius: 4px; font-size: 12px; }
.role-badge.admin { background: #dbeafe; color: #1d4ed8; }
.role-badge.super { background: #fef3c7; color: #92400e; }
.role-badge.common { background: #dcfce7; color: #166534; }
.role-badge.user { background: #f3f4f6; color: #6b7280; }
.status-badge { padding: 4px 8px; border-radius: 4px; font-size: 12px; }
.status-badge.active { background: #dcfce7; color: #166534; }
.status-badge.disabled { background: #fee2e2; color: #dc2626; }
.department-disabled { color: #b45309; font-weight: 600; }
.actions { display: flex; gap: 8px; }
.action-btn { padding: 6px 12px; border-radius: 6px; font-size: 12px; cursor: pointer; border: 1px solid #d1d5db; background: white; }
.action-btn:hover { background: #f9fafb; }
.action-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.batch-action-btn { background: #eff6ff; border-color: #bfdbfe; color: #1d4ed8; }
.action-btn.btn-success { background: #dcfce7; border-color: #86efac; color: #166534; }
.action-btn.btn-danger { background: #fee2e2; border-color: #fca5a5; color: #dc2626; }
.model-status-shell { background: white; border-radius: 8px; padding: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.model-status-note { margin: 6px 0 0; color: #6b7280; font-size: 13px; }
.model-status-panel { display: flex; flex-direction: column; gap: 14px; }
.model-status-summary { display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 10px; }
.model-status-summary span { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; color: #4b5563; font-size: 13px; padding: 10px 12px; }
.model-status-summary strong { display: block; color: #111827; font-size: 18px; line-height: 1.2; margin-bottom: 2px; }
.model-status-empty { border: 1px dashed #d1d5db; border-radius: 8px; color: #6b7280; padding: 28px; text-align: center; }
.model-endpoint-list { display: flex; flex-direction: column; gap: 12px; }
.model-endpoint-card { display: grid; grid-template-columns: minmax(0, 1fr) 220px; gap: 18px; border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; background: #fff; }
.model-endpoint-card:hover { border-color: #cbd5e1; box-shadow: 0 1px 2px rgba(15,23,42,0.06); }
.model-card-main { min-width: 0; }
.model-card-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 12px; }
.model-name-cell { min-width: 0; }
.model-name-cell h3 { margin: 0; color: #111827; font-size: 15px; line-height: 1.35; }
.model-card-meta { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 6px; color: #6b7280; font-size: 12px; }
.model-card-meta span { background: #f3f4f6; border-radius: 999px; padding: 3px 8px; max-width: 100%; overflow-wrap: anywhere; }
.model-route-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.model-route-item { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.model-route-item span { color: #6b7280; font-size: 12px; }
.model-route-item code { display: block; min-height: 42px; border: 1px solid #e5e7eb; border-radius: 6px; background: #f8fafc; color: #111827; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; font-size: 12px; line-height: 1.45; padding: 8px 10px; overflow-wrap: anywhere; white-space: normal; }
.model-card-side { display: flex; flex-direction: column; justify-content: space-between; gap: 14px; border-left: 1px solid #e5e7eb; padding-left: 18px; min-width: 0; }
.model-auth-cell, .model-test-cell { display: flex; flex-direction: column; gap: 6px; min-width: 0; color: #374151; font-size: 13px; }
.model-auth-cell small { color: #6b7280; font-size: 11px; overflow-wrap: anywhere; }
.model-status-badge { display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto; min-width: 56px; border-radius: 999px; padding: 4px 8px; font-size: 12px; font-weight: 600; }
.model-status-configured { background: #dcfce7; color: #166534; }
.model-status-unconfigured { background: #fef3c7; color: #92400e; }
.model-status-disabled { background: #f3f4f6; color: #6b7280; }
.model-test-cell .action-btn { align-self: flex-start; min-width: 72px; }
.model-test-result { font-size: 12px; line-height: 1.4; overflow-wrap: anywhere; }
.model-test-idle { color: #6b7280; }
.model-test-pending { color: #1d4ed8; }
.model-test-ok { color: #166534; font-weight: 600; }
.model-test-failed { color: #dc2626; font-weight: 600; }
.model-status-checked { margin: 12px 0 0; color: #6b7280; font-size: 12px; }
.pagination { display: flex; justify-content: center; align-items: center; gap: 16px; margin-top: 20px; }
.pagination button { padding: 8px 16px; border: 1px solid #d1d5db; background: white; border-radius: 6px; cursor: pointer; }
.pagination button:disabled { opacity: 0.5; cursor: not-allowed; }
.modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; }
.modal { background: white; border-radius: 12px; padding: 24px; width: 100%; max-width: 400px; }
.modal.modal-wide { max-width: 680px; }
.modal h3 { font-size: 18px; color: #1f2937; margin: 0 0 16px 0; }
.modal-body { margin-bottom: 24px; }
.modal-body .form-group { display: flex; flex-direction: column; gap: 8px; }
.modal-body .form-group label { font-size: 14px; color: #374151; }
.modal-body .form-group input { padding: 10px 12px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; }
.modal-body .password-input { display: flex; gap: 8px; }
.modal-body .password-input input { flex: 1; }
.modal-body .toggle-password { background: none; border: none; padding: 8px; cursor: pointer; font-size: 16px; }
.modal-body .user-type-selector { display: flex; gap: 12px; }
.modal-body .radio-option { display: flex; align-items: center; gap: 8px; cursor: pointer; padding: 8px 12px; border: 2px solid #e5e7eb; border-radius: 8px; transition: all 0.2s; }
.modal-body .radio-option:hover { border-color: #d1d5db; background: #f9fafb; }
.modal-body .radio-option input[type="radio"] { cursor: pointer; }
.modal-body .radio-option input[type="radio"]:checked + .radio-label { font-weight: 500; }
.modal-body .radio-option:has(input:checked) { border-color: #667eea; background: #eef2ff; }
.modal-body .radio-label { display: flex; align-items: center; }
.modal-body .warning { color: #dc2626; font-size: 14px; }
.modal-body .password-display { background: #f3f4f6; padding: 16px; border-radius: 8px; font-size: 16px; color: #1f2937; }
.modal-body .password-display label { font-weight: 500; margin-right: 8px; }
.modal-body .password-value { font-family: monospace; letter-spacing: 1px; }
.modal-body .hint-text { margin-top: 10px; color: #6b7280; font-size: 13px; }
.modal-footer { display: flex; justify-content: flex-end; gap: 12px; }
.btn-primary, .btn-secondary, .btn-danger { padding: 10px 20px; border-radius: 6px; font-size: 14px; cursor: pointer; border: none; }
.btn-primary { background: #667eea; color: white; }
.btn-secondary { background: #f3f4f6; color: #374151; border: 1px solid #d1d5db; }
.btn-danger { background: #dc2626; color: white; }
@media (max-width: 900px) {
  .admin-header { flex-direction: column; align-items: stretch; gap: 16px; }
  .header-actions { justify-content: space-between; flex-wrap: wrap; }
  .model-status-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .model-endpoint-card { grid-template-columns: 1fr; }
  .model-card-side { border-left: none; border-top: 1px solid #e5e7eb; padding-left: 0; padding-top: 14px; }
  .model-route-grid { grid-template-columns: 1fr; }
}
</style>
