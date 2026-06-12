<script setup>
import { ref } from 'vue'
import { authApi, persistStoredUser } from '../services/auth'

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)
const showLoginPassword = ref(false)
const showPasswordWarning = ref(false)
const passwordWarningMessage = ref('')
const showAccountLocked = ref(false)
const lockedMessage = ref('')
const remainingSeconds = ref(0)
const showSetupWarning = ref(false)
const setupWarningTitle = ref('首次登录')
const setupWarningText = ref('为了您的账号安全，请立即修改密码')
const disabledPersonnel = ref(null)
const disabledDepartmentPersonnel = ref(null)

function normalizeDisabledPersonnel(result) {
  const personnel = result?.data?.personnel || {}
  return {
    employee_no: String(personnel.employee_no || '').trim(),
    full_name: String(personnel.full_name || '').trim(),
    department_display: String(personnel.department_display || '').trim(),
  }
}

async function handleLogin() {
  error.value = ''
  showPasswordWarning.value = false
  showAccountLocked.value = false
  showSetupWarning.value = false
  disabledPersonnel.value = null
  disabledDepartmentPersonnel.value = null
  
  if (!username.value || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  
  loading.value = true
  
  try {
    const result = await authApi.login(username.value, password.value)
    
    if (result.success) {
      // 保存token和用户信息（包含is_first_login）
      localStorage.setItem('token', result.data.token)
      localStorage.setItem('agentcode.auth.token.v1', result.data.token)
      
      // 确保用户信息包含is_first_login字段
      const userData = {
        ...result.data.user,
        is_first_login: result.data.is_first_login || false,
        personnel_id: result.data?.user?.personnel_id ?? null,
        employee_no: result.data?.user?.employee_no ?? null,
        full_name: result.data?.user?.full_name ?? null,
        personnel_binding_status: result.data?.user?.personnel_binding_status || 'unbound',
        require_security_questions_setup: Boolean(result.require_security_questions_setup),
        require_department_setup: Boolean(result.require_department_setup || result.data?.require_department_setup),
        require_personnel_setup: Boolean(
          result.require_personnel_setup
          || result.data?.require_personnel_setup
          || result.data?.user?.require_personnel_setup
        ),
        has_security_questions: Boolean(result.data?.has_security_questions),
      }
      persistStoredUser(userData)
      
      // 检查是否需要进入强制补全流程（改密码 + 安全问题 + 部门信息 + 人员信息）
      const requireDepartmentSetup = Boolean(result.require_department_setup || result.data?.require_department_setup)
      const requirePersonnelSetup = Boolean(
        result.require_personnel_setup
        || result.data?.require_personnel_setup
        || result.data?.user?.require_personnel_setup
      )
      if (result.require_password_change || result.require_security_questions_setup || requireDepartmentSetup || requirePersonnelSetup) {
        const hints = []
        if (result.require_password_change) hints.push('修改密码')
        if (result.require_security_questions_setup) hints.push('设置至少一个安全问题')
        if (requireDepartmentSetup) hints.push('补全部门信息')
        if (requirePersonnelSetup) hints.push('绑定人员信息')
        setupWarningTitle.value = (result.require_password_change || result.require_security_questions_setup) ? '首次登录' : '信息补全'
        setupWarningText.value = `为了继续使用系统，请立即${hints.join('并')}。`
        showSetupWarning.value = true
        setTimeout(() => {
          const params = new URLSearchParams()
          if (result.require_password_change) params.set('change_password', 'required')
          if (result.require_security_questions_setup) params.set('security_questions', 'required')
          if (requireDepartmentSetup) params.set('department', 'required')
          if (requirePersonnelSetup) params.set('personnel', 'required')
          const query = params.toString()
          window.location.href = query ? `/profile?${query}` : '/profile'
        }, 3000)
        return
      }
      
      // 检查是否有密码过期警告
      if (result.warning && result.warning.code === 'PASSWORD_EXPIRED') {
        showPasswordWarning.value = true
        passwordWarningMessage.value = result.warning.message
        
        // 3秒后跳转
        setTimeout(() => {
          redirectAfterLogin(result.data.user.role)
        }, 3000)
      } else {
        // 直接跳转
        redirectAfterLogin(result.data.user.role)
      }
    } else {
      // 处理登录失败
      handleLoginError(result)
    }
  } catch (e) {
    error.value = '登录失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

function handleLoginError(result) {
  // 账号锁定
  if (result.code === 'ACCOUNT_LOCKED' || result.code === 'ACCOUNT_LOCKED_DUE_TO_FAILURES') {
    showAccountLocked.value = true
    lockedMessage.value = result.error
    
    if (result.remaining_seconds) {
      remainingSeconds.value = result.remaining_seconds
      startCountdown()
    }
  }
  // 密码错误，显示剩余次数
  else if (result.code === 'INVALID_CREDENTIALS' && result.remaining_attempts !== undefined) {
    error.value = result.error
  }
  else if (result.code === 'ACCOUNT_DISABLED') {
    error.value = '您的账号已被停用，请联系管理员'
  }
  else if (result.code === 'PERSONNEL_DISABLED') {
    error.value = '账号所属人员已停用，请联系管理员'
    disabledPersonnel.value = normalizeDisabledPersonnel(result)
  }
  else if (result.code === 'DEPARTMENT_DISABLED') {
    error.value = '账号所属部门已停用，请联系管理员'
    disabledDepartmentPersonnel.value = normalizeDisabledPersonnel(result)
  }
  // 其他错误
  else {
    error.value = result.error
  }
}

function startCountdown() {
  const timer = setInterval(() => {
    remainingSeconds.value--
    if (remainingSeconds.value <= 0) {
      clearInterval(timer)
      showAccountLocked.value = false
    }
  }, 1000)
}

function redirectAfterLogin(role) {
  // 根据角色跳转
  if (role === 'admin') {
    window.location.href = '/admin'
  } else {
    window.location.href = '/'
  }
}

function goToChangePassword() {
  window.location.href = '/profile'
}
</script>

<template>
  <div class="login-container">
    <div class="login-box">
      <div class="login-header">
        <h1>磷酸铁锂知识库</h1>
        <p>用户登录</p>
      </div>
      
      <form @submit.prevent="handleLogin" class="login-form">
        <div v-if="error" class="error-message">
          {{ error }}
        </div>

        <div v-if="disabledPersonnel" class="disabled-personnel-card">
          <div v-if="disabledPersonnel.employee_no" class="disabled-personnel-row">
            <span>工号</span>
            <strong>{{ disabledPersonnel.employee_no }}</strong>
          </div>
          <div v-if="disabledPersonnel.full_name" class="disabled-personnel-row">
            <span>姓名</span>
            <strong>{{ disabledPersonnel.full_name }}</strong>
          </div>
          <div v-if="disabledPersonnel.department_display" class="disabled-personnel-row">
            <span>部门</span>
            <strong>{{ disabledPersonnel.department_display }}</strong>
          </div>
        </div>

        <div v-if="disabledDepartmentPersonnel" class="disabled-personnel-card">
          <div v-if="disabledDepartmentPersonnel.employee_no" class="disabled-personnel-row">
            <span>工号</span>
            <strong>{{ disabledDepartmentPersonnel.employee_no }}</strong>
          </div>
          <div v-if="disabledDepartmentPersonnel.full_name" class="disabled-personnel-row">
            <span>姓名</span>
            <strong>{{ disabledDepartmentPersonnel.full_name }}</strong>
          </div>
          <div v-if="disabledDepartmentPersonnel.department_display" class="disabled-personnel-row">
            <span>部门</span>
            <strong>{{ disabledDepartmentPersonnel.department_display }}</strong>
          </div>
        </div>
        
        <div v-if="showAccountLocked" class="locked-message">
          <div class="locked-icon">🔒</div>
          <div class="locked-content">
            <p class="locked-title">账号已锁定</p>
            <p class="locked-text">{{ lockedMessage }}</p>
            <p v-if="remainingSeconds > 0" class="locked-countdown">
              剩余时间：{{ Math.floor(remainingSeconds / 60) }}分{{ remainingSeconds % 60 }}秒
            </p>
          </div>
        </div>
        
        <div v-if="showSetupWarning" class="first-login-message">
          <div class="first-login-icon">🔑</div>
          <div class="first-login-content">
            <p class="first-login-title">{{ setupWarningTitle }}</p>
            <p class="first-login-text">{{ setupWarningText }}</p>
            <p class="first-login-hint">3秒后自动跳转到个人中心...</p>
          </div>
        </div>
        
        <div v-if="showPasswordWarning" class="warning-message">
          <div class="warning-icon">⚠️</div>
          <div class="warning-content">
            <p class="warning-title">密码过期提醒</p>
            <p class="warning-text">{{ passwordWarningMessage }}</p>
            <button type="button" class="change-password-btn" @click="goToChangePassword">
              立即修改密码
            </button>
            <p class="warning-hint">3秒后自动跳转...</p>
          </div>
        </div>
        
        <div class="form-group">
          <label>用户名</label>
          <input 
            type="text" 
            v-model="username"
            placeholder="请输入用户名"
            :disabled="loading"
          >
        </div>
        
        <div class="form-group">
          <label>密码</label>
          <div class="password-input">
            <input
              :type="showLoginPassword ? 'text' : 'password'"
              v-model="password"
              placeholder="请输入密码"
              :disabled="loading"
            >
            <button
              type="button"
              class="password-toggle"
              aria-label="显示或隐藏密码"
              :disabled="loading"
              @click="showLoginPassword = !showLoginPassword"
            >
              {{ showLoginPassword ? '隐藏' : '显示' }}
            </button>
          </div>
        </div>
        
        <button type="submit" class="login-btn" :disabled="loading">
          {{ loading ? '登录中...' : '登录' }}
        </button>
      </form>
      
      <div class="login-footer">
        <a href="/">返回首页</a>
        <span class="divider">|</span>
        <a href="/register">注册账号</a>
        <span class="divider">|</span>
        <a href="/forgot-password">忘记密码？</a>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 20px;
}

.login-box {
  background: white;
  border-radius: 12px;
  padding: 40px;
  width: 100%;
  max-width: 400px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.login-header {
  text-align: center;
  margin-bottom: 30px;
}

.login-header h1 {
  font-size: 24px;
  color: #1f2937;
  margin-bottom: 8px;
}

.login-header p {
  color: #6b7280;
  font-size: 14px;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-group label {
  font-size: 14px;
  color: #374151;
  font-weight: 500;
}

.form-group input {
  padding: 12px 16px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 14px;
  transition: border-color 0.2s;
}

.password-input {
  position: relative;
}

.password-input input {
  width: 100%;
  padding-right: 62px;
  box-sizing: border-box;
}

.password-toggle {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  border: none;
  background: transparent;
  color: #667eea;
  cursor: pointer;
  font-size: 13px;
  padding: 4px;
}

.password-toggle:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.form-group input:focus {
  outline: none;
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

.error-message {
  background: #fef2f2;
  color: #dc2626;
  padding: 12px;
  border-radius: 8px;
  font-size: 14px;
  text-align: center;
}

.disabled-personnel-card {
  background: #fff7ed;
  border: 1px solid #fed7aa;
  border-radius: 8px;
  padding: 12px;
  display: grid;
  gap: 8px;
}

.disabled-personnel-row {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  align-items: start;
  gap: 8px;
  font-size: 13px;
}

.disabled-personnel-row span {
  color: #9a3412;
}

.disabled-personnel-row strong {
  color: #7c2d12;
  font-weight: 600;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.warning-message {
  background: #fffbeb;
  border: 2px solid #fbbf24;
  padding: 20px;
  border-radius: 8px;
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.warning-icon {
  font-size: 24px;
  flex-shrink: 0;
}

.warning-content {
  flex: 1;
}

.warning-title {
  font-size: 16px;
  font-weight: 600;
  color: #92400e;
  margin: 0 0 8px 0;
}

.warning-text {
  font-size: 14px;
  color: #78350f;
  margin: 0 0 12px 0;
  line-height: 1.5;
}

.change-password-btn {
  background: #f59e0b;
  color: white;
  border: none;
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s;
}

.change-password-btn:hover {
  background: #d97706;
}

.warning-hint {
  font-size: 12px;
  color: #92400e;
  margin: 8px 0 0 0;
  font-style: italic;
}

.login-btn {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  padding: 14px;
  border-radius: 8px;
  font-size: 16px;
  font-weight: 500;
  cursor: pointer;
  transition: opacity 0.2s;
}

.login-btn:hover:not(:disabled) {
  opacity: 0.9;
}

.login-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.login-footer {
  margin-top: 24px;
  text-align: center;
}

.login-footer a {
  color: #6b7280;
  text-decoration: none;
  font-size: 14px;
}

.login-footer a:hover {
  color: #667eea;
}

.login-footer .divider {
  margin: 0 12px;
  color: #d1d5db;
}

.locked-message {
  background: #fef2f2;
  border: 2px solid #dc2626;
  padding: 20px;
  border-radius: 8px;
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.locked-icon {
  font-size: 24px;
  flex-shrink: 0;
}

.locked-content {
  flex: 1;
}

.locked-title {
  font-size: 16px;
  font-weight: 600;
  color: #991b1b;
  margin: 0 0 8px 0;
}

.locked-text {
  font-size: 14px;
  color: #7f1d1d;
  margin: 0 0 8px 0;
  line-height: 1.5;
}

.locked-countdown {
  font-size: 14px;
  font-weight: 600;
  color: #dc2626;
  margin: 0;
}

.first-login-message {
  background: #eff6ff;
  border: 2px solid #3b82f6;
  padding: 20px;
  border-radius: 8px;
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.first-login-icon {
  font-size: 24px;
  flex-shrink: 0;
}

.first-login-content {
  flex: 1;
}

.first-login-title {
  font-size: 16px;
  font-weight: 600;
  color: #1e40af;
  margin: 0 0 8px 0;
}

.first-login-text {
  font-size: 14px;
  color: #1e3a8a;
  margin: 0 0 8px 0;
  line-height: 1.5;
}

.first-login-hint {
  font-size: 12px;
  color: #1e40af;
  margin: 0;
  font-style: italic;
}
</style>
