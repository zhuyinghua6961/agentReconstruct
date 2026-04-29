<script setup>
import { ref } from 'vue'
import { authApi, persistStoredUser } from '../services/auth'

const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const error = ref('')
const success = ref('')
const loading = ref(false)
const showRegisterPassword = ref(false)
const showConfirmPassword = ref(false)

const employeeNoInput = ref('')
const fullNameInput = ref('')
const verificationCodeInput = ref('')

const securityQuestions = ref([''])
const securityAnswers = ref([''])
const presetQuestions = [
  '我最喜欢的水果是什么？',
  '我出生在哪个城市？',
  '我最喜欢的一本书是什么？',
  '我的小学名称是什么？',
  '我最喜欢的电影是什么？',
  '我最喜欢的一句话是什么？',
  '我的偶像是谁？',
  '我最喜欢的一种运动是什么？',
]

function validatePasswordStrength(value) {
  if (!value || value.length < 8) {
    return '密码长度不能少于8位'
  }
  let categoryCount = 0
  categoryCount += /[a-z]/.test(value) ? 1 : 0
  categoryCount += /[A-Z]/.test(value) ? 1 : 0
  categoryCount += /[0-9]/.test(value) ? 1 : 0
  categoryCount += /[^A-Za-z0-9]/.test(value) ? 1 : 0
  if (categoryCount < 3) {
    return '密码必须包含数字、小写字母、大写字母、特殊符号中的至少3类'
  }
  return ''
}

function addQuestion() {
  if (securityQuestions.value.length >= 3) {
    return
  }
  securityQuestions.value.push('')
  securityAnswers.value.push('')
}

function removeQuestion(index) {
  if (securityQuestions.value.length <= 1) {
    return
  }
  securityQuestions.value.splice(index, 1)
  securityAnswers.value.splice(index, 1)
}

function buildSecurityQuestionItems() {
  if (securityQuestions.value.length < 1 || securityQuestions.value.length > 3) {
    return { success: false, error: '安全问题数量必须在1-3个之间' }
  }

  const items = []
  for (let i = 0; i < securityQuestions.value.length; i += 1) {
    const question = String(securityQuestions.value[i] || '').trim()
    const answer = String(securityAnswers.value[i] || '').trim()
    if (!question) {
      return { success: false, error: `请选择第${i + 1}个安全问题` }
    }
    if (!answer) {
      return { success: false, error: `请输入第${i + 1}个问题的答案` }
    }
    items.push({ question, answer })
  }
  return { success: true, data: items }
}

async function handleRegister() {
  error.value = ''
  success.value = ''

  const normalizedUsername = String(username.value || '').trim()
  if (!normalizedUsername) {
    error.value = '请输入用户名'
    return
  }
  if (normalizedUsername.length < 3 || normalizedUsername.length > 50) {
    error.value = '用户名长度必须在3-50之间'
    return
  }
  if (normalizedUsername.toLowerCase().startsWith('admin')) {
    error.value = '不能以 admin 开头'
    return
  }

  const passwordError = validatePasswordStrength(password.value)
  if (passwordError) {
    error.value = passwordError
    return
  }
  if (password.value !== confirmPassword.value) {
    error.value = '两次输入的密码不一致'
    return
  }

  if (!String(employeeNoInput.value || '').trim() || !String(fullNameInput.value || '').trim() || !String(verificationCodeInput.value || '').trim()) {
    error.value = '请完整填写工号、姓名和校验码'
    return
  }

  const securityQuestionResult = buildSecurityQuestionItems()
  if (!securityQuestionResult.success) {
    error.value = securityQuestionResult.error
    return
  }

  loading.value = true
  try {
    const result = await authApi.register({
      username: normalizedUsername,
      password: password.value,
      confirmPassword: confirmPassword.value,
      employee_no: String(employeeNoInput.value || '').trim(),
      full_name: String(fullNameInput.value || '').trim(),
      verification_code: String(verificationCodeInput.value || '').trim(),
      security_questions: securityQuestionResult.data,
    })

    if (!result.success) {
      error.value = result.error || '注册失败'
      return
    }

    localStorage.setItem('token', result.data.token)
    localStorage.setItem('agentcode.auth.token.v1', result.data.token)
    persistStoredUser({
      ...(result.data?.user || {}),
      is_first_login: Boolean(result.data?.is_first_login),
      require_security_questions_setup: Boolean(result.data?.require_security_questions_setup),
      require_department_setup: Boolean(result.data?.require_department_setup),
      require_personnel_setup: Boolean(result.data?.require_personnel_setup),
      has_security_questions: Boolean(result.data?.has_security_questions),
    })
    success.value = '注册成功，正在进入系统...'
    window.location.href = '/'
  } catch (e) {
    error.value = e instanceof Error && e.message ? e.message : '注册失败，请稍后重试'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="register-container">
    <div class="register-card">
      <div class="card-header">
        <h1>注册账号</h1>
        <p class="subtitle">一次性完成账号、人员绑定和安全问题设置</p>
      </div>

      <form class="register-form" @submit.prevent="handleRegister">
        <div v-if="error" class="alert alert-error">{{ error }}</div>
        <div v-if="success" class="alert alert-success">{{ success }}</div>

        <div class="register-layout">
          <div class="details-column">
            <section class="form-section">
              <h2>账号信息</h2>
              <div class="form-grid">
                <div class="form-group">
                  <label>用户名</label>
                  <input v-model="username" type="text" placeholder="请输入用户名" :disabled="loading">
                </div>
                <div class="form-group">
                  <label>密码</label>
                  <div class="password-input">
                    <input
                      v-model="password"
                      :type="showRegisterPassword ? 'text' : 'password'"
                      placeholder="请输入密码"
                      :disabled="loading"
                    >
                    <button
                      type="button"
                      class="password-toggle"
                      aria-label="显示或隐藏密码"
                      :disabled="loading"
                      @click="showRegisterPassword = !showRegisterPassword"
                    >
                      {{ showRegisterPassword ? '隐藏' : '显示' }}
                    </button>
                  </div>
                </div>
                <div class="form-group full-width">
                  <label>确认密码</label>
                  <div class="password-input">
                    <input
                      v-model="confirmPassword"
                      :type="showConfirmPassword ? 'text' : 'password'"
                      placeholder="请再次输入密码"
                      :disabled="loading"
                    >
                    <button
                      type="button"
                      class="password-toggle"
                      aria-label="显示或隐藏密码"
                      :disabled="loading"
                      @click="showConfirmPassword = !showConfirmPassword"
                    >
                      {{ showConfirmPassword ? '隐藏' : '显示' }}
                    </button>
                  </div>
                </div>
              </div>
              <p class="section-hint">密码至少 8 位，且数字、小写字母、大写字母、特殊符号中至少包含 3 类。</p>
            </section>

            <section class="form-section">
              <h2>人员信息</h2>
              <p class="section-hint personnel-hint">部门信息将根据绑定的人员记录自动带出，注册时无需单独填写。</p>
              <div class="form-grid">
                <div class="form-group">
                  <label>工号</label>
                  <input v-model="employeeNoInput" type="text" placeholder="请输入工号" :disabled="loading">
                </div>
                <div class="form-group">
                  <label>姓名</label>
                  <input v-model="fullNameInput" type="text" placeholder="请输入姓名" :disabled="loading">
                </div>
                <div class="form-group full-width">
                  <label>校验码</label>
                  <input v-model="verificationCodeInput" type="password" placeholder="请输入校验码" :disabled="loading">
                </div>
              </div>
            </section>
          </div>

          <section class="form-section security-column">
            <div class="section-title">
              <h2>安全问题设置</h2>
              <button
                v-if="securityQuestions.length < 3"
                type="button"
                class="link-btn"
                :disabled="loading"
                @click="addQuestion"
              >
                添加问题
              </button>
            </div>
            <p class="section-hint">至少设置 1 个，最多 3 个。用于找回密码。</p>

            <div
              v-for="(question, index) in securityQuestions"
              :key="`question-${index}`"
              class="question-card"
            >
              <div class="question-header">
                <span>问题 {{ index + 1 }}</span>
                <button
                  v-if="securityQuestions.length > 1"
                  type="button"
                  class="remove-btn"
                  :disabled="loading"
                  @click="removeQuestion(index)"
                >
                  移除
                </button>
              </div>
              <select v-model="securityQuestions[index]" :disabled="loading">
                <option value="">请选择安全问题</option>
                <option v-for="pq in presetQuestions" :key="pq" :value="pq">
                  {{ pq }}
                </option>
              </select>
              <input
                v-model="securityAnswers[index]"
                type="text"
                placeholder="请输入答案"
                :disabled="loading"
              >
            </div>
          </section>
        </div>

        <button type="submit" class="submit-btn" :disabled="loading">
          {{ loading ? '注册中...' : '注册并进入系统' }}
        </button>
      </form>

      <div class="card-footer">
        <a href="/login">已有账号，返回登录</a>
      </div>
    </div>
  </div>
</template>

<style scoped>
.register-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px 20px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.register-card {
  width: 100%;
  max-width: 900px;
  background: white;
  border-radius: 16px;
  padding: 40px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.card-header {
  margin-bottom: 28px;
  text-align: center;
}

.card-header h1 {
  margin: 0;
  font-size: 28px;
  color: #1f2937;
}

.subtitle {
  margin: 8px 0 0;
  color: #6b7280;
  font-size: 14px;
}

.register-form {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.register-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 20px;
  align-items: start;
}

.details-column {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 18px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #f9fafb;
}

.form-section h2 {
  margin: 0;
  font-size: 18px;
  color: #1f2937;
}

.section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.section-status,
.section-hint {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
  line-height: 1.5;
}

.personnel-hint {
  padding: 10px 12px;
  border: 1px solid #dbeafe;
  border-radius: 8px;
  background: #eff6ff;
  color: #1d4ed8;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px 16px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-group.full-width {
  grid-column: 1 / -1;
}

label {
  font-weight: 600;
  color: #374151;
  font-size: 14px;
}

input,
select {
  width: 100%;
  padding: 12px 16px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  background: #fff;
  font-size: 14px;
  color: #111827;
  box-sizing: border-box;
  transition: border-color 0.2s, box-shadow 0.2s;
}

input:focus,
select:focus {
  outline: none;
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

.password-input {
  position: relative;
}

.password-input input {
  padding-right: 62px;
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

.alert {
  border-radius: 8px;
  padding: 12px 14px;
  font-size: 14px;
  text-align: center;
}

.alert-error {
  background: #fef2f2;
  color: #b91c1c;
  border: 1px solid #fecaca;
}

.alert-success {
  background: #ecfdf5;
  color: #047857;
  border: 1px solid #a7f3d0;
}

.alert-inline {
  background: #fff7ed;
  color: #c2410c;
  border: 1px solid #fed7aa;
}

.question-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border-radius: 10px;
  background: #ffffff;
  border: 1px solid #e5e7eb;
}

.question-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #374151;
  font-size: 14px;
  font-weight: 600;
}

.link-btn,
.remove-btn {
  border: 1px solid #d1d5db;
  background: white;
  border-radius: 999px;
  color: #1d4ed8;
  cursor: pointer;
  padding: 5px 10px;
  font-size: 14px;
}

.link-btn:hover,
.remove-btn:hover {
  background: #eff6ff;
}

.submit-btn {
  width: 100%;
  border: none;
  border-radius: 8px;
  padding: 14px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: #fff;
  font-size: 16px;
  font-weight: 500;
  cursor: pointer;
  transition: opacity 0.2s;
}

.submit-btn:hover:not(:disabled) {
  opacity: 0.9;
}

.submit-btn:disabled,
.link-btn:disabled,
.remove-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.card-footer {
  margin-top: 20px;
  text-align: center;
}

.card-footer a {
  color: #6b7280;
  text-decoration: none;
  font-size: 14px;
}

.card-footer a:hover {
  color: #667eea;
}

@media (max-width: 860px) {
  .register-container {
    padding: 16px;
    align-items: flex-start;
  }

  .register-card {
    padding: 24px;
    border-radius: 14px;
  }

  .register-layout {
    grid-template-columns: 1fr;
  }

  .form-grid {
    grid-template-columns: 1fr;
  }

  .form-group.full-width {
    grid-column: auto;
  }
}
</style>
