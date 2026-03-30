<script setup>
import { computed, onMounted, ref } from 'vue'
import { quotaApi } from '../services/quota'

const configs = ref([])
const loading = ref(false)
const error = ref('')
const success = ref('')
const editingConfig = ref(null)
const creatingConfig = ref(null)

const PRESET_QUOTA_TYPES = [
  { value: 'ask_query', name: '普通问答' },
  { value: 'file_qa', name: '文件问答' },
  { value: 'file_view', name: '查看原文' },
  { value: 'doc_assist', name: '文档辅助' },
]

function normalizeLimitInput(value) {
  if (value === null || value === undefined || String(value).trim() === '') return ''
  const n = Number(value)
  return Number.isFinite(n) ? Math.max(0, Math.floor(n)) : ''
}

function parseLimitInput(value) {
  if (value === null || value === undefined || String(value).trim() === '') {
    return { value: null, error: null }
  }
  const n = Number(value)
  if (!Number.isFinite(n) || n < 0) {
    return { value: null, error: '配额上限必须是大于等于0的整数' }
  }
  return { value: Math.floor(n), error: null }
}

function pickDefaultLimit(payload) {
  for (const key of ['daily_limit', 'weekly_limit', 'monthly_limit']) {
    const value = payload[key]
    if (value !== null && value !== undefined) return Number(value)
  }
  return 0
}

function formatLimitForDisplay(value) {
  if (value === null || value === undefined || String(value).trim() === '') return '未设置'
  return String(value)
}

const quotaTypeOptions = computed(() => {
  const existing = new Set((configs.value || []).map((item) => String(item?.quota_type || '').trim()))
  return PRESET_QUOTA_TYPES.map((item) => ({ ...item, exists: existing.has(item.value) }))
})

const existingQuotaTypes = computed(() => {
  return new Set((configs.value || []).map((item) => String(item?.quota_type || '').trim()).filter(Boolean))
})

const availableQuotaTypeOptions = computed(() => quotaTypeOptions.value.filter((item) => !item.exists))

function buildEditableConfig(config) {
  return {
    ...config,
    is_active: Boolean(config.is_active),
    daily_limit: normalizeLimitInput(config.daily_limit),
    weekly_limit: normalizeLimitInput(config.weekly_limit),
    monthly_limit: normalizeLimitInput(config.monthly_limit),
  }
}

async function fetchConfigs() {
  loading.value = true
  error.value = ''
  const result = await quotaApi.getQuotaConfigs()
  if (result.success) {
    configs.value = (result?.data?.configs || []).map((item) => buildEditableConfig(item))
  } else {
    error.value = result.error || '获取配额配置失败'
  }
  loading.value = false
}

function startEdit(config) {
  editingConfig.value = buildEditableConfig(config)
}

function cancelEdit() {
  editingConfig.value = null
  error.value = ''
}

function startCreate() {
  error.value = ''
  success.value = ''
  const first = availableQuotaTypeOptions.value[0] || null
  if (!first) {
    error.value = '4 个标准配额类型都已存在，请直接编辑现有配置'
    return
  }
  creatingConfig.value = {
    quota_type: first.value,
    quota_name: first.name,
    daily_limit: '',
    weekly_limit: '',
    monthly_limit: '',
    is_active: true,
  }
}

function cancelCreate() {
  creatingConfig.value = null
  error.value = ''
}

function syncCreateQuotaName() {
  if (!creatingConfig.value) return
  const selected = PRESET_QUOTA_TYPES.find((item) => item.value === creatingConfig.value.quota_type)
  if (selected) creatingConfig.value.quota_name = selected.name
}

function buildLimitPayload(source) {
  const daily = parseLimitInput(source.daily_limit)
  const weekly = parseLimitInput(source.weekly_limit)
  const monthly = parseLimitInput(source.monthly_limit)
  if (daily.error || weekly.error || monthly.error) {
    return { ok: false, error: daily.error || weekly.error || monthly.error }
  }
  const payload = {
    daily_limit: daily.value,
    weekly_limit: weekly.value,
    monthly_limit: monthly.value,
  }
  if (source.is_active && payload.daily_limit === null && payload.weekly_limit === null && payload.monthly_limit === null) {
    return { ok: false, error: '启用状态下至少需要设置一个周期配额（日/周/月）' }
  }
  return { ok: true, payload }
}

function buildMutationPayload(source) {
  const built = buildLimitPayload(source)
  if (!built.ok) {
    return built
  }
  const allPeriodLimitsEmpty =
    built.payload.daily_limit === null &&
    built.payload.weekly_limit === null &&
    built.payload.monthly_limit === null
  return {
    ok: true,
    payload: {
      ...built.payload,
      default_limit: allPeriodLimitsEmpty ? 0 : pickDefaultLimit(built.payload),
      is_active: source.is_active ? 1 : 0,
      ...(allPeriodLimitsEmpty && !source.is_active ? { period: 'none' } : {}),
    },
  }
}

async function saveCreateConfig() {
  error.value = ''
  success.value = ''
  const quotaType = String(creatingConfig.value?.quota_type || '').trim()
  if (!quotaType) {
    error.value = '请输入配额类型'
    return
  }
  if (existingQuotaTypes.value.has(quotaType)) {
    error.value = '该配额类型已存在，请直接编辑现有配置'
    return
  }

  const built = buildMutationPayload(creatingConfig.value)
  if (!built.ok) {
    error.value = built.error
    return
  }
  const payload = {
    quota_type: quotaType,
    quota_name: String(creatingConfig.value.quota_name || '').trim() || quotaType,
    ...built.payload,
  }
  const result = await quotaApi.createQuotaConfig(payload)
  if (result.success) {
    success.value = '配额已新增'
    creatingConfig.value = null
    await fetchConfigs()
    setTimeout(() => (success.value = ''), 3000)
  } else {
    error.value = result.error || '新增失败'
  }
}

async function saveConfig() {
  error.value = ''
  success.value = ''
  if (!editingConfig.value) return

  const built = buildMutationPayload(editingConfig.value)
  if (!built.ok) {
    error.value = built.error
    return
  }
  const payload = { ...built.payload }

  const result = await quotaApi.updateQuotaConfig(editingConfig.value.quota_type, payload)
  if (result.success) {
    success.value = '配置已更新'
    editingConfig.value = null
    await fetchConfigs()
    setTimeout(() => (success.value = ''), 3000)
  } else {
    error.value = result.error || '更新失败'
  }
}

onMounted(fetchConfigs)
</script>

<template>
  <div class="quota-management">
    <header class="page-header">
      <div class="header-left">
        <a href="/" class="back-link">← 返回</a>
        <h1>配额管理</h1>
      </div>
      <div class="header-actions">
        <button
          class="btn-create"
          :disabled="loading"
          @click="startCreate"
        >
          新增配额
        </button>
      </div>
    </header>

    <main class="page-main">
      <div v-if="success" class="alert alert-success">{{ success }}</div>
      <div v-if="error" class="alert alert-error">{{ error }}</div>

      <div v-if="loading" class="loading">加载中...</div>

      <div v-else class="config-list">
        <div 
          v-for="config in configs" 
          :key="config.quota_type"
          class="config-item"
        >
          <div class="config-header">
            <div class="config-info">
              <h3>{{ config.quota_name }}</h3>
              <span class="config-type">{{ config.quota_type }}</span>
            </div>
            <div class="config-status">
              <span 
                class="status-badge" 
                :class="config.is_active ? 'active' : 'inactive'"
              >
                {{ config.is_active ? '启用' : '停用' }}
              </span>
            </div>
          </div>

          <div class="config-body">
            <div class="config-row">
              <span class="label">每日上限</span>
              <span class="value">{{ formatLimitForDisplay(config.daily_limit) }}</span>
            </div>
            <div class="config-row">
              <span class="label">每周上限</span>
              <span class="value">{{ formatLimitForDisplay(config.weekly_limit) }}</span>
            </div>
            <div class="config-row">
              <span class="label">每月上限</span>
              <span class="value">{{ formatLimitForDisplay(config.monthly_limit) }}</span>
            </div>
            <div class="config-row">
              <span class="label">说明</span>
              <span class="value description">{{ config.description }}</span>
            </div>
          </div>

          <div class="config-actions">
            <button 
              class="btn-edit" 
              @click="startEdit(config)"
            >
              编辑配置
            </button>
          </div>
        </div>
      </div>

      <div v-if="creatingConfig" class="modal-overlay" @click="cancelCreate">
        <div class="modal-content" @click.stop>
          <h2>新增配额配置</h2>

          <div class="form-group">
            <label>配额类型</label>
            <select v-model="creatingConfig.quota_type" @change="syncCreateQuotaName">
              <option
                v-for="item in availableQuotaTypeOptions"
                :key="item.value"
                :value="item.value"
              >
                {{ item.name }}（{{ item.value }}）
              </option>
            </select>
          </div>

          <div class="form-group">
            <label>预设说明</label>
            <p class="input-hint">管理员界面只允许创建 4 个标准配额类型，不再新增 legacy 或自定义 quota_type。</p>
          </div>

          <div class="form-group">
            <label>配额名称</label>
            <input type="text" :value="creatingConfig.quota_name" disabled>
          </div>

          <div class="form-group">
            <label>每日上限（可留空）</label>
            <input
              type="number"
              v-model.number="creatingConfig.daily_limit"
              min="0"
              placeholder="如：200"
            >
          </div>

          <div class="form-group">
            <label>每周上限（可留空）</label>
            <input
              type="number"
              v-model.number="creatingConfig.weekly_limit"
              min="0"
              placeholder="如：1200"
            >
          </div>

          <div class="form-group">
            <label>每月上限（可留空）</label>
            <input
              type="number"
              v-model.number="creatingConfig.monthly_limit"
              min="0"
              placeholder="如：5000"
            >
          </div>

          <div class="form-group">
            <label class="checkbox-label">
              <input
                type="checkbox"
                v-model="creatingConfig.is_active"
              >
              <span>启用此配额</span>
            </label>
          </div>

          <div class="modal-actions">
            <button class="btn-secondary" @click="cancelCreate">取消</button>
            <button class="btn-primary" @click="saveCreateConfig">创建</button>
          </div>
        </div>
      </div>

      <!-- 编辑弹窗 -->
      <div v-if="editingConfig" class="modal-overlay" @click="cancelEdit">
        <div class="modal-content" @click.stop>
          <h2>编辑配额配置</h2>
          
          <div class="form-group">
            <label>配额名称</label>
            <input type="text" :value="editingConfig.quota_name" disabled>
          </div>

          <div class="form-group">
            <label>每日上限（可留空）</label>
            <input
              type="number"
              v-model.number="editingConfig.daily_limit"
              min="0"
              placeholder="如：200"
            >
          </div>

          <div class="form-group">
            <label>每周上限（可留空）</label>
            <input
              type="number"
              v-model.number="editingConfig.weekly_limit"
              min="0"
              placeholder="如：1200"
            >
          </div>

          <div class="form-group">
            <label>每月上限（可留空）</label>
            <input
              type="number"
              v-model.number="editingConfig.monthly_limit"
              min="0"
              placeholder="如：5000"
            >
          </div>

          <div class="form-group">
            <label class="checkbox-label">
              <input 
                type="checkbox" 
                v-model="editingConfig.is_active"
              >
              <span>启用此配额</span>
            </label>
          </div>

          <div class="modal-actions">
            <button class="btn-secondary" @click="cancelEdit">取消</button>
            <button class="btn-primary" @click="saveConfig">保存</button>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.quota-management {
  min-height: 100vh;
  background: #f9fafb;
}

.page-header {
  background: white;
  padding: 20px 40px;
  border-bottom: 1px solid #e5e7eb;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 20px;
}

.header-actions {
  display: flex;
  align-items: center;
}

.back-link {
  color: #667eea;
  text-decoration: none;
  font-size: 14px;
}

.page-header h1 {
  font-size: 24px;
  color: #1f2937;
  margin: 0;
}

.btn-create {
  padding: 8px 16px;
  background: #10b981;
  color: #fff;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
}

.btn-create:hover {
  background: #059669;
}

.btn-create:disabled {
  background: #9ca3af;
  cursor: not-allowed;
}

.page-main {
  max-width: 1200px;
  margin: 0 auto;
  padding: 40px 20px;
}

.loading {
  text-align: center;
  padding: 60px;
  color: #6b7280;
}

.alert {
  padding: 12px 16px;
  border-radius: 8px;
  margin-bottom: 20px;
}

.alert-success {
  background: #dcfce7;
  color: #166534;
}

.alert-error {
  background: #fef2f2;
  color: #dc2626;
}

.config-list {
  display: grid;
  gap: 20px;
}

.config-item {
  background: white;
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.config-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid #f3f4f6;
}

.config-info h3 {
  font-size: 18px;
  color: #1f2937;
  margin: 0 0 4px 0;
}

.config-type {
  font-size: 12px;
  color: #9ca3af;
  font-family: monospace;
}

.status-badge {
  padding: 4px 12px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
}

.status-badge.active {
  background: #dcfce7;
  color: #166534;
}

.status-badge.inactive {
  background: #f3f4f6;
  color: #6b7280;
}

.config-body {
  margin-bottom: 16px;
}

.config-row {
  display: flex;
  justify-content: space-between;
  padding: 8px 0;
}

.config-row .label {
  color: #6b7280;
  font-size: 14px;
}

.config-row .value {
  color: #1f2937;
  font-size: 14px;
  font-weight: 500;
}

.config-row .description {
  max-width: 60%;
  text-align: right;
}

.config-actions {
  display: flex;
  justify-content: flex-end;
}

.btn-edit {
  padding: 8px 16px;
  background: #667eea;
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s;
}

.btn-edit:hover {
  background: #5568d3;
}

/* 模态框样式 */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-content {
  background: white;
  border-radius: 12px;
  padding: 24px;
  width: 90%;
  max-width: 500px;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1);
}

.modal-content h2 {
  font-size: 20px;
  color: #1f2937;
  margin: 0 0 20px 0;
}

.form-group {
  margin-bottom: 16px;
}

.form-group label {
  display: block;
  font-size: 14px;
  color: #374151;
  margin-bottom: 8px;
  font-weight: 500;
}

.input-hint {
  margin: 0;
  font-size: 13px;
  color: #6b7280;
  line-height: 1.5;
}

.form-group input[type="text"],
.form-group input[type="number"],
.form-group select {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 14px;
  background: #fff;
}

.form-group input:disabled {
  background: #f3f4f6;
  color: #6b7280;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}

.checkbox-label input[type="checkbox"] {
  width: 18px;
  height: 18px;
  cursor: pointer;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 24px;
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

.btn-primary:hover {
  background: #5568d3;
}

.btn-secondary {
  background: #f3f4f6;
  color: #374151;
  border: 1px solid #d1d5db;
}

.btn-secondary:hover {
  background: #e5e7eb;
}
</style>
