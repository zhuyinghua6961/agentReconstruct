function normalizeId(value) {
  if (value === null || value === undefined || value === '') {
    return null
  }
  const normalized = Number(value)
  return Number.isFinite(normalized) ? normalized : null
}

function matchesSearch(text, keyword) {
  return String(text || '').toLowerCase().includes(String(keyword || '').toLowerCase())
}

function secondaryTertiaryItems(secondary) {
  return Array.isArray(secondary?.tertiary_items) ? secondary.tertiary_items : []
}

export function buildSearchMatches(tree, keyword) {
  const normalizedKeyword = String(keyword || '').trim().toLowerCase()
  if (!normalizedKeyword) {
    return []
  }

  return (Array.isArray(tree) ? tree : []).flatMap((primary) => (
    (Array.isArray(primary?.secondary_items) ? primary.secondary_items : []).flatMap((secondary) => (
      secondaryTertiaryItems(secondary)
        .filter((tertiary) => (
          matchesSearch(primary?.name, normalizedKeyword)
          || matchesSearch(secondary?.name, normalizedKeyword)
          || matchesSearch(tertiary?.name, normalizedKeyword)
        ))
        .map((tertiary) => ({
          primaryId: normalizeId(primary?.id),
          secondaryId: normalizeId(secondary?.id),
          tertiaryId: normalizeId(tertiary?.id),
          path: `${primary?.name || ''} / ${secondary?.name || ''} / ${tertiary?.name || ''}`,
        }))
    ))
  ))
}

export function buildSecondarySelectionState(secondary) {
  const tertiaryItems = secondaryTertiaryItems(secondary)
  const selectable = Boolean(secondary)
  return {
    selectable,
    disabledReason: selectable ? '' : String(secondary?.disabled_reason || ''),
    tertiaryItems,
  }
}

export function shouldClearTertiarySelection(secondary, tertiaryId) {
  const normalizedTertiaryId = normalizeId(tertiaryId)
  if (normalizedTertiaryId === null) {
    return false
  }
  const tertiaryItems = secondaryTertiaryItems(secondary)
  return !tertiaryItems.some(item => normalizeId(item?.id) === normalizedTertiaryId)
}

export function buildFilteredPrimaryItems(tree, keyword) {
  const normalizedKeyword = String(keyword || '').trim().toLowerCase()
  const items = Array.isArray(tree) ? tree : []
  if (!normalizedKeyword) {
    return items
  }
  return items.filter((primary) => {
    if (matchesSearch(primary?.name, normalizedKeyword)) {
      return true
    }
    return (Array.isArray(primary?.secondary_items) ? primary.secondary_items : []).some((secondary) => (
      matchesSearch(secondary?.name, normalizedKeyword)
      || secondaryTertiaryItems(secondary).some(tertiary => matchesSearch(tertiary?.name, normalizedKeyword))
    ))
  })
}

export function buildFilteredSecondaryItems(primary, keyword) {
  const items = Array.isArray(primary?.secondary_items) ? primary.secondary_items : []
  const normalizedKeyword = String(keyword || '').trim().toLowerCase()
  if (!normalizedKeyword) {
    return items
  }
  return items.filter((secondary) => (
    matchesSearch(primary?.name, normalizedKeyword)
    || matchesSearch(secondary?.name, normalizedKeyword)
    || secondaryTertiaryItems(secondary).some(tertiary => matchesSearch(tertiary?.name, normalizedKeyword))
  ))
}

export function buildFilteredTertiaryItems(primary, secondary, keyword) {
  const tertiaryItems = secondaryTertiaryItems(secondary)
  const normalizedKeyword = String(keyword || '').trim().toLowerCase()
  if (!normalizedKeyword) {
    return tertiaryItems
  }
  return tertiaryItems.filter((tertiary) => (
    matchesSearch(primary?.name, normalizedKeyword)
    || matchesSearch(secondary?.name, normalizedKeyword)
    || matchesSearch(tertiary?.name, normalizedKeyword)
  ))
}

export function selectSearchMatch(match) {
  return {
    primaryId: normalizeId(match?.primaryId),
    secondaryId: normalizeId(match?.secondaryId),
    tertiaryId: normalizeId(match?.tertiaryId),
  }
}

export { normalizeId }
