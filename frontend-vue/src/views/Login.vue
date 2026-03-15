<script setup>
import { ref } from 'vue'
import { authApi } from '../services/auth'

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)
const showPasswordWarning = ref(false)
const passwordWarningMessage = ref('')
const showAccountLocked = ref(false)
const lockedMessage = ref('')
const remainingSeconds = ref(0)
const showFirstLoginWarning = ref(false)
const firstLoginWarningText = ref('为了您的账号安全，请立即修改密码')

async function handleLogin() {
  error.value = ''
  showPasswordWarning.value = false
  showAccountLocked.value = false
  showFirstLoginWarning.value = false
  
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
      
      // 确保用户信息包含is_first_login字段
      const userData = {
        ...result.data.user,
        is_first_login: result.data.is_first_login || false,
        require_security_questions_setup: Boolean(result.require_security_questions_setup),
        has_security_questions: Boolean(result.data?.has_security_questions),
      }
      localStorage.setItem('user', JSON.stringify(userData))
      
      // 检查是否需要进入首次安全设置流程（改密码 + 设置安全问题）
      if (result.require_password_change || result.require_security_questions_setup) {
        const hints = []
        if (result.require_password_change) hints.push('修改密码')
        if (result.require_security_questions_setup) hints.push('设置至少一个安全问题')
        firstLoginWarningText.value = `为了您的账号安全，请立即${hints.join('并')}。`
        showFirstLoginWarning.value = true
        // 3秒后跳转到修改密码页面
        setTimeout(() => {
          const params = new URLSearchParams()
          if (result.require_password_change) params.set('change_password', 'required')
          if (result.require_security_questions_setup) params.set('security_questions', 'required')
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
        
        <div v-if="showFirstLoginWarning" class="first-login-message">
          <div class="first-login-icon">🔑</div>
          <div class="first-login-content">
            <p class="first-login-title">首次登录</p>
            <p class="first-login-text">{{ firstLoginWarningText }}</p>
            <p class="first-login-hint">3秒后自动跳转到修改密码页面...</p>
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
          <input 
            type="password" 
            v-model="password"
            placeholder="请输入密码"
            :disabled="loading"
          >
        </div>
        
        <button type="submit" class="login-btn" :disabled="loading">
          {{ loading ? '登录中...' : '登录' }}
        </button>
      </form>
      
      <div class="login-footer">
        <a href="/">返回首页</a>
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
