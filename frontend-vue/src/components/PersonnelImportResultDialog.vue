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
const failedCount = computed(() => Number(summary.value?.failed || 0))
const totalCount = computed(() => Number(summary.value?.total || details.value.length || 0))
const issueCount = computed(() => failedCount.value + skippedCount.value)
const createdDepartmentsTotal = computed(() => Number(summary.value?.created_departments_total || 0))
const createdDepartmentBreakdown = computed(() => ({
  primary: Number(summary.value?.created_primary_departments || 0),
  secondary: Number(summary.value?.created_secondary_departments || 0),
  tertiary: Number(summary.value?.created_tertiary_departments || 0),
}))

const filteredDetails = computed(() => {
  return filterPersonnelImportDetails(details.value, filterStatus.value)
})

const filterOptions = computed(() => [
  { value: 'all', label: '全部', count: details.value.length },
  { value: 'success', label: '成功', count: successCount.value },
  { value: 'failed', label: '失败', count: failedCount.value },
  { value: 'skipped', label: '跳过', count: skippedCount.value },
])

const resultTitle = computed(() => {
  if (failedCount.value > 0) return '导入完成，存在失败记录'
  if (skippedCount.value > 0) return '导入完成，存在跳过记录'
  return '导入完成'
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

function getPersonnelStatusText(status) {
  return {
    active: '正常',
    disabled: '停用',
  }[status] || status || '-'
}

function close() {
  emit('close')
}
</script>

<template>
  <div v-if="show" class="modal-overlay" @click.self="close">
    <div class="modal modal-large">
      <div class="modal-header">
        <div>
          <p class="modal-kicker">批量导入</p>
          <h3>人员导入结果</h3>
        </div>
        <button class="close-btn" type="button" aria-label="关闭" @click="close">×</button>
      </div>

      <div class="modal-body">
        <div class="result-banner" :class="{ warning: issueCount > 0 }">
          <div>
            <p class="result-title">{{ resultTitle }}</p>
            <p class="result-subtitle">
              本次共处理 {{ totalCount }} 条记录，成功 {{ successCount }} 条
              <template v-if="issueCount > 0">，需关注 {{ issueCount }} 条</template>
            </p>
          </div>
          <span class="duration-pill">耗时 {{ duration }}s</span>
        </div>

        <div class="summary-grid">
          <div class="summary-item total">
            <span class="summary-label">总记录数</span>
            <span class="summary-value">{{ totalCount }}</span>
          </div>
          <div class="summary-item success">
            <span class="summary-label">成功</span>
            <span class="summary-value">{{ successCount }}</span>
          </div>
          <div class="summary-item failed">
            <span class="summary-label">失败</span>
            <span class="summary-value">{{ failedCount }}</span>
          </div>
          <div class="summary-item skipped">
            <span class="summary-label">跳过</span>
            <span class="summary-value">{{ skippedCount }}</span>
          </div>
          <div v-if="createdDepartmentsTotal > 0" class="summary-item departments">
            <span class="summary-label">自动创建部门</span>
            <span class="summary-value">{{ createdDepartmentsTotal }}</span>
            <span class="summary-hint">
              一级 {{ createdDepartmentBreakdown.primary }} / 二级 {{ createdDepartmentBreakdown.secondary }} / 三级 {{ createdDepartmentBreakdown.tertiary }}
            </span>
          </div>
        </div>

        <div class="result-toolbar">
          <div class="filter-buttons" aria-label="导入结果筛选">
            <button
              v-for="option in filterOptions"
              :key="option.value"
              class="filter-btn"
              :class="{ active: filterStatus === option.value }"
              type="button"
              @click="filterStatus = option.value"
            >
              <span>{{ option.label }}</span>
              <strong>{{ option.count }}</strong>
            </button>
          </div>
          <p class="result-count">当前显示 {{ filteredDetails.length }} 条</p>
        </div>

        <div class="results-panel">
          <div v-if="filteredDetails.length === 0" class="empty-state">
            <p>暂无匹配记录</p>
          </div>

          <div v-else class="table-scroll">
            <table class="results-table">
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
                  <td class="muted-cell">{{ item.row || index + 1 }}</td>
                  <td class="strong-cell">{{ item.employee_no || '-' }}</td>
                  <td>{{ item.full_name || '-' }}</td>
                  <td class="department-cell">{{ getDepartmentText(item) }}</td>
                  <td>
                    <span class="record-status" :class="item.personnel_record_status || ''">
                      {{ getPersonnelStatusText(item.personnel_record_status) }}
                    </span>
                  </td>
                  <td>
                    <span class="status-badge" :class="getStatusClass(item.status)">
                      {{ getStatusText(item.status) }}
                    </span>
                  </td>
                  <td class="message-cell">{{ item.message || item.reason || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="modal-footer">
        <button class="btn-primary" type="button" @click="close">关闭</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.42);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  z-index: 1000;
}

.modal {
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  width: 100%;
  max-width: 1060px;
  max-height: 90vh;
  overflow: hidden;
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.22);
}

.modal-header,
.modal-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 18px 24px;
  border-bottom: 1px solid #e5e7eb;
}

.modal-header h3 {
  margin: 2px 0 0;
  color: #111827;
  font-size: 20px;
  line-height: 1.3;
}

.modal-kicker {
  margin: 0;
  color: #6b7280;
  font-size: 12px;
}

.modal-footer {
  border-bottom: none;
  border-top: 1px solid #e5e7eb;
  justify-content: flex-end;
  background: #f9fafb;
}

.modal-body {
  max-height: calc(90vh - 137px);
  overflow-y: auto;
  padding: 24px;
  background: #ffffff;
}

.close-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: #6b7280;
  cursor: pointer;
  font-size: 22px;
  line-height: 1;
}

.close-btn:hover {
  background: #f3f4f6;
  border-color: #e5e7eb;
  color: #111827;
}

.result-banner {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  padding: 16px 18px;
  border: 1px solid #bbf7d0;
  border-radius: 8px;
  background: #f0fdf4;
  margin-bottom: 16px;
}

.result-banner.warning {
  background: #fffbeb;
  border-color: #fde68a;
}

.result-title {
  margin: 0;
  color: #111827;
  font-size: 16px;
  font-weight: 700;
}

.result-subtitle {
  margin: 5px 0 0;
  color: #4b5563;
  font-size: 13px;
}

.duration-pill {
  flex: 0 0 auto;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid rgba(148, 163, 184, 0.36);
  color: #334155;
  font-size: 13px;
  padding: 7px 12px;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}

.summary-item {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #ffffff;
  padding: 14px 16px;
}

.summary-label {
  display: block;
  color: #6b7280;
  font-size: 12px;
  margin-bottom: 8px;
}

.summary-value {
  display: block;
  color: #111827;
  font-size: 26px;
  font-weight: 700;
  line-height: 1;
}

.summary-hint {
  display: block;
  margin-top: 8px;
  color: #6b7280;
  font-size: 12px;
  line-height: 1.4;
}

.summary-item.success .summary-value {
  color: #15803d;
}

.summary-item.failed .summary-value {
  color: #dc2626;
}

.summary-item.skipped .summary-value {
  color: #b45309;
}

.summary-item.departments .summary-value {
  color: #0369a1;
}

.result-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.filter-buttons {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.filter-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 1px solid #d1d5db;
  border-radius: 999px;
  background: #ffffff;
  color: #374151;
  cursor: pointer;
  font-size: 13px;
  padding: 8px 12px;
}

.filter-btn strong {
  color: inherit;
  font-size: 12px;
  line-height: 1;
}

.filter-btn:hover {
  background: #f9fafb;
}

.filter-btn.active {
  background: #ecfdf5;
  border-color: #86efac;
  color: #166534;
}

.result-count {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
}

.results-panel {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
}

.table-scroll {
  overflow-x: auto;
}

.results-table {
  width: 100%;
  border-collapse: collapse;
  min-width: 920px;
}

.results-table th,
.results-table td {
  padding: 13px 14px;
  border-bottom: 1px solid #e5e7eb;
  text-align: left;
  vertical-align: middle;
  color: #111827;
  font-size: 14px;
}

.results-table th {
  background: #f9fafb;
  color: #374151;
  font-size: 13px;
  font-weight: 700;
}

.results-table tbody tr:hover {
  background: #f9fafb;
}

.results-table tbody tr:last-child td {
  border-bottom: none;
}

.muted-cell {
  color: #6b7280;
}

.strong-cell {
  font-weight: 600;
}

.department-cell,
.message-cell {
  color: #4b5563;
  max-width: 280px;
  overflow-wrap: anywhere;
}

.status-badge,
.record-status {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  padding: 6px 10px;
  white-space: nowrap;
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

.record-status {
  background: #eef2ff;
  color: #3730a3;
}

.record-status.disabled {
  background: #f3f4f6;
  color: #4b5563;
}

.empty-state {
  padding: 44px 16px;
  text-align: center;
  color: #6b7280;
}

.empty-state p {
  margin: 0;
}

.btn-primary {
  border: 1px solid #0f766e;
  border-radius: 8px;
  background: #0f766e;
  color: #ffffff;
  cursor: pointer;
  font-size: 14px;
  padding: 9px 18px;
}

.btn-primary:hover {
  background: #115e59;
  border-color: #115e59;
}

@media (max-width: 760px) {
  .modal-overlay {
    padding: 12px;
  }

  .modal-header,
  .modal-footer,
  .modal-body {
    padding-left: 16px;
    padding-right: 16px;
  }

  .result-banner,
  .result-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
