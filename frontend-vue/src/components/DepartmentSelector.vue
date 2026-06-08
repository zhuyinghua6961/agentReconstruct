<script setup>
import { computed, ref, watch } from 'vue'
import {
  buildFilteredPrimaryItems,
  buildFilteredSecondaryItems,
  buildFilteredTertiaryItems,
  buildSearchMatches,
  buildSecondarySelectionState,
  normalizeId,
  shouldClearTertiarySelection,
  selectSearchMatch as selectSearchMatchState,
} from '../utils/departmentSelectorModel'

const props = defineProps({
  tree: {
    type: Array,
    default: () => [],
  },
  primaryId: {
    type: [Number, String, null],
    default: null,
  },
  secondaryId: {
    type: [Number, String, null],
    default: null,
  },
  tertiaryId: {
    type: [Number, String, null],
    default: null,
  },
  allowEmpty: {
    type: Boolean,
    default: false,
  },
  disabled: {
    type: Boolean,
    default: false,
  },
  primaryLabel: {
    type: String,
    default: '一级部门',
  },
  secondaryLabel: {
    type: String,
    default: '二级部门',
  },
  tertiaryLabel: {
    type: String,
    default: '三级部门',
  },
  searchPlaceholder: {
    type: String,
    default: '搜索部门',
  },
  emptyText: {
    type: String,
    default: '暂无可选部门，请联系管理员维护部门字典',
  },
})

const emit = defineEmits(['update:primaryId', 'update:secondaryId', 'update:tertiaryId'])

const searchKeyword = ref('')

const treeItems = computed(() => (
  Array.isArray(props.tree) ? props.tree : []
))

const normalizedPrimaryId = computed(() => normalizeId(props.primaryId))
const normalizedSecondaryId = computed(() => normalizeId(props.secondaryId))
const normalizedTertiaryId = computed(() => normalizeId(props.tertiaryId))
const normalizedKeyword = computed(() => searchKeyword.value.trim().toLowerCase())

const selectedPrimaryItem = computed(() => (
  treeItems.value.find(item => Number(item.id) === normalizedPrimaryId.value) || null
))

const selectedSecondaryItem = computed(() => {
  if (!selectedPrimaryItem.value) {
    return null
  }
  const secondaryItems = Array.isArray(selectedPrimaryItem.value.secondary_items)
    ? selectedPrimaryItem.value.secondary_items
    : []
  return secondaryItems.find(item => Number(item.id) === normalizedSecondaryId.value) || null
})

const selectedSecondaryState = computed(() => (
  buildSecondarySelectionState(selectedSecondaryItem.value)
))

const filteredPrimaryItems = computed(() => {
  return buildFilteredPrimaryItems(treeItems.value, normalizedKeyword.value)
})

const searchMatches = computed(() => {
  if (!normalizedKeyword.value || selectedPrimaryItem.value) {
    return []
  }
  return buildSearchMatches(treeItems.value, normalizedKeyword.value)
})

const filteredSecondaryItems = computed(() => {
  if (!selectedPrimaryItem.value) {
    return []
  }
  return buildFilteredSecondaryItems(selectedPrimaryItem.value, normalizedKeyword.value)
})

const filteredTertiaryItems = computed(() => {
  if (!selectedPrimaryItem.value || !selectedSecondaryItem.value) {
    return []
  }
  return buildFilteredTertiaryItems(
    selectedPrimaryItem.value,
    selectedSecondaryItem.value,
    normalizedKeyword.value,
  )
})

watch(selectedPrimaryItem, (primaryItem) => {
  if (!primaryItem) {
    emit('update:secondaryId', null)
    emit('update:tertiaryId', null)
    return
  }
  if (!normalizedSecondaryId.value) {
    return
  }
  const secondaryItems = Array.isArray(primaryItem.secondary_items) ? primaryItem.secondary_items : []
  const exists = secondaryItems.some(item => Number(item.id) === normalizedSecondaryId.value)
  if (!exists) {
    emit('update:secondaryId', null)
    emit('update:tertiaryId', null)
  }
})

watch(selectedSecondaryItem, (secondaryItem) => {
  if (!secondaryItem) {
    emit('update:tertiaryId', null)
    return
  }
  if (shouldClearTertiarySelection(secondaryItem, normalizedTertiaryId.value)) {
    emit('update:tertiaryId', null)
  }
})

function handlePrimaryChange(value) {
  const nextPrimaryId = normalizeId(value)
  if (nextPrimaryId === normalizedPrimaryId.value) {
    return
  }
  emit('update:primaryId', nextPrimaryId)
  emit('update:secondaryId', null)
  emit('update:tertiaryId', null)
}

function handleSecondaryChange(value) {
  emit('update:secondaryId', normalizeId(value))
  emit('update:tertiaryId', null)
}

function handleTertiaryChange(value) {
  emit('update:tertiaryId', normalizeId(value))
}

function clearSelection() {
  emit('update:primaryId', null)
  emit('update:secondaryId', null)
  emit('update:tertiaryId', null)
}

function selectSearchMatch(match) {
  const selected = selectSearchMatchState(match)
  emit('update:primaryId', selected.primaryId)
  emit('update:secondaryId', selected.secondaryId)
  emit('update:tertiaryId', selected.tertiaryId)
}
</script>

<template>
  <div class="department-selector" :class="{ disabled }">
    <div class="selector-toolbar">
      <label class="toolbar-label">搜索部门</label>
      <div class="toolbar-actions">
        <input
          v-model="searchKeyword"
          class="search-input"
          type="text"
          :placeholder="searchPlaceholder"
          :disabled="disabled"
        >
        <button
          v-if="allowEmpty"
          type="button"
          class="clear-btn"
          :disabled="disabled"
          @click="clearSelection"
        >
          清空
        </button>
      </div>
      <p class="toolbar-hint">支持按一级、二级或三级部门名称搜索，搜索结果会显示完整路径。</p>
    </div>

    <div v-if="!treeItems.length" class="selector-empty">
      {{ emptyText }}
    </div>

    <div v-else-if="searchMatches.length" class="search-match-list">
      <button
        v-for="match in searchMatches"
        :key="`${match.primaryId}-${match.secondaryId}-${match.tertiaryId}`"
        type="button"
        class="search-match-item"
        :disabled="disabled"
        @click="selectSearchMatch(match)"
      >
        {{ match.path }}
      </button>
    </div>

    <div v-if="treeItems.length" class="selector-grid">
      <div class="selector-field">
        <label>{{ primaryLabel }}</label>
        <select
          :value="normalizedPrimaryId ?? ''"
          :disabled="disabled"
          @change="handlePrimaryChange($event.target.value)"
        >
          <option value="">请选择一级部门</option>
          <option
            v-for="item in filteredPrimaryItems"
            :key="item.id"
            :value="item.id"
          >
            {{ item.name }}
          </option>
        </select>
      </div>

      <div class="selector-field">
        <label>{{ secondaryLabel }}</label>
        <select
          :value="normalizedSecondaryId ?? ''"
          :disabled="disabled || !selectedPrimaryItem"
          @change="handleSecondaryChange($event.target.value)"
        >
          <option value="">{{ selectedPrimaryItem ? '请选择二级部门' : '请先选择一级部门' }}</option>
          <option
            v-for="item in filteredSecondaryItems"
            :key="item.id"
            :value="item.id"
          >
            {{ item.name }}
          </option>
        </select>
        <p v-if="selectedPrimaryItem && !filteredSecondaryItems.length" class="secondary-empty">
          当前一级部门下暂无匹配的二级部门
        </p>
        <p
          v-else-if="selectedSecondaryItem && !selectedSecondaryState.selectable"
          class="secondary-empty"
        >
          {{ selectedSecondaryState.disabledReason }}
        </p>
      </div>

      <div class="selector-field">
        <label>{{ tertiaryLabel }}</label>
        <select
          :value="normalizedTertiaryId ?? ''"
          :disabled="disabled || !selectedSecondaryItem || !selectedSecondaryState.selectable || !filteredTertiaryItems.length"
          @change="handleTertiaryChange($event.target.value)"
        >
          <option value="">
            {{
              !selectedPrimaryItem
                ? '请先选择一级部门'
                : !selectedSecondaryItem
                  ? '请先选择二级部门'
                  : !selectedSecondaryState.selectable
                    ? '当前二级部门不可选'
                    : !filteredTertiaryItems.length
                      ? '三级部门可留空'
                      : '请选择三级部门'
            }}
          </option>
          <option
            v-for="item in filteredTertiaryItems"
            :key="item.id"
            :value="item.id"
          >
            {{ item.name }}
          </option>
        </select>
        <p
          v-if="selectedSecondaryItem && selectedSecondaryState.selectable && !filteredTertiaryItems.length"
          class="secondary-empty"
        >
          当前二级部门下暂无三级部门，可直接绑定到二级部门
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.department-selector {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.department-selector.disabled {
  opacity: 0.7;
}

.selector-toolbar {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.toolbar-label {
  font-size: 14px;
  font-weight: 600;
  color: #374151;
}

.toolbar-actions {
  display: flex;
  gap: 12px;
}

.search-input,
.selector-field select {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 14px;
  color: #1f2937;
  background: #fff;
}

.search-input:focus,
.selector-field select:focus {
  outline: none;
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.12);
}

.clear-btn {
  min-width: 88px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  background: #f9fafb;
  color: #374151;
  cursor: pointer;
}

.toolbar-hint,
.secondary-empty,
.selector-empty {
  margin: 0;
  font-size: 13px;
  color: #6b7280;
}

.selector-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.selector-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.selector-field label {
  font-size: 14px;
  color: #374151;
}

.search-match-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.search-match-item {
  border: 1px solid #c7d2fe;
  border-radius: 999px;
  background: #eef2ff;
  color: #3730a3;
  padding: 6px 12px;
  font-size: 13px;
  cursor: pointer;
}

.search-match-item:hover {
  background: #e0e7ff;
}

@media (max-width: 768px) {
  .selector-grid {
    grid-template-columns: 1fr;
  }

  .toolbar-actions {
    flex-direction: column;
  }

  .clear-btn {
    min-height: 40px;
  }
}
</style>
