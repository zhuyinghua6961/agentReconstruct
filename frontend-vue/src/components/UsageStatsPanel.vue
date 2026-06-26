<script setup>
import { computed, onMounted, ref } from 'vue'
import DepartmentSelector from './DepartmentSelector.vue'
import { adminApi } from '../services/admin'

const loading = ref(false)
const exporting = ref(false)
const error = ref('')
const rows = ref([])
const pagination = ref({ page: 1, pageSize: 20, total: 0 })

const keyword = ref('')
const fromDate = ref('')
const toDate = ref('')
const primaryDepartmentId = ref(null)
const secondaryDepartmentId = ref(null)
const tertiaryDepartmentId = ref(null)
const departmentTree = ref([])
const departmentTreeLoading = ref(false)
const sortBy = ref('last_active_at')
const sortOrder = ref('desc')

const sortOptions = [
  { value: 'last_active_at', label: '最后活跃' },
  { value: 'ask_total', label: '问答合计' },
  { value: 'ask_query_count', label: '普通问答' },
  { value: 'file_qa_count', label: '文件问答' },
  { value: 'literature_search_count', label: '文献检索' },
  { value: 'patent_search_count', label: '专利检索' },
  { value: 'active_seconds', label: '活跃使用' },
  { value: 'username', label: '账号' },
]

function defaultDateRange() {
  const end = new Date()
  const start = new Date()
  start.setDate(end.getDate() - 6)
  const toIso = (value) => value.toISOString().slice(0, 10)
  fromDate.value = toIso(start)
  toDate.value = toIso(end)
}

function formatDuration(seconds) {
  const total = Math.max(0, Number(seconds) || 0)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (hours > 0) {
    return `${hours}小时${minutes}分`
  }
  return `${minutes}分`
}

function formatDateTime(value) {
  if (!value) return '-'
  const text = String(value)
  return text.replace('T', ' ').slice(0, 19)
}

const totalPages = computed(() => {
  const total = Number(pagination.value.total || 0)
  const pageSize = Number(pagination.value.pageSize || 20)
  return Math.max(1, Math.ceil(total / pageSize))
})

function currentQueryParams() {
  return {
    from: fromDate.value,
    to: toDate.value,
    keyword: keyword.value.trim(),
    primary_department_id: primaryDepartmentId.value,
    secondary_department_id: secondaryDepartmentId.value,
    tertiary_department_id: tertiaryDepartmentId.value,
    sort_by: sortBy.value,
    sort_order: sortOrder.value,
  }
}

async function fetchDepartmentTree() {
  departmentTreeLoading.value = true
  try {
    const result = await adminApi.getDepartmentTree()
    departmentTree.value = result.success && Array.isArray(result.data) ? result.data : []
  } catch {
    departmentTree.value = []
  } finally {
    departmentTreeLoading.value = false
  }
}

async function fetchStats(page = pagination.value.page) {
  loading.value = true
  error.value = ''
  try {
    const result = await adminApi.getUsageStats({
      ...currentQueryParams(),
      page,
      page_size: pagination.value.pageSize,
    })
    if (!result.success) {
      error.value = result.error || '加载统计数据失败'
      rows.value = []
      pagination.value = { ...pagination.value, page, total: 0 }
      return
    }
    rows.value = Array.isArray(result.data) ? result.data : []
    const pageInfo = result.pagination || {}
    pagination.value = {
      page: Number(pageInfo.page || page),
      pageSize: Number(pageInfo.page_size || pagination.value.pageSize),
      total: Number(pageInfo.total || 0),
    }
  } catch (err) {
    error.value = err?.message || '加载统计数据失败'
    rows.value = []
  } finally {
    loading.value = false
  }
}

async function handleExport(format) {
  exporting.value = true
  error.value = ''
  try {
    await adminApi.exportUsageStats({
      ...currentQueryParams(),
      format,
    })
  } catch (err) {
    error.value = err?.message || '导出失败'
  } finally {
    exporting.value = false
  }
}

function handleSearch() {
  pagination.value.page = 1
  void fetchStats(1)
}

function handleReset() {
  keyword.value = ''
  primaryDepartmentId.value = null
  secondaryDepartmentId.value = null
  tertiaryDepartmentId.value = null
  sortBy.value = 'last_active_at'
  sortOrder.value = 'desc'
  defaultDateRange()
  pagination.value.page = 1
  void fetchStats(1)
}

function toggleSortOrder() {
  sortOrder.value = sortOrder.value === 'desc' ? 'asc' : 'desc'
  pagination.value.page = 1
  void fetchStats(1)
}

function goPrevPage() {
  if (pagination.value.page <= 1) return
  void fetchStats(pagination.value.page - 1)
}

function goNextPage() {
  if (pagination.value.page >= totalPages.value) return
  void fetchStats(pagination.value.page + 1)
}

onMounted(() => {
  defaultDateRange()
  void fetchDepartmentTree()
  void fetchStats(1)
})
</script>

<template>
  <section class="usage-stats-panel" aria-label="数据统计">
    <div class="section-header">
      <div>
        <h2>数据统计</h2>
        <p class="section-subtitle">按时段查看账号活跃使用、问答与检索情况。活跃使用为连续有操作或问答/检索的时段，超过 15 分钟无操作则截断。</p>
      </div>
    </div>

    <div class="filters-card">
      <div class="filters-grid">
        <label class="filter-field">
          <span>开始日期</span>
          <input v-model="fromDate" type="date">
        </label>
        <label class="filter-field">
          <span>结束日期</span>
          <input v-model="toDate" type="date">
        </label>
        <label class="filter-field filter-field-wide">
          <span>关键词</span>
          <input v-model="keyword" type="text" placeholder="账号 / 人员姓名 / 工号">
        </label>
      </div>
      <div class="filters-grid filters-grid-sort">
        <label class="filter-field">
          <span>排序字段</span>
          <select v-model="sortBy" @change="handleSearch">
            <option v-for="option in sortOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>
        <label class="filter-field">
          <span>排序方向</span>
          <select v-model="sortOrder" @change="handleSearch">
            <option value="desc">降序</option>
            <option value="asc">升序</option>
          </select>
        </label>
      </div>
      <DepartmentSelector
        :tree="departmentTree"
        :primary-id="primaryDepartmentId"
        :secondary-id="secondaryDepartmentId"
        :tertiary-id="tertiaryDepartmentId"
        :disabled="departmentTreeLoading"
        :allow-empty="true"
        search-placeholder="筛选部门"
        @update:primary-id="primaryDepartmentId = $event"
        @update:secondary-id="secondaryDepartmentId = $event"
        @update:tertiary-id="tertiaryDepartmentId = $event"
      />
      <div class="filters-actions">
        <button type="button" class="btn-secondary" :disabled="loading || exporting" @click="handleReset">重置</button>
        <button type="button" class="btn-secondary" :disabled="loading || exporting" @click="handleExport('csv')">
          {{ exporting ? '导出中...' : '导出 CSV' }}
        </button>
        <button type="button" class="btn-secondary" :disabled="loading || exporting" @click="handleExport('xlsx')">
          {{ exporting ? '导出中...' : '导出 Excel' }}
        </button>
        <button type="button" class="btn-primary" :disabled="loading || exporting" @click="handleSearch">
          {{ loading ? '查询中...' : '查询' }}
        </button>
      </div>
    </div>

    <div v-if="error" class="alert alert-error">{{ error }}</div>

    <div class="table-shell">
      <div class="user-table-scroll">
        <div v-if="loading" class="loading">加载中...</div>
        <table v-else class="user-table">
          <thead>
            <tr>
              <th>账号</th>
              <th>绑定人员</th>
              <th>部门</th>
              <th>普通问答</th>
              <th>文件问答</th>
              <th>文献检索</th>
              <th>专利检索</th>
              <th>活跃使用</th>
              <th>
                <button type="button" class="sortable-th" @click="sortBy = 'last_active_at'; toggleSortOrder()">
                  最后活跃
                  <span v-if="sortBy === 'last_active_at'">{{ sortOrder === 'desc' ? '↓' : '↑' }}</span>
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="rows.length === 0">
              <td colspan="9" class="empty-cell">暂无数据</td>
            </tr>
            <tr v-for="row in rows" :key="row.id">
              <td>{{ row.username }}</td>
              <td>{{ row.personnel_display || '未绑定' }}</td>
              <td>{{ row.department_display || '未填写' }}</td>
              <td>{{ row.ask_query_count ?? 0 }}</td>
              <td>{{ row.file_qa_count ?? 0 }}</td>
              <td>{{ row.literature_search_count ?? 0 }}</td>
              <td>{{ row.patent_search_count ?? 0 }}</td>
              <td>{{ formatDuration(row.active_seconds) }}</td>
              <td>{{ formatDateTime(row.last_active_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="pagination-bar">
      <span>共 {{ pagination.total }} 条</span>
      <div class="pagination-actions">
        <button type="button" class="btn-secondary" :disabled="pagination.page <= 1 || loading" @click="goPrevPage">上一页</button>
        <span>第 {{ pagination.page }} / {{ totalPages }} 页</span>
        <button type="button" class="btn-secondary" :disabled="pagination.page >= totalPages || loading" @click="goNextPage">下一页</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.usage-stats-panel {
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.section-header {
  margin-bottom: 20px;
}

.section-header h2 {
  margin: 0 0 6px;
  font-size: 18px;
  color: #1f2937;
}

.section-subtitle {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
  line-height: 1.5;
}

.filters-card {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-bottom: 20px;
  padding: 16px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  background: #f9fafb;
}

.filters-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.filters-grid-sort {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.filter-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: #374151;
}

.filter-field-wide {
  grid-column: span 1;
}

.filter-field input,
.filter-field select {
  border: 1px solid #d1d5db;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 14px;
  background: white;
}

.filters-actions {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 10px;
}

.table-shell {
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  overflow: hidden;
}

.user-table-scroll {
  overflow-x: auto;
}

.user-table {
  width: 100%;
  border-collapse: collapse;
  min-width: 960px;
}

.user-table th,
.user-table td {
  padding: 12px 14px;
  border-bottom: 1px solid #f3f4f6;
  text-align: left;
  font-size: 14px;
  color: #1f2937;
}

.user-table th {
  background: #f9fafb;
  color: #6b7280;
  font-weight: 600;
}

.sortable-th {
  border: none;
  background: transparent;
  color: inherit;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
  padding: 0;
}

.empty-cell,
.loading {
  text-align: center;
  color: #6b7280;
  padding: 32px;
}

.pagination-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 16px;
  color: #6b7280;
  font-size: 14px;
}

.pagination-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.btn-primary,
.btn-secondary {
  border-radius: 8px;
  padding: 8px 14px;
  font-size: 14px;
  cursor: pointer;
}

.btn-primary {
  background: #667eea;
  color: white;
  border: none;
}

.btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-secondary {
  background: white;
  color: #374151;
  border: 1px solid #d1d5db;
}

.btn-secondary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.alert-error {
  background: #fef2f2;
  color: #dc2626;
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 16px;
}

@media (max-width: 900px) {
  .filters-grid,
  .filters-grid-sort {
    grid-template-columns: 1fr;
  }
}
</style>
