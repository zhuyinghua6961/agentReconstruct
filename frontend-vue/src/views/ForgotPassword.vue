<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { authApi } from '../services/auth'

const router = useRouter()

// 步骤: 1=输入用户名, 2=回答问题, 3=重置成功
const step = ref(1)
const username = ref('')
const questions = ref([])
const answers = ref([])
const newPassword = ref('')
const confirmPassword = ref('')
const error = ref('')
const success = ref('')
const loading = ref(false)

// 预设安全问题选项
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

// 步骤1: 输入用户名
async function checkUsername() {
  error.value = ''
  if (!username.value.trim()) {
    error.value = '请输入用户名'
    return
  }
  
  loading.value = true
  const result = await authApi.initiatePasswordReset(username.value)
  loading.value = false
  
  if (result.success) {
    if (result.data.has_security_questions) {
      questions.value = result.data.questions
      step.value = 2
    } else {
      error.value = '该用户未设置安全问题，请联系管理员重置密码'
    }
  } else {
    error.value = result.error
  }
}

// 步骤2: 回答问题并重置密码
async function verifyAndReset() {
  error.value = ''
  success.value = ''
  
  // 验证所有问题都已回答
  if (answers.value.length !== questions.value.length) {
    error.value = '请回答所有安全问题'
    return
  }
  
  for (let i = 0; i < answers.value.length; i++) {
    if (!answers.value[i] || !answers.value[i].trim()) {
      error.value = `请回答第${i + 1}个问题`
      return
    }
  }
  
  // 验证新密码（与后端规则对齐：至少8位，4类字符至少3类）
  if (!newPassword.value || newPassword.value.length < 8) {
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
  
  if (newPassword.value !== confirmPassword.value) {
    error.value = '两次输入的密码不一致'
    return
  }
  
  loading.value = true
  const result = await authApi.verifyAndResetPassword(username.value, answers.value, newPassword.value)
  loading.value = false
  
  if (result.success) {
    success.value = '密码重置成功！'
    step.value = 3
  } else {
    error.value = result.error
  }
}

function goToLogin() {
  router.push('/login')
}
</script>

<template>
  <div class="forgot-password-container">
    <div class="forgot-password-card">
      <div class="card-header">
        <h1>找回密码</h1>
        <p class="subtitle">通过安全问题重置密码</p>
      </div>

      <!-- 步骤1: 输入用户名 -->
      <div v-if="step === 1" class="step-content">
        <div class="form-group">
          <label>用户名</label>
          <input 
            type="text" 
            v-model="username" 
            placeholder="请输入您的用户名"
            @keyup.enter="checkUsername"
          >
        </div>
        
        <div v-if="error" class="alert alert-error">{{ error }}</div>
        
        <button class="btn-primary" @click="checkUsername" :loading="loading">
          下一步
        </button>
      </div>

      <!-- 步骤2: 回答问题 -->
      <div v-if="step === 2" class="step-content">
        <h3>回答安全问题</h3>
        <p class="hint">请回答以下安全问题，答案不区分大小写</p>
        
        <div class="questions-list">
          <div v-for="(question, index) in questions" :key="index" class="question-item">
            <label>{{ index + 1 }}. {{ question }}</label>
            <input 
              type="text" 
              v-model="answers[index]"
              :placeholder="`请输入答案`"
            >
          </div>
        </div>
        
        <div class="form-group">
          <label>新密码</label>
          <input 
            type="password" 
            v-model="newPassword" 
            placeholder="请输入新密码（至少8位，4类字符至少3类）"
          >
        </div>
        
        <div class="form-group">
          <label>确认新密码</label>
          <input 
            type="password" 
            v-model="confirmPassword" 
            placeholder="请再次输入新密码"
            @keyup.enter="verifyAndReset"
          >
        </div>
        
        <div v-if="error" class="alert alert-error">{{ error }}</div>
        
        <div class="button-group">
          <button class="btn-secondary" @click="step = 1">上一步</button>
          <button class="btn-primary" @click="verifyAndReset" :loading="loading">
            确认重置
          </button>
        </div>
      </div>

      <!-- 步骤3: 重置成功 -->
      <div v-if="step === 3" class="step-content success-content">
        <div class="success-icon">✓</div>
        <h3>{{ success }}</h3>
        <p>请使用新密码登录</p>
        <button class="btn-primary" @click="goToLogin">返回登录</button>
      </div>

      <div class="card-footer">
        <a href="/login">返回登录</a>
      </div>
    </div>
  </div>
</template>

<style scoped>
.forgot-password-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 20px;
}

.forgot-password-card {
  background: white;
  border-radius: 16px;
  padding: 40px;
  width: 100%;
  max-width: 420px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.3);
}

.card-header {
  text-align: center;
  margin-bottom: 32px;
}

.card-header h1 {
  font-size: 28px;
  color: #1f2937;
  margin: 0 0 8px 0;
}

.subtitle {
  color: #6b7280;
  font-size: 14px;
  margin: 0;
}

.step-content h3 {
  font-size: 18px;
  color: #1f2937;
  margin: 0 0 8px 0;
}

.hint {
  color: #6b7280;
  font-size: 13px;
  margin: 0 0 20px 0;
}

.form-group {
  margin-bottom: 20px;
}

.form-group label {
  display: block;
  font-size: 14px;
  color: #374151;
  margin-bottom: 8px;
}

.form-group input {
  width: 100%;
  padding: 12px 16px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 14px;
  transition: border-color 0.2s;
}

.form-group input:focus {
  outline: none;
  border-color: #667eea;
}

.questions-list {
  margin-bottom: 20px;
}

.question-item {
  margin-bottom: 16px;
}

.question-item label {
  display: block;
  font-size: 14px;
  color: #374151;
  margin-bottom: 8px;
}

.question-item input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 14px;
}

.button-group {
  display: flex;
  gap: 12px;
}

.btn-primary, .btn-secondary {
  flex: 1;
  padding: 12px 20px;
  border-radius: 8px;
  font-size: 14px;
  cursor: pointer;
  border: none;
  transition: background-color 0.2s;
}

.btn-primary {
  background: #667eea;
  color: white;
}

.btn-primary:hover {
  background: #5a67d8;
}

.btn-secondary {
  background: #f3f4f6;
  color: #374151;
  border: 1px solid #d1d5db;
}

.btn-secondary:hover {
  background: #e5e7eb;
}

.alert {
  padding: 12px 16px;
  border-radius: 8px;
  margin-bottom: 16px;
  font-size: 14px;
}

.alert-error {
  background: #fef2f2;
  color: #dc2626;
}

.success-content {
  text-align: center;
}

.success-icon {
  width: 64px;
  height: 64px;
  background: #dcfce7;
  color: #166534;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 32px;
  margin: 0 auto 20px;
}

.success-content h3 {
  color: #166534;
}

.success-content p {
  color: #6b7280;
  margin-bottom: 24px;
}

.card-footer {
  text-align: center;
  margin-top: 24px;
  padding-top: 24px;
  border-top: 1px solid #e5e7eb;
}

.card-footer a {
  color: #667eea;
  text-decoration: none;
  font-size: 14px;
}

.card-footer a:hover {
  text-decoration: underline;
}
</style>
