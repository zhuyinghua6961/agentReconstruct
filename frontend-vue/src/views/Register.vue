<script setup>
import { ref } from 'vue'
import { authApi, persistStoredUser } from '../services/auth'

const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const error = ref('')
const success = ref('')
const loading = ref(false)

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

        <section class="form-section">
          <h2>账号信息</h2>
          <div class="form-grid">
            <div class="form-group">
              <label>用户名</label>
              <input v-model="username" type="text" placeholder="请输入用户名" :disabled="loading">
            </div>
            <div class="form-group">
              <label>密码</label>
              <input v-model="password" type="password" placeholder="请输入密码" :disabled="loading">
            </div>
            <div class="form-group full-width">
              <label>确认密码</label>
              <input v-model="confirmPassword" type="password" placeholder="请再次输入密码" :disabled="loading">
            </div>
          </div>
          <p class="section-hint">密码要求与首次登录修改密码一致：至少 8 位，且 4 类字符至少包含 3 类。</p>
        </section>

        <section class="form-section">
          <h2>部门同步说明</h2>
          <p class="section-hint">部门信息将根据绑定的人员记录自动带出，注册时无需单独填写。</p>
        </section>

        <section class="form-section">
          <h2>人员信息</h2>
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

        <section class="form-section">
          <div class="section-title">
            <h2>安全问题设置</h2>
            <button
              v-if="securityQuestions.length < 3"
              type="button"
              class="link-btn"
              :disabled="loading"
              @click="addQuestion"
            >
              + 添加问题
            </button>
          </div>
          <p class="section-hint">至少设置 1 个，最多 3 个，注册成功后不再进入首次登录安全问题补全。</p>

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
  padding: 28px;
  background:
    radial-gradient(circle at top left, rgba(255, 255, 255, 0.22), transparent 34%),
    linear-gradient(145deg, #4f7cac 0%, #6b9080 45%, #f6bd60 100%);
}

.register-card {
  width: min(920px, 100%);
  background: rgba(255, 255, 255, 0.96);
  border-radius: 24px;
  padding: 36px;
  box-shadow: 0 28px 80px rgba(15, 23, 42, 0.24);
  backdrop-filter: blur(8px);
}

.card-header {
  margin-bottom: 28px;
}

.card-header h1 {
  margin: 0;
  font-size: 34px;
  color: #17324d;
}

.subtitle {
  margin: 10px 0 0;
  color: #4b5563;
}

.register-form {
  display: flex;
  flex-direction: column;
  gap: 22px;
}

.form-section {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 20px;
  border: 1px solid #dbe4ea;
  border-radius: 18px;
  background: #f8fbfc;
}

.form-section h2 {
  margin: 0;
  font-size: 20px;
  color: #17324d;
}

.section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.section-status,
.section-hint {
  color: #5f6b7a;
  font-size: 14px;
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
  color: #1f2937;
}

input,
select {
  width: 100%;
  padding: 12px 14px;
  border: 1px solid #c7d2da;
  border-radius: 12px;
  background: #fff;
  font-size: 15px;
  color: #111827;
  box-sizing: border-box;
}

input:focus,
select:focus {
  outline: none;
  border-color: #4f7cac;
  box-shadow: 0 0 0 3px rgba(79, 124, 172, 0.14);
}

.alert {
  border-radius: 14px;
  padding: 12px 14px;
  font-size: 14px;
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
  padding: 16px;
  border-radius: 16px;
  background: #ffffff;
  border: 1px solid #d7e1e8;
}

.question-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #17324d;
  font-weight: 600;
}

.link-btn,
.remove-btn {
  border: none;
  background: none;
  color: #1d4ed8;
  cursor: pointer;
  padding: 0;
  font-size: 14px;
}

.submit-btn {
  width: 100%;
  border: none;
  border-radius: 16px;
  padding: 14px 18px;
  background: linear-gradient(135deg, #17324d 0%, #2a6f97 100%);
  color: #fff;
  font-size: 16px;
  font-weight: 700;
  cursor: pointer;
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
  color: #17324d;
  text-decoration: none;
  font-weight: 600;
}

@media (max-width: 768px) {
  .register-container {
    padding: 16px;
  }

  .register-card {
    padding: 24px;
    border-radius: 18px;
  }

  .form-grid {
    grid-template-columns: 1fr;
  }

  .form-group.full-width {
    grid-column: auto;
  }
}
</style>
