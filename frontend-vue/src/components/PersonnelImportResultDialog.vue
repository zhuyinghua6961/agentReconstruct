<script setup>
import { computed, ref } from 'vue'
import {
  filterPersonnelImportDetails,
  getPersonnelImportResultText,
  getPersonnelImportSkippedCount,
  getPersonnelImportStatusClass,
  getPersonnelImportSuccessCount,
} from '../utils/personnelImportResult'

const props = defineProps({
  show: Boolean,
  result: {
    type: Object,
    default: () => ({}),
  },
})

const emit = defineEmits(['close'])
const filterStatus = ref('all')

const summary = computed(() => props.result?.summary || {})
const details = computed(() => props.result?.details || [])
const duration = computed(() => props.result?.duration || 0)
const successCount = computed(() => getPersonnelImportSuccessCount(summary.value))
const skippedCount = computed(() => getPersonnelImportSkippedCount(summary.value))

const filteredDetails = computed(() => {
  return filterPersonnelImportDetails(details.value, filterStatus.value)
})

function getStatusClass(status) {
  return getPersonnelImportStatusClass(status)
}

function getStatusText(status) {
  return getPersonnelImportResultText(status)
}

function getDepartmentText(item) {
  if (item?.department_display) {
    return item.department_display
  }
  const parts = [
    item?.primary_department_name,
    item?.secondary_department_name,
    item?.tertiary_department_name,
  ].filter(Boolean)
  return parts.length ? parts.join(' / ') : '-'
}

function close() {
  emit('close')
}
</script>

<template>
  <div v-if="show" class="modal-overlay" @click.self="close">
    <div class="modal modal-large">
      <div class="modal-header">
        <h3>人员导入结果</h3>
        <button class="close-btn" @click="close">x</button>
      </div>

      <div class="modal-body">
        <div class="summary-card">
          <div class="summary-item">
            <span class="summary-label">总记录数</span>
            <span class="summary-value">{{ summary.total || 0 }}</span>
          </div>
          <div class="summary-item">
            <span class="summary-label">成功</span>
            <span class="summary-value">{{ successCount }}</span>
          </div>
          <div class="summary-item">
            <span class="summary-label">失败</span>
            <span class="summary-value">{{ summary.failed || 0 }}</span>
          </div>
          <div class="summary-item">
            <span class="summary-label">跳过</span>
            <span class="summary-value">{{ skippedCount }}</span>
          </div>
          <div class="summary-item">
            <span class="summary-label">耗时</span>
            <span class="summary-value">{{ duration }}s</span>
          </div>
        </div>

        <div class="filter-buttons">
          <button class="filter-btn" :class="{ active: filterStatus === 'all' }" @click="filterStatus = 'all'">全部</button>
          <button class="filter-btn" :class="{ active: filterStatus === 'success' }" @click="filterStatus = 'success'">成功</button>
          <button class="filter-btn" :class="{ active: filterStatus === 'failed' }" @click="filterStatus = 'failed'">失败</button>
          <button class="filter-btn" :class="{ active: filterStatus === 'skipped' }" @click="filterStatus = 'skipped'">跳过</button>
        </div>

        <table v-if="filteredDetails.length" class="results-table">
          <thead>
            <tr>
              <th>行号</th>
              <th>工号</th>
              <th>姓名</th>
              <th>部门</th>
              <th>状态</th>
              <th>结果</th>
              <th>消息</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(item, index) in filteredDetails" :key="`${item.row || index}-${item.employee_no || ''}`">
              <td>{{ item.row || index + 1 }}</td>
              <td>{{ item.employee_no || '-' }}</td>
              <td>{{ item.full_name || '-' }}</td>
              <td>{{ getDepartmentText(item) }}</td>
              <td>{{ item.personnel_record_status || '-' }}</td>
              <td>
                <span class="status-badge" :class="getStatusClass(item.status)">
                  {{ getStatusText(item.status) }}
                </span>
              </td>
              <td>{{ item.message || item.reason || '' }}</td>
            </tr>
          </tbody>
        </table>

        <div v-else class="empty-state">暂无数据</div>
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
  max-width: 1000px;
  max-height: 90vh;
  overflow-y: auto;
}

.modal-header,
.modal-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px;
  border-bottom: 1px solid #e5e7eb;
}

.modal-footer {
  border-bottom: none;
  border-top: 1px solid #e5e7eb;
  justify-content: flex-end;
}

.modal-body {
  padding: 24px;
}

.summary-card,
.filter-buttons {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.results-table {
  width: 100%;
  border-collapse: collapse;
}

.results-table th,
.results-table td {
  padding: 10px 12px;
  border-bottom: 1px solid #e5e7eb;
  text-align: left;
}

.empty-state {
  color: #6b7280;
}
</style>
