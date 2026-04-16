<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  show: Boolean,
  result: {
    type: Object,
    default: () => ({})
  }
})

const emit = defineEmits(['close'])

const filterStatus = ref('all')

const summary = computed(() => props.result?.summary || {})
const details = computed(() => props.result?.details || [])
const duration = computed(() => props.result?.duration || 0)

const filteredDetails = computed(() => {
  if (filterStatus.value === 'all') {
    return details.value
  }
  return details.value.filter(item => item.status === filterStatus.value)
})

function getStatusClass(status) {
  const classes = {
    success: 'status-success',
    failed: 'status-failed',
    skipped: 'status-skipped'
  }
  return classes[status] || ''
}

function getStatusText(status) {
  const texts = {
    success: '成功',
    failed: '失败',
    skipped: '跳过'
  }
  return texts[status] || status
}

function downloadFailedRecords() {
  const failedRecords = details.value.filter(item => item.status === 'failed' || item.status === 'skipped')
  if (failedRecords.length === 0) {
    return
  }

  const headers = ['行号', '一级部门', '一级状态', '二级部门', '二级状态', '结果', '消息']
  const rows = failedRecords.map(record => [
    record.row ?? '',
    record.primary_department_name || '',
    record.primary_status || '',
    record.secondary_department_name || '',
    record.secondary_status || '',
    getStatusText(record.status),
    record.message || record.reason || ''
  ])
  const csvContent = [
    headers.join(','),
    ...rows.map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(','))
  ].join('\n')

  const blob = new Blob([`\ufeff${csvContent}`], { type: 'text/csv;charset=utf-8;' })
  const objectUrl = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = `department_import_failed_records_${Date.now()}.csv`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(objectUrl)
}

function close() {
  emit('close')
}
</script>

<template>
  <div v-if="show" class="modal-overlay" @click.self="close">
    <div class="modal modal-large">
      <div class="modal-header">
        <h3>部门导入结果</h3>
        <button class="close-btn" @click="close">x</button>
      </div>

      <div class="modal-body">
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

        <div class="filter-section">
          <div class="filter-buttons">
            <button class="filter-btn" :class="{ active: filterStatus === 'all' }" @click="filterStatus = 'all'">
              全部 ({{ details.length }})
            </button>
            <button class="filter-btn" :class="{ active: filterStatus === 'success' }" @click="filterStatus = 'success'">
              成功 ({{ summary.success || 0 }})
            </button>
            <button class="filter-btn" :class="{ active: filterStatus === 'failed' }" @click="filterStatus = 'failed'">
              失败 ({{ summary.failed || 0 }})
            </button>
            <button class="filter-btn" :class="{ active: filterStatus === 'skipped' }" @click="filterStatus = 'skipped'">
              跳过 ({{ summary.skipped || 0 }})
            </button>
          </div>

          <button
            v-if="(summary.failed || 0) + (summary.skipped || 0) > 0"
            class="download-btn"
            @click="downloadFailedRecords"
          >
            下载失败记录
          </button>
        </div>

        <div class="results-section">
          <div v-if="filteredDetails.length === 0" class="empty-state">
            <p>暂无数据</p>
          </div>

          <table v-else class="results-table">
            <thead>
              <tr>
                <th>行号</th>
                <th>一级部门</th>
                <th>一级状态</th>
                <th>二级部门</th>
                <th>二级状态</th>
                <th>结果</th>
                <th>消息</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(item, index) in filteredDetails"
                :key="`${item.row || index}-${item.primary_department_name || ''}-${item.secondary_department_name || ''}`"
              >
                <td>{{ item.row || index + 1 }}</td>
                <td>{{ item.primary_department_name || '-' }}</td>
                <td>{{ item.primary_status || '-' }}</td>
                <td>{{ item.secondary_department_name || '-' }}</td>
                <td>{{ item.secondary_status || '-' }}</td>
                <td>
                  <span class="status-badge" :class="getStatusClass(item.status)">
                    {{ getStatusText(item.status) }}
                  </span>
                </td>
                <td>{{ item.message || item.reason || '' }}</td>
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
  inset: 0;
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
  max-width: 1100px;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px;
  border-bottom: 1px solid #e5e7eb;
}

.modal-header h3 {
  margin: 0;
  color: #1f2937;
  font-size: 18px;
}

.close-btn {
  background: none;
  border: none;
  font-size: 24px;
  color: #9ca3af;
  cursor: pointer;
  width: 32px;
  height: 32px;
  border-radius: 6px;
}

.close-btn:hover {
  background: #f3f4f6;
  color: #374151;
}

.modal-body {
  padding: 24px;
}

.summary-section {
  margin-bottom: 24px;
}

.summary-card {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
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

.summary-item.failed .summary-value {
  color: #dc2626;
}

.summary-item.skipped .summary-value {
  color: #d97706;
}

.filter-section {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
}

.filter-buttons {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.filter-btn,
.download-btn,
.btn-primary {
  border: 1px solid #d1d5db;
  border-radius: 8px;
  background: white;
  color: #374151;
  cursor: pointer;
  padding: 8px 14px;
}

.filter-btn.active {
  background: #eef2ff;
  border-color: #c7d2fe;
  color: #4338ca;
}

.download-btn {
  background: #f0fdf4;
  border-color: #bbf7d0;
  color: #166534;
}

.results-table {
  width: 100%;
  border-collapse: collapse;
}

.results-table th,
.results-table td {
  padding: 12px 10px;
  border-bottom: 1px solid #e5e7eb;
  text-align: left;
  font-size: 14px;
  color: #1f2937;
  vertical-align: top;
}

.results-table th {
  background: #f9fafb;
  font-weight: 600;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
}

.status-success {
  background: #dcfce7;
  color: #166534;
}

.status-failed {
  background: #fee2e2;
  color: #b91c1c;
}

.status-skipped {
  background: #fef3c7;
  color: #b45309;
}

.empty-state {
  padding: 32px 0;
  text-align: center;
  color: #6b7280;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  padding: 20px 24px;
  border-top: 1px solid #e5e7eb;
}

.btn-primary {
  background: #0f766e;
  border-color: #0f766e;
  color: white;
}

@media (max-width: 900px) {
  .summary-card {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .filter-section {
    flex-direction: column;
    align-items: stretch;
  }

  .results-section {
    overflow-x: auto;
  }

  .results-table {
    min-width: 760px;
  }
}
</style>
