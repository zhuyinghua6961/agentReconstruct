export const DEFAULT_DEPARTMENT_CREATE_TARGET_LEVEL = 'tertiary'

export function cleanDepartmentName(value) {
  return String(value || '').trim()
}

export function normalizeDepartmentId(value) {
  if (value === null || value === undefined || value === '') {
    return null
  }
  const normalized = Number(value)
  return Number.isFinite(normalized) ? normalized : null
}

export function buildDepartmentCreateState(options = {}) {
  const departmentTree = Array.isArray(options.departmentTree) ? options.departmentTree : []
  const targetLevel = options.targetLevel || DEFAULT_DEPARTMENT_CREATE_TARGET_LEVEL
  const primaryMode = options.primaryMode || 'new'
  const secondaryMode = options.secondaryMode || 'new'
  const selectedPrimaryId = normalizeDepartmentId(options.selectedPrimaryId)
  const selectedSecondaryId = normalizeDepartmentId(options.selectedSecondaryId)
  const primaryName = cleanDepartmentName(options.primaryName)
  const secondaryName = cleanDepartmentName(options.secondaryName)
  const tertiaryName = cleanDepartmentName(options.tertiaryName)
  const selectedPrimary = departmentTree.find(item => Number(item.id) === selectedPrimaryId) || null
  const secondaryItems = selectedPrimary && Array.isArray(selectedPrimary.secondary_items)
    ? selectedPrimary.secondary_items
    : []
  const needsParentPrimary = targetLevel !== 'primary'
  const needsTertiary = targetLevel === 'tertiary'
  const canSelectPrimary = departmentTree.length > 0
  const canSelectSecondary = (
    needsTertiary
    && primaryMode === 'existing'
    && Boolean(selectedPrimary)
    && secondaryItems.length > 0
  )

  return {
    departmentTree,
    targetLevel,
    primaryMode,
    secondaryMode,
    selectedPrimaryId,
    selectedSecondaryId,
    primaryName,
    secondaryName,
    tertiaryName,
    selectedPrimary,
    secondaryItems,
    needsParentPrimary,
    needsTertiary,
    canSelectPrimary,
    canSelectSecondary,
    canSubmit: buildCanSubmit({
      targetLevel,
      primaryMode,
      secondaryMode,
      selectedPrimaryId,
      selectedSecondaryId,
      primaryName,
      secondaryName,
      tertiaryName,
      submitting: Boolean(options.submitting),
    }),
  }
}

function buildCanSubmit({
  targetLevel,
  primaryMode,
  secondaryMode,
  selectedPrimaryId,
  selectedSecondaryId,
  primaryName,
  secondaryName,
  tertiaryName,
  submitting,
}) {
  if (submitting) {
    return false
  }
  if (targetLevel === 'primary') {
    return Boolean(primaryName)
  }
  if (primaryMode === 'existing' && selectedPrimaryId === null) {
    return false
  }
  if (primaryMode === 'new' && !primaryName) {
    return false
  }
  if (targetLevel === 'secondary') {
    return Boolean(secondaryName)
  }
  if (secondaryMode === 'existing') {
    return selectedSecondaryId !== null && Boolean(tertiaryName)
  }
  return Boolean(secondaryName && tertiaryName)
}
