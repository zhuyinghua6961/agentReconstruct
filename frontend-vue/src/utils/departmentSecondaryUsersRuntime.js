import { ref } from 'vue'

function normalizeId(value) {
  return Number(value)
}

function hasLoaded(usersById, secondaryId) {
  return Object.prototype.hasOwnProperty.call(usersById, normalizeId(secondaryId))
}

export function createSecondaryUsersRuntime({ requestUsers }) {
  const expandedIds = ref([])
  const usersById = ref({})
  const loadingById = ref({})
  const errorById = ref({})

  function isExpanded(secondaryId) {
    return expandedIds.value.includes(normalizeId(secondaryId))
  }

  function reset() {
    expandedIds.value = []
    usersById.value = {}
    loadingById.value = {}
    errorById.value = {}
  }

  async function load(secondaryId, options = {}) {
    const normalizedId = normalizeId(secondaryId)
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

  async function toggle(secondaryId) {
    const normalizedId = normalizeId(secondaryId)
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
