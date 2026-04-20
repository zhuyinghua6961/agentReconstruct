<script setup>
import { computed, ref, watch } from 'vue'
import { adminApi } from '../services/admin'

const props = defineProps({
  modelValue: {
    type: [Number, String, null],
    default: null,
  },
  initialOptions: {
    type: Array,
    default: () => [],
  },
  disabled: Boolean,
  placeholder: {
    type: String,
    default: '搜索工号或姓名',
  },
})

const emit = defineEmits(['update:modelValue', 'select'])

const keyword = ref('')
const loading = ref(false)
const error = ref('')
const options = ref([])
const showOptions = ref(false)
const suppressSearch = ref(false)
const activeRequestId = ref(0)

function isActiveOption(item) {
  const statusText = String(
    item?.personnel_record_status
    ?? item?.status
    ?? '',
  ).trim().toLowerCase()
  return statusText === 'active'
}

function dedupeOptions(items) {
  const seen = new Set()
  return (Array.isArray(items) ? items : []).filter(item => {
    const optionId = Number(item?.id ?? 0)
    if (!optionId || seen.has(optionId)) {
      return false
    }
    seen.add(optionId)
    return true
  })
}

function setOptions(items) {
  options.value = dedupeOptions(items).filter(isActiveOption)
}

function matchesKeyword(item, normalizedKeyword) {
  if (!normalizedKeyword) {
    return true
  }
  const employeeNo = String(item?.employee_no || '').toLowerCase()
  const fullName = String(item?.full_name || '').toLowerCase()
  return employeeNo.includes(normalizedKeyword) || fullName.includes(normalizedKeyword)
}

const filteredOptions = computed(() => {
  const normalizedKeyword = String(keyword.value || '').trim().toLowerCase()
  return options.value.filter(item => matchesKeyword(item, normalizedKeyword))
})

async function fetchOptions(nextKeyword = keyword.value, { pageSize = 20, preserveExisting = false } = {}) {
  const searchKeyword = String(nextKeyword || '')
  const requestId = activeRequestId.value + 1
  activeRequestId.value = requestId
  loading.value = true
  error.value = ''
  const result = await adminApi.getPersonnel({ keyword: searchKeyword, status: 'active', page_size: pageSize })
  if (requestId !== activeRequestId.value) {
    return
  }
  if (result.success) {
    const items = Array.isArray(result.data?.items) ? result.data.items : []
    setOptions(items)
  } else {
    if (!preserveExisting) {
      setOptions([])
    }
    error.value = result.error || '获取人员选项失败'
  }
  loading.value = false
}

function selectOption(item) {
  suppressSearch.value = true
  keyword.value = `${item.employee_no} / ${item.full_name}`
  emit('update:modelValue', item.id)
  emit('select', item)
  showOptions.value = false
  queueMicrotask(() => {
    suppressSearch.value = false
  })
}

async function handleFocus() {
  showOptions.value = true
  if (!options.value.length && !props.initialOptions.length) {
    await fetchOptions('', { pageSize: 100 })
  }
}

watch(() => props.initialOptions, (items) => {
  setOptions(items)
}, { immediate: true })

watch(keyword, async (nextKeyword) => {
  if (suppressSearch.value || props.disabled) {
    return
  }
  emit('update:modelValue', null)
  showOptions.value = true
  const normalizedKeyword = String(nextKeyword || '').trim()
  if (!normalizedKeyword) {
    setOptions(props.initialOptions)
    return
  }
  await fetchOptions(normalizedKeyword, { preserveExisting: filteredOptions.value.length > 0 })
})
</script>

<template>
  <div class="personnel-lookup-select">
    <input
      v-model="keyword"
      class="lookup-input"
      type="text"
      :disabled="disabled"
      :placeholder="placeholder"
      @focus="handleFocus"
    >

    <div v-if="showOptions" class="lookup-panel">
      <div v-if="loading" class="lookup-state">搜索中...</div>
      <div v-else-if="error" class="lookup-state error">{{ error }}</div>
      <div v-else-if="!filteredOptions.length" class="lookup-state">暂无可选人员</div>
      <button
        v-for="item in filteredOptions"
        :key="item.id"
        class="lookup-option"
        type="button"
        @click="selectOption(item)"
      >
        {{ item.employee_no }} / {{ item.full_name }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.personnel-lookup-select {
  position: relative;
}

.lookup-input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
}

.lookup-panel {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  right: 0;
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.08);
  max-height: 240px;
  overflow-y: auto;
  z-index: 20;
}

.lookup-option,
.lookup-state {
  display: block;
  width: 100%;
  padding: 10px 12px;
  text-align: left;
}

.lookup-option {
  background: white;
  border: none;
  cursor: pointer;
}

.lookup-option:hover {
  background: #f3f4f6;
}

.lookup-state.error {
  color: #b91c1c;
}
</style>
