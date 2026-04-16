<script setup>
import { computed, ref, onMounted } from 'vue'
import DepartmentSelector from '../components/DepartmentSelector.vue'
import { authApi, clearStoredAuth, persistStoredUser, readStoredUser } from '../services/auth'
import { departmentApi, mergePreservedDepartmentTree } from '../services/departments'
import { quotaApi } from '../services/quota'

const currentUser = ref(null)
const loading = ref(false)
const error = ref('')
const success = ref('')
const departmentError = ref('')
const departmentSuccess = ref('')
const forcePasswordChange = ref(false)
const forceSecurityQuestionSetup = ref(false)
const forceDepartmentSetup = ref(false)

// 配额信息
const quotas = ref(null)
const quotaLoading = ref(false)

// 修改密码表单
const showPasswordForm = ref(false)
const oldPassword = ref('')
const newPassword = ref('')

// 部门信息
const showDepartmentForm = ref(false)
const departmentTree = ref([])
const departmentLoading = ref(false)
const selectedPrimaryDepartmentId = ref(null)
const selectedSecondaryDepartmentId = ref(null)
const departmentSelectorTree = computed(() => (
  mergePreservedDepartmentTree(departmentTree.value, currentUser.value)
))

// 安全问题表单
const showSecurityForm = ref(false)
const securityQuestions = ref([])
const securityAnswers = ref([])
const presetQuestions = [
  "我最喜欢的水果是什么？",
  "我出生在哪个城市？",
  "我最喜欢的一本书是什么？",
  "我的小学名称是什么？",
  "我最喜欢的电影是什么？",
  "我最喜欢的一句话是什么？",
  "我的偶像是谁？",
  "我最喜欢的一种运动是什么？"
]

// 获取角色显示名称
function getRoleText(user) {
  if (!user) return '未知'
  const userType = user.user_type
  
  // user_type = 1: 管理员
  if (userType === 1 || user.role === 'admin') {
    return '管理员'
  }
  
  // user_type = 2: 超级用户
  if (userType === 2 || user.role === 'super') {
    return '超级用户'
  }
  
  // user_type = 3 或其他: 普通用户
  return '普通用户'
}

// 获取角色样式类名
function getRoleClass(user) {
  if (!user) return 'user'
  const userType = user.user_type
  
  // user_type = 1: 管理员
  if (userType === 1 || user.role === 'admin') {
    return 'admin'
  }
  
  // user_type = 2: 超级用户
  if (userType === 2 || user.role === 'super') {
    return 'super'
  }
  
  // user_type = 3 或其他: 普通用户
  return 'user'
}

async function fetchCurrentUser() {
  loading.value = true
  try {
    const result = await authApi.getMe()
    if (result.success) {
      currentUser.value = result.data
      forcePasswordChange.value = Boolean(result.data?.is_first_login)
      forceSecurityQuestionSetup.value = Boolean(result.data?.require_security_questions_setup)
      forceDepartmentSetup.value = Boolean(result.data?.require_department_setup)
      selectedPrimaryDepartmentId.value = result.data?.primary_department_id ?? null
      selectedSecondaryDepartmentId.value = result.data?.secondary_department_id ?? null
      if (forcePasswordChange.value) {
        showPasswordForm.value = true
      }
      if (forceSecurityQuestionSetup.value) {
        showSecurityForm.value = true
      }
      if (forceDepartmentSetup.value) {
        showDepartmentForm.value = true
      }
      // 获取已设置的安全问题
      await Promise.all([
        fetchSecurityQuestions(),
        fetchDepartmentTree(),
      ])
    } else {
      error.value = result.error || '获取用户信息失败'
    }
  } catch (e) {
    error.value = '获取用户信息失败'
  } finally {
    loading.value = false
  }
}

async function fetchDepartmentTree() {
  departmentLoading.value = true
  departmentError.value = ''
  try {
    const result = await departmentApi.getSelectableTree()
    if (result.success) {
      departmentTree.value = Array.isArray(result.data?.items) ? result.data.items : []
      return
    }
    departmentTree.value = []
    departmentError.value = result.error || '获取部门选项失败'
  } catch (e) {
    departmentTree.value = []
    departmentError.value = '获取部门选项失败'
  } finally {
    departmentLoading.value = false
  }
}

function syncStoredUser(patch) {
  const latestUser = {
    ...(readStoredUser() || {}),
    ...(currentUser.value || {}),
    ...patch,
  }
  persistStoredUser(latestUser)
}

function hasPendingForcedSetup() {
  return forcePasswordChange.value || forceSecurityQuestionSetup.value || forceDepartmentSetup.value
}

function redirectAfterProfileCompletion() {
  window.location.href = currentUser.value?.role === 'admin' ? '/admin' : '/'
}

async function fetchSecurityQuestions() {
  const result = await authApi.getSecurityQuestions()
  if (result.success && result.data.questions) {
    securityQuestions.value = result.data.questions
    // 如果有已设置的问题，初始化答案数组
    if (securityQuestions.value.length > 0) {
      securityAnswers.value = securityQuestions.value.map(() => '')
    }
  }
}

async function submitPasswordChange() {
  error.value = ''
  success.value = ''
  
  if (!oldPassword.value || !newPassword.value) {
    error.value = '请填写所有字段'
    return
  }
  
  const role = currentUser.value?.role || 'user'
  if (role === 'admin') {
    if (newPassword.value.length < 12) {
      error.value = '管理员密码长度不能少于12位'
      return
    }
    if (!/[a-z]/.test(newPassword.value)) {
      error.value = '管理员密码必须包含小写字母'
      return
    }
    if (!/[A-Z]/.test(newPassword.value)) {
      error.value = '管理员密码必须包含大写字母'
      return
    }
    if (!/[0-9]/.test(newPassword.value)) {
      error.value = '管理员密码必须包含数字'
      return
    }
    if (!/[^A-Za-z0-9]/.test(newPassword.value)) {
      error.value = '管理员密码必须包含英文符号'
      return
    }
  } else {
    if (newPassword.value.length < 8) {
      error.value = '新密码长度不能少于8位'
      return
    }
    let categoryCount = 0
    categoryCount += /[a-z]/.test(newPassword.value) ? 1 : 0
    categoryCount += /[A-Z]/.test(newPassword.value) ? 1 : 0
    categoryCount += /[0-9]/.test(newPassword.value) ? 1 : 0
    categoryCount += /[^A-Za-z0-9]/.test(newPassword.value) ? 1 : 0
    if (categoryCount < 3) {
      error.value = '密码必须包含数字、小写字母、大写字母、特殊符号中的至少3类'
      return
    }
  }
  
  const result = await authApi.changePassword(oldPassword.value, newPassword.value)
  
  if (result.success) {
    success.value = '密码修改成功'
    showPasswordForm.value = false
    oldPassword.value = ''
    newPassword.value = ''
    forcePasswordChange.value = false
    if (currentUser.value) {
      currentUser.value.is_first_login = false
    }
    syncStoredUser({
      is_first_login: false,
      require_security_questions_setup: forceSecurityQuestionSetup.value,
      require_department_setup: forceDepartmentSetup.value,
    })
    
    setTimeout(() => {
      success.value = ''
      // 若仍需设置安全问题，停留在本页并展开安全问题表单
      if (forceSecurityQuestionSetup.value) {
        showSecurityForm.value = true
        return
      }
      if (forceDepartmentSetup.value) {
        showDepartmentForm.value = true
        return
      }
      if (!hasPendingForcedSetup()) {
        redirectAfterProfileCompletion()
      }
    }, 2000)
  } else {
    error.value = result.error
  }
}

// 添加一个问题
function addQuestion() {
  if (securityQuestions.value.length < 3) {
    securityQuestions.value.push('')
    securityAnswers.value.push('')
  }
}

// 移除一个问题
function removeQuestion(index) {
  securityQuestions.value.splice(index, 1)
  securityAnswers.value.splice(index, 1)
}

// 保存安全问题
async function saveSecurityQuestions() {
  error.value = ''
  success.value = ''
  
  // 验证
  for (let i = 0; i < securityQuestions.value.length; i++) {
    if (!securityQuestions.value[i]) {
      error.value = `请选择或输入第${i + 1}个问题`
      return
    }
    if (!securityAnswers.value[i] || !securityAnswers.value[i].trim()) {
      error.value = `请输入第${i + 1}个问题的答案`
      return
    }
  }
  
  const questions = securityQuestions.value.map((q, i) => ({
    question: q,
    answer: securityAnswers.value[i].trim()
  }))
  
  const result = await authApi.setSecurityQuestions(questions)
  
  if (result.success) {
    success.value = '安全问题设置成功'
    showSecurityForm.value = false
    if (forceSecurityQuestionSetup.value) {
      forceSecurityQuestionSetup.value = false
      if (currentUser.value) {
        currentUser.value.require_security_questions_setup = false
        currentUser.value.has_security_questions = true
      }
      syncStoredUser({
        require_security_questions_setup: false,
        has_security_questions: true,
        require_department_setup: forceDepartmentSetup.value,
      })
      setTimeout(() => {
        success.value = ''
        if (forceDepartmentSetup.value) {
          showDepartmentForm.value = true
          return
        }
        if (!hasPendingForcedSetup()) {
          redirectAfterProfileCompletion()
        }
      }, 1500)
      return
    }
    setTimeout(() => success.value = '', 3000)
  } else {
    error.value = result.error
  }
}

async function saveDepartment() {
  departmentError.value = ''
  departmentSuccess.value = ''

  if (!selectedPrimaryDepartmentId.value || !selectedSecondaryDepartmentId.value) {
    departmentError.value = '请选择一级和二级部门'
    return
  }

  const result = await departmentApi.updateMyDepartment(
    selectedPrimaryDepartmentId.value,
    selectedSecondaryDepartmentId.value,
  )

  if (result.success) {
    currentUser.value = {
      ...(currentUser.value || {}),
      ...(result.data || {}),
    }
    selectedPrimaryDepartmentId.value = result.data?.primary_department_id ?? null
    selectedSecondaryDepartmentId.value = result.data?.secondary_department_id ?? null
    forceDepartmentSetup.value = Boolean(result.data?.require_department_setup)
    showDepartmentForm.value = false
    syncStoredUser({
      ...(result.data || {}),
      require_department_setup: Boolean(result.data?.require_department_setup),
    })
    departmentSuccess.value = '部门信息保存成功'
    setTimeout(() => {
      departmentSuccess.value = ''
      if (!hasPendingForcedSetup()) {
        redirectAfterProfileCompletion()
      }
    }, 1500)
    return
  }

  departmentError.value = result.error || '保存部门信息失败'
}

async function logout() {
  clearStoredAuth()
  window.location.href = '/login'
}

// 获取配额信息
// 检查是否需要强制修改密码
function checkForcePasswordChange() {
  const urlParams = new URLSearchParams(window.location.search)
  if (urlParams.get('change_password') === 'required') {
    forcePasswordChange.value = true
    showPasswordForm.value = true
  }
  if (urlParams.get('security_questions') === 'required') {
    forceSecurityQuestionSetup.value = true
    showSecurityForm.value = true
  }
  if (urlParams.get('department') === 'required') {
    forceDepartmentSetup.value = true
    showDepartmentForm.value = true
  }
}

async function fetchQuotas() {
  quotaLoading.value = true
  const result = await quotaApi.getMyQuotas()
  if (result.success) {
    quotas.value = result.data
  }
  quotaLoading.value = false
}

function getQuotaWindows(quota) {
  const windows = Array.isArray(quota?.windows) ? quota.windows : []
  if (windows.length > 0) return windows
  return [
    {
      period: quota?.period || 'none',
      period_days: Number(quota?.period_days || 0),
      current: Number(quota?.current || 0),
      limit: Number(quota?.limit || 0),
      remaining: Number(quota?.remaining || 0),
      reset_time: quota?.reset_time || '未知',
    },
  ]
}

// 获取配额使用百分比
function getQuotaPercentage(windowItem) {
  const limit = Number(windowItem?.limit || 0)
  const current = Number(windowItem?.current || 0)
  if (limit <= 0) return 0
  return Math.min(100, Math.round((current / limit) * 100))
}

// 获取配额状态类
function getQuotaStatusClass(windowItem) {
  const percentage = getQuotaPercentage(windowItem)
  if (percentage >= 100) return 'quota-full'
  if (percentage >= 80) return 'quota-warning'
  return 'quota-normal'
}

function getQuotaCardStatusClass(quota) {
  const windows = getQuotaWindows(quota)
  if (windows.some((item) => getQuotaStatusClass(item) === 'quota-full')) return 'quota-full'
  if (windows.some((item) => getQuotaStatusClass(item) === 'quota-warning')) return 'quota-warning'
  return 'quota-normal'
}

// 获取周期文本
function getPeriodText(period, periodDays = 0) {
  if (period === 'custom_days') {
    const days = Number(periodDays || 0)
    return days > 0 ? `每${days}天` : '自定义'
  }
  const periodMap = {
    'daily': '每日',
    'weekly': '每周',
    'monthly': '每月',
    'none': '无限制'
  }
  return periodMap[period] || period
}

onMounted(() => {
  checkForcePasswordChange()
  fetchCurrentUser()
  fetchQuotas()
})
</script>

<template>
  <div class="profile-container">
    <header class="profile-header">
      <div class="header-left">
        <a href="/" class="back-link">← 返回对话</a>
        <h1>个人中心</h1>
      </div>
      <button class="logout-btn" @click="logout">退出登录</button>
    </header>

    <main class="profile-main">
      <div v-if="loading" class="loading">加载中...</div>
      
      <template v-else-if="currentUser">
        <!-- 用户信息卡片 -->
        <div class="info-card">
          <h2>基本信息</h2>
          <div class="info-row">
            <span class="label">用户名</span>
            <span class="value">{{ currentUser.username }}</span>
          </div>
          <div class="info-row">
            <span class="label">角色</span>
            <span class="value role-badge" :class="getRoleClass(currentUser)">
              {{ getRoleText(currentUser) }}
            </span>
          </div>
          <div class="info-row">
            <span class="label">状态</span>
            <span class="value status-badge" :class="currentUser.status">
              {{ currentUser.status === 'active' ? '正常' : '已停用' }}
            </span>
          </div>
          <div class="info-row">
            <span class="label">创建时间</span>
            <span class="value">{{ currentUser.created_at }}</span>
          </div>
        </div>

        <!-- 部门信息 -->
        <div class="action-card">
          <h2>部门信息</h2>
          <p class="hint">请选择您的一级、二级部门。未填写部门信息时会被强制拦截到个人中心补全。</p>

          <div v-if="forceDepartmentSetup" class="alert alert-warning">
            <strong>⚠️ 部门信息必填</strong><br>
            请先补全部门信息后再继续使用系统。
          </div>

          <div v-if="departmentSuccess" class="alert alert-success">{{ departmentSuccess }}</div>
          <div v-if="departmentError" class="alert alert-error">{{ departmentError }}</div>

          <div v-if="!showDepartmentForm" class="department-summary">
            <div class="info-row">
              <span class="label">当前部门</span>
              <span
                class="value"
                :class="{ 'department-disabled': currentUser.department_effective_status === 'disabled' }"
              >
                {{ currentUser.department_display || '未填写' }}
              </span>
            </div>
            <button class="action-btn" @click="showDepartmentForm = true">
              {{ currentUser.primary_department_id && currentUser.secondary_department_id ? '修改部门' : '填写部门' }}
            </button>
          </div>

          <div v-else class="department-form">
            <div v-if="departmentLoading" class="loading-small">加载部门选项中...</div>
            <template v-else>
              <DepartmentSelector
                :tree="departmentSelectorTree"
                :primary-id="selectedPrimaryDepartmentId"
                :secondary-id="selectedSecondaryDepartmentId"
                :allow-empty="false"
                @update:primary-id="selectedPrimaryDepartmentId = $event"
                @update:secondary-id="selectedSecondaryDepartmentId = $event"
              />
              <div class="form-actions">
                <button class="btn-secondary" @click="showDepartmentForm = false" :disabled="forceDepartmentSetup">取消</button>
                <button class="btn-primary" @click="saveDepartment" :disabled="departmentLoading || !departmentTree.length">保存部门</button>
              </div>
            </template>
          </div>
        </div>

        <!-- 配额信息卡片 -->
        <div class="info-card quota-card" v-if="currentUser.user_type === 3">
          <h2>配额使用情况</h2>
          
          <div v-if="quotaLoading" class="loading-small">加载中...</div>
          
          <div v-else-if="quotas" class="quota-list">
            <div 
              v-for="(quota, type) in quotas" 
              :key="type"
              class="quota-item"
              :class="getQuotaCardStatusClass(quota)"
            >
              <div class="quota-header">
                <span class="quota-name">{{ quota.name }}</span>
              </div>

              <div class="quota-window-list">
                <div
                  v-for="windowItem in getQuotaWindows(quota)"
                  :key="`${type}-${windowItem.period}-${windowItem.period_days}`"
                  class="quota-window"
                  :class="getQuotaStatusClass(windowItem)"
                >
                  <div class="quota-window-header">
                    <span class="quota-period">{{ getPeriodText(windowItem.period, windowItem.period_days) }}</span>
                    <span class="quota-count">{{ windowItem.current }}/{{ windowItem.limit }}</span>
                  </div>

                  <div class="quota-bar">
                    <div 
                      class="quota-progress" 
                      :style="{ width: getQuotaPercentage(windowItem) + '%' }"
                    ></div>
                  </div>

                  <div class="quota-footer">
                    <span class="quota-remaining">剩余 {{ windowItem.remaining }}</span>
                    <span class="quota-reset">{{ windowItem.reset_time }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
          
          <div v-else class="no-data">暂无配额信息</div>
        </div>

        <!-- 修改密码 -->
        <div class="action-card">
          <h2>修改密码</h2>
          
          <div v-if="forcePasswordChange" class="alert alert-warning">
            <strong>⚠️ 首次登录提醒</strong><br>
            为了您的账号安全，请立即修改密码后才能正常使用系统。
          </div>
          <div v-if="forceSecurityQuestionSetup" class="alert alert-warning">
            <strong>⚠️ 安全问题必填</strong><br>
            请至少设置一个安全问题后再继续使用系统。
          </div>
          
          <div v-if="success" class="alert alert-success">{{ success }}</div>
          <div v-if="error" class="alert alert-error">{{ error }}</div>
          
          <div v-if="!showPasswordForm">
            <button class="action-btn" @click="showPasswordForm = true">
              修改密码
            </button>
          </div>
          
          <div v-else class="password-form">
            <div class="form-group">
              <label>旧密码</label>
              <input type="password" v-model="oldPassword" placeholder="请输入旧密码">
            </div>
            <div class="form-group">
              <label>新密码</label>
              <input 
                type="password" 
                v-model="newPassword" 
                :placeholder="currentUser?.role === 'admin' 
                  ? '请输入新密码（12位以上，包含大小写字母、数字、特殊符号）' 
                  : '请输入新密码（8位以上，数字/小写/大写/符号至少3类）'"
              >
            </div>
            <div class="form-actions">
              <button class="btn-secondary" @click="showPasswordForm = false" :disabled="forcePasswordChange">取消</button>
              <button class="btn-primary" @click="submitPasswordChange">确认修改</button>
            </div>
          </div>
        </div>

        <!-- 安全问题设置 -->
        <div class="action-card">
          <h2>安全问题设置</h2>
          <p class="hint">设置安全问题后，可以通过回答问题找回密码（最多设置3个问题）</p>
          <div v-if="forceSecurityQuestionSetup" class="alert alert-warning">
            首次登录流程要求：至少设置 1 个安全问题后才能进入系统。
          </div>
          
          <div v-if="success" class="alert alert-success">{{ success }}</div>
          <div v-if="error" class="alert alert-error">{{ error }}</div>
          
          <div v-if="!showSecurityForm">
            <div class="security-status">
              <span v-if="securityQuestions.length > 0" class="has-questions">
                已设置 {{ securityQuestions.length }} 个安全问题
              </span>
              <span v-else class="no-questions">
                尚未设置安全问题
              </span>
            </div>
            <button class="action-btn" @click="showSecurityForm = true">
              {{ securityQuestions.length > 0 ? '修改安全问题' : '设置安全问题' }}
            </button>
          </div>
          
          <div v-else class="security-form">
            <div 
              v-for="(question, index) in securityQuestions" 
              :key="index" 
              class="question-item"
            >
              <div class="question-header">
                <span class="question-number">问题 {{ index + 1 }}</span>
                <button 
                  v-if="securityQuestions.length > 1" 
                  class="remove-btn"
                  @click="removeQuestion(index)"
                >
                  移除
                </button>
              </div>
              <select v-model="securityQuestions[index]">
                <option value="">请选择问题</option>
                <option 
                  v-for="pq in presetQuestions" 
                  :key="pq" 
                  :value="pq"
                >
                  {{ pq }}
                </option>
              </select>
              <input 
                type="text" 
                v-model="securityAnswers[index]" 
                placeholder="请输入答案"
              >
            </div>
            
            <button 
              v-if="securityQuestions.length < 3" 
              class="add-btn" 
              @click="addQuestion"
            >
              + 添加问题
            </button>
            
            <div class="form-actions">
              <button class="btn-secondary" @click="showSecurityForm = false" :disabled="forceSecurityQuestionSetup">取消</button>
              <button class="btn-primary" @click="saveSecurityQuestions">保存</button>
            </div>
          </div>
        </div>
      </template>
    </main>
  </div>
</template>

<style scoped>
.profile-container {
  min-height: 100vh;
  background: #f3f4f6;
  overflow-y: auto;
}

.profile-header {
  background: white;
  padding: 16px 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 20px;
}

.back-link {
  color: #667eea;
  text-decoration: none;
  font-size: 14px;
}

.back-link:hover {
  text-decoration: underline;
}

.header-left h1 {
  font-size: 20px;
  color: #1f2937;
  margin: 0;
}

.logout-btn {
  background: #f3f4f6;
  border: 1px solid #d1d5db;
  padding: 8px 16px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
}

.profile-main {
  padding: 24px;
  max-width: 600px;
  margin: 0 auto;
}

.loading {
  text-align: center;
  padding: 40px;
  color: #6b7280;
}

.info-card, .action-card {
  background: white;
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.info-card h2, .action-card h2 {
  font-size: 16px;
  color: #1f2937;
  margin: 0 0 8px 0;
  padding-bottom: 12px;
  border-bottom: 1px solid #e5e7eb;
}

.hint {
  color: #666;
  font-size: 14px;
  margin-bottom: 15px;
}

.department-summary,
.department-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.department-disabled {
  color: #b45309;
  font-weight: 600;
}

/* 配额卡片样式 */
.quota-card {
  margin-top: 20px;
}

.loading-small {
  text-align: center;
  color: #666;
  padding: 20px;
}

.quota-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  margin-top: 16px;
}

.quota-item {
  padding: 16px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fafafa;
  transition: all 0.3s;
}

.quota-item:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.quota-item.quota-warning {
  border-color: #fbbf24;
  background: #fffbeb;
}

.quota-item.quota-full {
  border-color: #ef4444;
  background: #fef2f2;
}

.quota-header {
  margin-bottom: 10px;
}

.quota-name {
  font-weight: 600;
  font-size: 15px;
  color: #1f2937;
}

.quota-window-list {
  display: grid;
  gap: 10px;
}

.quota-window {
  padding: 10px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
}

.quota-window.quota-warning {
  border-color: #fbbf24;
  background: #fffbeb;
}

.quota-window.quota-full {
  border-color: #ef4444;
  background: #fef2f2;
}

.quota-window-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.quota-count {
  font-size: 14px;
  color: #6b7280;
  font-weight: 500;
}

.quota-window.quota-warning .quota-count {
  color: #d97706;
}

.quota-window.quota-full .quota-count {
  color: #dc2626;
}

.quota-bar {
  height: 8px;
  background: #e5e7eb;
  border-radius: 4px;
  overflow: hidden;
  margin-bottom: 8px;
}

.quota-progress {
  height: 100%;
  background: #10b981;
  transition: width 0.3s ease;
  border-radius: 4px;
}

.quota-window.quota-warning .quota-progress {
  background: #fbbf24;
}

.quota-window.quota-full .quota-progress {
  background: #ef4444;
}

.quota-footer {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #9ca3af;
}

.quota-period {
  font-weight: 500;
}

.quota-remaining {
  font-weight: 500;
}

.quota-reset {
  font-style: italic;
}

.no-data {
  text-align: center;
  color: #9ca3af;
  padding: 40px;
  font-size: 14px;
}

.info-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 0;
  border-bottom: 1px solid #f3f4f6;
}

.info-row:last-child {
  border-bottom: none;
}

.label {
  color: #6b7280;
  font-size: 14px;
}

.value {
  color: #1f2937;
  font-size: 14px;
  font-weight: 500;
}

.role-badge {
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 12px;
}

.role-badge.admin {
  background: #dbeafe;
  color: #1d4ed8;
}

.role-badge.super {
  background: #fef3c7;
  color: #92400e;
}

.role-badge.user {
  background: #dcfce7;
  color: #166534;
}

.status-badge {
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 12px;
}

.status-badge.active {
  background: #dcfce7;
  color: #166534;
}

.status-badge.disabled {
  background: #fee2e2;
  color: #dc2626;
}

.action-btn {
  width: 100%;
  padding: 12px;
  background: #f3f4f6;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  color: #374151;
}

.action-btn:hover {
  background: #e5e7eb;
}

.password-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-group label {
  font-size: 14px;
  color: #374151;
}

.form-group input {
  padding: 10px 12px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 14px;
}

.form-group input:focus {
  outline: none;
  border-color: #667eea;
}

.form-actions {
  display: flex;
  gap: 12px;
  justify-content: flex-end;
  margin-top: 8px;
}

.btn-primary, .btn-secondary {
  padding: 10px 20px;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
  border: none;
}

.btn-primary {
  background: #667eea;
  color: white;
}

.btn-secondary {
  background: #f3f4f6;
  color: #374151;
  border: 1px solid #d1d5db;
}

.alert {
  padding: 12px 16px;
  border-radius: 8px;
  margin-bottom: 16px;
}

.alert-success {
  background: #dcfce7;
  color: #166534;
}

.alert-error {
  background: #fef2f2;
  color: #dc2626;
}

.alert-warning {
  background: #fffbeb;
  color: #92400e;
  border: 2px solid #fbbf24;
}

.security-status {
  padding: 12px;
  background: #f3f4f6;
  border-radius: 8px;
  margin-bottom: 16px;
  font-size: 14px;
}

.security-status .has-questions {
  color: #166534;
}

.security-status .no-questions {
  color: #dc2626;
}

.security-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.question-item {
  padding: 16px;
  background: #f9fafb;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.question-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.question-number {
  font-size: 14px;
  font-weight: 500;
  color: #374151;
}

.remove-btn {
  background: none;
  border: none;
  color: #dc2626;
  cursor: pointer;
  font-size: 13px;
}

.question-item select,
.question-item input {
  padding: 10px 12px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 14px;
}

.question-item select:focus,
.question-item input:focus {
  outline: none;
  border-color: #667eea;
}

.add-btn {
  background: none;
  border: 1px dashed #d1d5db;
  padding: 12px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  color: #667eea;
}

.add-btn:hover {
  background: #f9fafb;
}
</style>
