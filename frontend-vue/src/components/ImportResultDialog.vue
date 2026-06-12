<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  show: Boolean,
  result: Object,
  title: {
    type: String,
    default: '操作结果'
  }
})

const emit = defineEmits(['close'])

const filterStatus = ref('all')

// 统计信息
const summary = computed(() => props.result?.summary || {})
const details = computed(() => props.result?.details || [])
const duration = computed(() => props.result?.duration || 0)
const updatedCount = computed(() => Number(summary.value?.updated || 0))
const resultColumns = computed(() => {
  const sample = details.value.find(item => item && typeof item === 'object') || {}
  if ('department_name' in sample || 'level_name' in sample || 'level' in sample) {
    return [
      { key: 'row', label: '序号', getValue: (item, index) => item.row ?? index + 1 },
      { key: 'level_name', label: '层级', getValue: item => item.level_name || getDepartmentLevelText(item.level) },
      { key: 'department_name', label: '部门', getValue: item => item.department_name || '-' },
      { key: 'status', label: '状态', type: 'status', getValue: item => item.status },
      { key: 'message', label: '消息', getValue: item => item.message || item.reason || '' },
      { key: 'id', label: '部门ID', getValue: item => item.id || '-' }
    ]
  }
  if ('employee_no' in sample || 'full_name' in sample || 'personnel_id' in sample) {
    return [
      { key: 'row', label: '序号', getValue: (item, index) => item.row ?? index + 1 },
      { key: 'employee_no', label: '工号', getValue: item => item.employee_no || '-' },
      { key: 'full_name', label: '姓名', getValue: item => item.full_name || '-' },
      { key: 'status', label: '状态', type: 'status', getValue: item => item.status },
      { key: 'message', label: '消息', getValue: item => item.message || item.reason || '' },
      { key: 'personnel_id', label: '人员ID', getValue: item => item.personnel_id || '-' }
    ]
  }
  return [
    { key: 'row', label: '序号', getValue: (item, index) => item.row ?? item.user_id ?? index + 1 },
    { key: 'username', label: '用户名', getValue: item => item.username || '-' },
    { key: 'status', label: '状态', type: 'status', getValue: item => item.status },
    { key: 'message', label: '消息', getValue: item => item.message || item.reason || '' },
    { key: 'user_id', label: '用户ID', getValue: item => item.user_id || '-' }
  ]
})

// 筛选后的详细结果
const filteredDetails = computed(() => {
  if (filterStatus.value === 'all') {
    return details.value
  }
  return details.value.filter(item => item.status === filterStatus.value)
})

// 状态标签样式
function getStatusClass(status) {
  const classes = {
    success: 'status-success',
    updated: 'status-updated',
    failed: 'status-failed',
    skipped: 'status-skipped'
  }
  return classes[status] || ''
}

// 状态文本
function getStatusText(status) {
  const texts = {
    success: '成功',
    updated: '更新',
    failed: '失败',
    skipped: '跳过'
  }
  return texts[status] || status
}

function getDepartmentLevelText(level) {
  const texts = {
    primary: '一级部门',
    secondary: '二级部门',
    tertiary: '三级部门'
  }
  return texts[level] || level || '-'
}

function getColumnValue(item, column, index) {
  return column.getValue ? column.getValue(item, index) : item[column.key]
}

function getDetailKey(item, index) {
  return [
    item.row,
    item.user_id,
    item.personnel_id,
    item.id,
    item.username,
    item.employee_no,
    item.department_name,
    index
  ].filter(value => value !== undefined && value !== null && value !== '').join('-')
}

function escapeCsvCell(value) {
  return `"${String(value ?? '').replace(/"/g, '""')}"`
}

// 下载失败记录
function downloadFailedRecords() {
  const failedRecords = details.value.filter(item => item.status === 'failed' || item.status === 'skipped')
  
  if (failedRecords.length === 0) {
    return
  }
  
  // 生成CSV内容
  const headers = resultColumns.value.map(column => column.label)
  const rows = failedRecords.map((record, index) => resultColumns.value.map((column) => {
    if (column.type === 'status') {
      return getStatusText(record.status)
    }
    return getColumnValue(record, column, index)
  }))
  
  const csvContent = [
    headers.join(','),
    ...rows.map(row => row.map(escapeCsvCell).join(','))
  ].join('\n')
  
  // 下载文件
  const blob = new Blob(['\ufeff' + csvContent], { type: 'text/csv;charset=utf-8;' })
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `import_failed_records_${Date.now()}.csv`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  window.URL.revokeObjectURL(url)
}

// 关闭对话框
function close() {
  emit('close')
}
</script>

<template>
  <div v-if="show" class="modal-overlay" @click.self="close">
    <div class="modal modal-large">
      <div class="modal-header">
        <h3>{{ title }}</h3>
        <button class="close-btn" @click="close">×</button>
      </div>

      <div class="modal-body">
        <!-- 统计信息 -->
        <div class="summary-section">
          <div class="summary-card">
            <div class="summary-item">
              <span class="summary-label">总记录数</span>
              <span class="summary-value">{{ summary.total || 0 }}</span>
            </div>
            <div class="summary-item success">
              <span class="summary-label">成功</span>
              <span class="summary-value">{{ summary.success || 0 }}</span>
            </div>
            <div class="summary-item updated">
              <span class="summary-label">更新</span>
              <span class="summary-value">{{ updatedCount }}</span>
            </div>
            <div class="summary-item failed">
              <span class="summary-label">失败</span>
              <span class="summary-value">{{ summary.failed || 0 }}</span>
            </div>
            <div class="summary-item skipped">
              <span class="summary-label">跳过</span>
              <span class="summary-value">{{ summary.skipped || 0 }}</span>
            </div>
            <div class="summary-item">
              <span class="summary-label">耗时</span>
              <span class="summary-value">{{ duration }}s</span>
            </div>
          </div>
        </div>

        <!-- 筛选器 -->
        <div class="filter-section">
          <div class="filter-buttons">
            <button 
              class="filter-btn" 
              :class="{ active: filterStatus === 'all' }"
              @click="filterStatus = 'all'"
            >
              全部 ({{ details.length }})
            </button>
            <button 
              class="filter-btn" 
              :class="{ active: filterStatus === 'success' }"
              @click="filterStatus = 'success'"
            >
              成功 ({{ summary.success || 0 }})
            </button>
            <button
              v-if="updatedCount > 0"
              class="filter-btn"
              :class="{ active: filterStatus === 'updated' }"
              @click="filterStatus = 'updated'"
            >
              更新 ({{ updatedCount }})
            </button>
            <button 
              class="filter-btn" 
              :class="{ active: filterStatus === 'failed' }"
              @click="filterStatus = 'failed'"
            >
              失败 ({{ summary.failed || 0 }})
            </button>
            <button 
              class="filter-btn" 
              :class="{ active: filterStatus === 'skipped' }"
              @click="filterStatus = 'skipped'"
            >
              跳过 ({{ summary.skipped || 0 }})
            </button>
          </div>
          
          <button 
            v-if="(summary.failed || 0) + (summary.skipped || 0) > 0"
            class="download-btn"
            @click="downloadFailedRecords"
          >
            <span class="icon">⬇️</span>
            下载失败记录
          </button>
        </div>

        <!-- 详细结果列表 -->
        <div class="results-section">
          <div v-if="filteredDetails.length === 0" class="empty-state">
            <p>暂无数据</p>
          </div>
          
          <table v-else class="results-table">
            <thead>
              <tr>
                <th v-for="column in resultColumns" :key="column.key">{{ column.label }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, index) in filteredDetails" :key="getDetailKey(item, index)">
                <td v-for="column in resultColumns" :key="column.key">
                  <span v-if="column.type === 'status'" class="status-badge" :class="getStatusClass(item.status)">
                    {{ getStatusText(getColumnValue(item, column, index)) }}
                  </span>
                  <template v-else>
                    {{ getColumnValue(item, column, index) }}
                  </template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="modal-footer">
        <button class="btn-primary" @click="close">关闭</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
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

.modal {
  background: white;
  border-radius: 12px;
  width: 100%;
  max-width: 600px;
  max-height: 90vh;
  overflow-y: auto;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1);
}

.modal-large {
  max-width: 900px;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px;
  border-bottom: 1px solid #e5e7eb;
}

.modal-header h3 {
  font-size: 18px;
  color: #1f2937;
  margin: 0;
}

.close-btn {
  background: none;
  border: none;
  font-size: 28px;
  color: #9ca3af;
  cursor: pointer;
  padding: 0;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
}

.close-btn:hover {
  background: #f3f4f6;
  color: #374151;
}

.modal-body {
  padding: 24px;
}

/* 统计信息 */
.summary-section {
  margin-bottom: 24px;
}

.summary-card {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 16px;
  background: #f9fafb;
  padding: 20px;
  border-radius: 8px;
}

.summary-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.summary-label {
  font-size: 12px;
  color: #6b7280;
}

.summary-value {
  font-size: 24px;
  font-weight: 600;
  color: #1f2937;
}

.summary-item.success .summary-value {
  color: #16a34a;
}

.summary-item.updated .summary-value {
  color: #2563eb;
}

.summary-item.failed .summary-value {
  color: #dc2626;
}

.summary-item.skipped .summary-value {
  color: #f59e0b;
}

/* 筛选器 */
.filter-section {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  gap: 16px;
}

.filter-buttons {
  display: flex;
  gap: 8px;
  flex: 1;
}

.filter-btn {
  padding: 8px 16px;
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  font-size: 13px;
  color: #374151;
  cursor: pointer;
  transition: all 0.2s;
}

.filter-btn:hover {
  background: #f9fafb;
  border-color: #d1d5db;
}

.filter-btn.active {
  background: #667eea;
  color: white;
  border-color: #667eea;
}

.download-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: #f3f4f6;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 13px;
  color: #374151;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.download-btn:hover {
  background: #e5e7eb;
}

.download-btn .icon {
  font-size: 14px;
}

/* 结果列表 */
.results-section {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
  max-height: 400px;
  overflow-y: auto;
}

.empty-state {
  padding: 40px;
  text-align: center;
  color: #9ca3af;
}

.results-table {
  width: 100%;
  border-collapse: collapse;
}

.results-table th,
.results-table td {
  padding: 12px;
  text-align: left;
  border-bottom: 1px solid #e5e7eb;
}

.results-table th {
  background: #f9fafb;
  font-weight: 500;
  color: #374151;
  font-size: 13px;
  position: sticky;
  top: 0;
  z-index: 10;
}

.results-table td {
  color: #1f2937;
  font-size: 13px;
}

.results-table tbody tr:last-child td {
  border-bottom: none;
}

.results-table tbody tr:hover {
  background: #f9fafb;
}

.status-badge {
  display: inline-block;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
}

.status-success {
  background: #dcfce7;
  color: #166534;
}

.status-updated {
  background: #dbeafe;
  color: #1d4ed8;
}

.status-failed {
  background: #fee2e2;
  color: #dc2626;
}

.status-skipped {
  background: #fef3c7;
  color: #92400e;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 16px 24px;
  border-top: 1px solid #e5e7eb;
}

.btn-primary {
  padding: 10px 24px;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
  border: none;
  background: #667eea;
  color: white;
  transition: all 0.2s;
}

.btn-primary:hover {
  background: #5a67d8;
}
</style>
