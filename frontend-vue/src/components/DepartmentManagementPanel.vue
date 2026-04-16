<script setup>
import { onMounted, ref } from 'vue'
import { adminApi } from '../services/admin'
import DepartmentBatchImportDialog from './DepartmentBatchImportDialog.vue'
import DepartmentImportResultDialog from './DepartmentImportResultDialog.vue'

const emit = defineEmits(['updated'])

const loading = ref(false)
const error = ref('')
const success = ref('')
const departmentTree = ref([])
const newPrimaryName = ref('')
const secondaryDrafts = ref({})
const showDepartmentImportDialog = ref(false)
const showDepartmentImportResultDialog = ref(false)
const departmentImportResult = ref(null)

function setSuccess(message) {
  success.value = message
  error.value = ''
  setTimeout(() => {
    success.value = ''
  }, 3000)
}

async function fetchDepartmentTree() {
  loading.value = true
  const result = await adminApi.getDepartmentTree()
  if (result.success) {
    departmentTree.value = Array.isArray(result.data?.items) ? result.data.items : []
    error.value = ''
  } else {
    error.value = result.error || '获取部门列表失败'
  }
  loading.value = false
}

async function handleCreatePrimary() {
  const name = String(newPrimaryName.value || '').trim()
  if (!name) {
    error.value = '请输入一级部门名称'
    return
  }
  const result = await adminApi.createPrimaryDepartment(name)
  if (result.success) {
    newPrimaryName.value = ''
    setSuccess('一级部门创建成功')
    await fetchDepartmentTree()
    emit('updated')
    return
  }
  error.value = result.error || '创建一级部门失败'
}

async function handleRenamePrimary(item) {
  const nextName = window.prompt('请输入新的一级部门名称：', item.name)
  if (!nextName || nextName.trim() === item.name) {
    return
  }
  const result = await adminApi.renamePrimaryDepartment(item.id, nextName.trim())
  if (result.success) {
    setSuccess('一级部门已更新')
    await fetchDepartmentTree()
    emit('updated')
    return
  }
  error.value = result.error || '更新一级部门失败'
}

async function handleTogglePrimaryStatus(item) {
  const targetStatus = item.status === 'active' ? 'disabled' : 'active'
  const actionText = targetStatus === 'disabled' ? '停用' : '启用'
  if (!window.confirm(`确定要${actionText}一级部门 ${item.name} 吗？`)) {
    return
  }
  const result = await adminApi.updatePrimaryDepartmentStatus(item.id, targetStatus)
  if (result.success) {
    setSuccess(`一级部门已${actionText}`)
    await fetchDepartmentTree()
    emit('updated')
    return
  }
  error.value = result.error || `一级部门${actionText}失败`
}

async function handleCreateSecondary(primary) {
  const name = String(secondaryDrafts.value[primary.id] || '').trim()
  if (!name) {
    error.value = '请输入二级部门名称'
    return
  }
  const result = await adminApi.createSecondaryDepartment(primary.id, name)
  if (result.success) {
    secondaryDrafts.value = {
      ...secondaryDrafts.value,
      [primary.id]: '',
    }
    setSuccess('二级部门创建成功')
    await fetchDepartmentTree()
    emit('updated')
    return
  }
  error.value = result.error || '创建二级部门失败'
}

async function handleRenameSecondary(secondary) {
  const nextName = window.prompt('请输入新的二级部门名称：', secondary.name)
  if (!nextName || nextName.trim() === secondary.name) {
    return
  }
  const result = await adminApi.renameSecondaryDepartment(secondary.id, nextName.trim())
  if (result.success) {
    setSuccess('二级部门已更新')
    await fetchDepartmentTree()
    emit('updated')
    return
  }
  error.value = result.error || '更新二级部门失败'
}

async function handleToggleSecondaryStatus(secondary) {
  const targetStatus = secondary.status === 'active' ? 'disabled' : 'active'
  const actionText = targetStatus === 'disabled' ? '停用' : '启用'
  if (!window.confirm(`确定要${actionText}二级部门 ${secondary.name} 吗？`)) {
    return
  }
  const result = await adminApi.updateSecondaryDepartmentStatus(secondary.id, targetStatus)
  if (result.success) {
    setSuccess(`二级部门已${actionText}`)
    await fetchDepartmentTree()
    emit('updated')
    return
  }
  error.value = result.error || `二级部门${actionText}失败`
}

async function handleDepartmentImportSuccess(result) {
  const summary = result?.summary || {}
  departmentImportResult.value = result
  showDepartmentImportResultDialog.value = true
  setSuccess(
    `部门导入完成：成功 ${summary.success || 0} 条，失败 ${summary.failed || 0} 条，跳过 ${summary.skipped || 0} 条`
  )
  await fetchDepartmentTree()
  emit('updated')
}

onMounted(() => {
  fetchDepartmentTree()
})
</script>

<template>
  <section class="department-panel">
    <div class="panel-header">
      <div>
        <h2>部门管理</h2>
        <p>维护一级、二级部门字典。停用后不会清空已绑定用户，只会禁止后续新选择。</p>
      </div>
      <div class="panel-actions">
        <button class="refresh-btn" @click="fetchDepartmentTree">刷新</button>
        <button class="import-btn" @click="showDepartmentImportDialog = true">批量导入部门</button>
      </div>
    </div>

    <div v-if="success" class="alert alert-success">{{ success }}</div>
    <div v-if="error" class="alert alert-error">{{ error }}</div>

    <div class="create-primary">
      <input v-model="newPrimaryName" type="text" placeholder="新增一级部门，例如：计算机学院">
      <button class="primary-btn" @click="handleCreatePrimary">新增一级部门</button>
    </div>

    <div v-if="loading" class="loading">加载中...</div>

    <div v-else class="department-tree">
      <div
        v-for="primary in departmentTree"
        :key="primary.id"
        class="primary-card"
      >
        <div class="primary-header">
          <div class="title-group">
            <h3>{{ primary.name }}</h3>
            <span class="status-badge" :class="primary.status">
              {{ primary.status === 'active' ? '启用中' : '已停用' }}
            </span>
          </div>
          <div class="actions">
            <button class="action-btn" @click="handleRenamePrimary(primary)">改名</button>
            <button class="action-btn" @click="handleTogglePrimaryStatus(primary)">
              {{ primary.status === 'active' ? '停用' : '启用' }}
            </button>
          </div>
        </div>

        <div class="create-secondary">
          <input
            v-model="secondaryDrafts[primary.id]"
            type="text"
            :placeholder="`在 ${primary.name} 下新增二级部门`"
          >
          <button class="secondary-btn" @click="handleCreateSecondary(primary)">新增二级部门</button>
        </div>

        <div v-if="primary.secondary_items?.length" class="secondary-list">
          <div
            v-for="secondary in primary.secondary_items"
            :key="secondary.id"
            class="secondary-item"
          >
            <div class="secondary-main">
              <span class="secondary-name">{{ secondary.name }}</span>
              <span class="status-badge" :class="secondary.effective_status">
                {{ secondary.effective_status === 'active' ? '可选' : '已停用' }}
              </span>
            </div>
            <div class="secondary-meta">
              <span v-if="secondary.effective_status !== secondary.status" class="meta-text">
                子级存储状态 {{ secondary.status === 'active' ? '启用' : '停用' }}，当前随一级部门禁用
              </span>
              <div class="actions">
                <button class="action-btn" @click="handleRenameSecondary(secondary)">改名</button>
                <button class="action-btn" @click="handleToggleSecondaryStatus(secondary)">
                  {{ secondary.status === 'active' ? '停用' : '启用' }}
                </button>
              </div>
            </div>
          </div>
        </div>

        <p v-else class="empty-secondary">暂无二级部门</p>
      </div>
    </div>

    <DepartmentBatchImportDialog
      :show="showDepartmentImportDialog"
      @close="showDepartmentImportDialog = false"
      @import-success="handleDepartmentImportSuccess"
    />
    <DepartmentImportResultDialog
      :show="showDepartmentImportResultDialog"
      :result="departmentImportResult"
      @close="showDepartmentImportResultDialog = false"
    />
  </section>
</template>

<style scoped>
.department-panel {
  background: white;
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 24px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.panel-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.panel-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.panel-header h2 {
  margin: 0 0 8px 0;
  color: #1f2937;
}

.panel-header p {
  margin: 0;
  font-size: 14px;
  color: #6b7280;
}

.refresh-btn,
.primary-btn,
.import-btn,
.secondary-btn,
.action-btn {
  border: 1px solid #d1d5db;
  border-radius: 8px;
  background: white;
  color: #1f2937;
  cursor: pointer;
  padding: 8px 14px;
}

.primary-btn,
.secondary-btn {
  background: #667eea;
  border-color: #667eea;
  color: white;
}

.import-btn {
  background: #0f766e;
  border-color: #0f766e;
  color: white;
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

.create-primary,
.create-secondary {
  display: flex;
  gap: 12px;
}

.create-primary {
  margin-bottom: 20px;
}

.create-primary input,
.create-secondary input {
  flex: 1;
  padding: 10px 12px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 14px;
}

.department-tree {
  display: grid;
  gap: 16px;
}

.primary-card {
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 16px;
  background: #fafafa;
}

.primary-header,
.secondary-meta,
.secondary-main {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
}

.primary-header {
  margin-bottom: 14px;
}

.title-group {
  display: flex;
  align-items: center;
  gap: 10px;
}

.title-group h3,
.secondary-name {
  margin: 0;
  color: #1f2937;
}

.status-badge {
  border-radius: 999px;
  padding: 4px 10px;
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

.secondary-list {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.secondary-item {
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 12px;
  background: white;
}

.meta-text,
.empty-secondary,
.loading {
  color: #6b7280;
  font-size: 13px;
}

.loading {
  padding: 20px 0;
}

@media (max-width: 768px) {
  .panel-header,
  .panel-actions,
  .create-primary,
  .create-secondary,
  .primary-header,
  .secondary-meta {
    flex-direction: column;
    align-items: stretch;
  }

  .title-group,
  .secondary-main {
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}
</style>
