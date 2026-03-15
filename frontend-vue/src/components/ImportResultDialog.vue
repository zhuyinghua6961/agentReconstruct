<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  show: Boolean,
  result: Object
})

const emit = defineEmits(['close'])

const filterStatus = ref('all')

// 统计信息
const summary = computed(() => props.result?.summary || {})
const details = computed(() => props.result?.details || [])
const duration = computed(() => props.result?.duration || 0)

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
    failed: 'status-failed',
    skipped: 'status-skipped'
  }
  return classes[status] || ''
}

// 状态文本
function getStatusText(status) {
  const texts = {
    success: '成功',
    failed: '失败',
    skipped: '跳过'
  }
  return texts[status] || status
}

// 下载失败记录
function downloadFailedRecords() {
  const failedRecords = details.value.filter(item => item.status === 'failed' || item.status === 'skipped')
  
  if (failedRecords.length === 0) {
    return
  }
  
  // 生成CSV内容
  const headers = ['行号', '用户名', '状态', '错误信息']
  const rows = failedRecords.map(record => [
    record.row,
    record.username,
    getStatusText(record.status),
    record.message
  ])
  
  const csvContent = [
    headers.join(','),
    ...rows.map(row => row.map(cell => `"${cell}"`).join(','))
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
        <h3>导入结果</h3>
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
                <th>行号</th>
                <th>用户名</th>
                <th>状态</th>
                <th>消息</th>
                <th>用户ID</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in filteredDetails" :key="item.row">
                <td>{{ item.row }}</td>
                <td>{{ item.username }}</td>
                <td>
                  <span class="status-badge" :class="getStatusClass(item.status)">
                    {{ getStatusText(item.status) }}
                  </span>
                </td>
                <td>{{ item.message }}</td>
                <td>{{ item.user_id || '-' }}</td>
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
  grid-template-columns: repeat(5, 1fr);
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
