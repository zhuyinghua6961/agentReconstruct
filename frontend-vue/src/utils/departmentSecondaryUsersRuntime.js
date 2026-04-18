import { ref } from 'vue'

function normalizeKey(value) {
  return String(value)
}

function hasLoaded(usersById, nodeKey) {
  return Object.prototype.hasOwnProperty.call(usersById, normalizeKey(nodeKey))
}

export function createDepartmentUsersRuntime({ requestUsers }) {
  const expandedIds = ref([])
  const usersById = ref({})
  const loadingById = ref({})
  const errorById = ref({})

  function isExpanded(nodeKey) {
    return expandedIds.value.includes(normalizeKey(nodeKey))
  }

  function reset() {
    expandedIds.value = []
    usersById.value = {}
    loadingById.value = {}
    errorById.value = {}
  }

  async function load(nodeKey, options = {}) {
    const normalizedId = normalizeKey(nodeKey)
    const force = Boolean(options.force)
    if (!force && hasLoaded(usersById.value, normalizedId)) {
      return
    }

    loadingById.value = {
      ...loadingById.value,
      [normalizedId]: true,
    }
    errorById.value = {
      ...errorById.value,
      [normalizedId]: '',
    }

    try {
      const result = await requestUsers(normalizedId)
      if (result?.success) {
        usersById.value = {
          ...usersById.value,
          [normalizedId]: Array.isArray(result.data?.users) ? result.data.users : [],
        }
        errorById.value = {
          ...errorById.value,
          [normalizedId]: '',
        }
      } else {
        errorById.value = {
          ...errorById.value,
          [normalizedId]: result?.error || '获取用户列表失败',
        }
      }
    } catch {
      errorById.value = {
        ...errorById.value,
        [normalizedId]: '获取用户列表失败',
      }
    } finally {
      loadingById.value = {
        ...loadingById.value,
        [normalizedId]: false,
      }
    }
  }

  async function toggle(nodeKey) {
    const normalizedId = normalizeKey(nodeKey)
    if (expandedIds.value.includes(normalizedId)) {
      expandedIds.value = expandedIds.value.filter((item) => item !== normalizedId)
      return
    }

    expandedIds.value = [...expandedIds.value, normalizedId]
    await load(normalizedId)
  }

  return {
    expandedIds,
    usersById,
    loadingById,
    errorById,
    isExpanded,
    reset,
    load,
    toggle,
  }
}

export function createSecondaryUsersRuntime({ requestUsers }) {
  return createDepartmentUsersRuntime({ requestUsers })
}
