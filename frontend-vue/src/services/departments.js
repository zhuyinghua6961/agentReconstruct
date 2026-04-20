import { clearStoredAuth, readStoredToken } from './auth.js'

const AUTH_DEPARTMENT_BASE = '/api/auth'

function normalizeId(value) {
  if (value === null || value === undefined || value === '') {
    return null
  }
  const normalized = Number(value)
  return Number.isFinite(normalized) ? normalized : null
}

async function safeJson(response) {
  try {
    return await response.json()
  } catch {
    return { success: false, error: '接口暂不可用' }
  }
}

function handleDepartmentError(data, response) {
  if (data?.code === 'TOKEN_INVALID' || data?.code === 'TOKEN_MISSING' || response?.status === 401) {
    clearStoredAuth()
    if (!window.location.pathname.includes('/login')) {
      window.location.href = '/login'
    }
  }
}

async function fetchDepartmentJson(url, options = {}) {
  const response = await fetch(url, options)
  const data = await safeJson(response)
  if (!response.ok || !data.success) {
    handleDepartmentError(data, response)
  }
  return data?.success !== undefined
    ? data
    : { success: response.ok, error: response.ok ? '' : `HTTP ${response.status}` }
}

export function mergePreservedDepartmentTree(tree, department) {
  const sourceItems = Array.isArray(tree) ? tree : []
  const primaryDepartmentId = normalizeId(department?.primary_department_id)
  const secondaryDepartmentId = normalizeId(department?.secondary_department_id)
  const tertiaryDepartmentId = normalizeId(department?.tertiary_department_id)
  const primaryDepartmentName = String(department?.primary_department_name || '').trim()
  const secondaryDepartmentName = String(department?.secondary_department_name || '').trim()
  const tertiaryDepartmentName = String(department?.tertiary_department_name || '').trim()
  const effectiveStatus = String(department?.department_effective_status || '').trim().toLowerCase()

  const clonedItems = sourceItems.map((item) => ({
    ...item,
    secondary_items: Array.isArray(item.secondary_items)
      ? item.secondary_items.map((secondary) => ({
        ...secondary,
        tertiary_items: Array.isArray(secondary.tertiary_items)
          ? secondary.tertiary_items.map(tertiary => ({ ...tertiary }))
          : [],
      }))
      : [],
  }))

  if (
    primaryDepartmentId === null
    || secondaryDepartmentId === null
    || !primaryDepartmentName
    || !secondaryDepartmentName
    || effectiveStatus !== 'disabled'
  ) {
    return clonedItems
  }

  const primaryIndex = clonedItems.findIndex(item => Number(item.id) === primaryDepartmentId)
  const disabledSuffix = '（当前绑定，已停用）'
  const buildDisabledSecondary = () => ({
    id: secondaryDepartmentId,
    name: `${secondaryDepartmentName}${disabledSuffix}`,
    tertiary_items: tertiaryDepartmentId !== null && tertiaryDepartmentName
      ? [{ id: tertiaryDepartmentId, name: `${tertiaryDepartmentName}${disabledSuffix}` }]
      : [],
  })

  if (tertiaryDepartmentId === null || !tertiaryDepartmentName) {
    if (
      primaryDepartmentId === null
      || secondaryDepartmentId === null
      || !primaryDepartmentName
      || !secondaryDepartmentName
      || effectiveStatus !== 'disabled'
    ) {
      return clonedItems
    }
    if (primaryIndex === -1) {
      return [
        ...clonedItems,
        {
          id: primaryDepartmentId,
          name: `${primaryDepartmentName}${disabledSuffix}`,
          secondary_items: [buildDisabledSecondary()],
        },
      ]
    }
    const primaryItem = clonedItems[primaryIndex]
    const hasSecondary = Array.isArray(primaryItem.secondary_items)
      && primaryItem.secondary_items.some(item => Number(item.id) === secondaryDepartmentId)
    if (hasSecondary) {
      return clonedItems
    }
    primaryItem.secondary_items = [
      ...(Array.isArray(primaryItem.secondary_items) ? primaryItem.secondary_items : []),
      buildDisabledSecondary(),
    ]
    return clonedItems
  }

  if (primaryIndex === -1) {
    return [
      ...clonedItems,
      {
        id: primaryDepartmentId,
        name: `${primaryDepartmentName}${disabledSuffix}`,
        secondary_items: [buildDisabledSecondary()],
      },
    ]
  }

  const primaryItem = clonedItems[primaryIndex]
  const secondaryIndex = Array.isArray(primaryItem.secondary_items)
    ? primaryItem.secondary_items.findIndex(item => Number(item.id) === secondaryDepartmentId)
    : -1
  if (secondaryIndex === -1) {
    primaryItem.secondary_items = [
      ...(Array.isArray(primaryItem.secondary_items) ? primaryItem.secondary_items : []),
      buildDisabledSecondary(),
    ]
    return clonedItems
  }

  const secondaryItem = primaryItem.secondary_items[secondaryIndex]
  const hasTertiary = Array.isArray(secondaryItem.tertiary_items)
    && secondaryItem.tertiary_items.some(item => Number(item.id) === tertiaryDepartmentId)
  if (hasTertiary) {
    return clonedItems
  }
  secondaryItem.tertiary_items = [
    ...(Array.isArray(secondaryItem.tertiary_items) ? secondaryItem.tertiary_items : []),
    {
      id: tertiaryDepartmentId,
      name: `${tertiaryDepartmentName}${disabledSuffix}`,
    },
  ]
  return clonedItems
}

export const departmentApi = {
  async getSelectableTree() {
    const token = readStoredToken()
    const headers = {}
    if (token) {
      headers.Authorization = `Bearer ${token}`
    }
    return fetchDepartmentJson(`${AUTH_DEPARTMENT_BASE}/departments/tree`, {
      headers,
    })
  },

  async updateMyDepartment(primaryDepartmentId, secondaryDepartmentId, tertiaryDepartmentId) {
    const token = readStoredToken()
    return fetchDepartmentJson(`${AUTH_DEPARTMENT_BASE}/department`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        primary_department_id: primaryDepartmentId,
        secondary_department_id: secondaryDepartmentId,
        tertiary_department_id: tertiaryDepartmentId,
      }),
    })
  },
}
