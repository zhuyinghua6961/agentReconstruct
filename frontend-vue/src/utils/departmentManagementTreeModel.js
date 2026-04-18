function normalizeCount(value) {
  const normalized = Number(value)
  return Number.isFinite(normalized) ? normalized : 0
}

export function buildDepartmentRenderTree(secondaryItems) {
  return (Array.isArray(secondaryItems) ? secondaryItems : []).map((secondary) => {
    const children = []
    const legacyUserCount = normalizeCount(secondary?.legacy_user_count)

    if (legacyUserCount > 0) {
      children.push({
        id: `legacy-secondary-${secondary.id}`,
        nodeKey: `legacy-secondary-${secondary.id}`,
        nodeType: 'legacy_pending',
        node_type: 'legacy_pending',
        name: '未补全三级部门用户',
        userCount: legacyUserCount,
        user_count: legacyUserCount,
        status: 'active',
        secondaryId: secondary.id,
      })
    }

    ;(Array.isArray(secondary?.tertiary_items) ? secondary.tertiary_items : []).forEach((tertiary) => {
      children.push({
        id: tertiary.id,
        nodeKey: String(tertiary.id),
        nodeType: 'tertiary',
        node_type: 'tertiary',
        name: tertiary.name,
        userCount: normalizeCount(tertiary.user_count),
        user_count: normalizeCount(tertiary.user_count),
        status: tertiary.status || 'active',
        effectiveStatus: tertiary.effective_status || tertiary.status || 'active',
        tertiaryId: tertiary.id,
        tertiary_id: tertiary.id,
        raw: tertiary,
      })
    })

    return {
      id: secondary.id,
      nodeType: 'secondary',
      node_type: 'secondary',
      name: secondary.name,
      status: secondary.status || 'active',
      effectiveStatus: secondary.effective_status || secondary.status || 'active',
      tertiaryCount: normalizeCount(secondary.tertiary_count || children.filter(item => item.nodeType === 'tertiary').length),
      tertiary_count: normalizeCount(secondary.tertiary_count || children.filter(item => item.nodeType === 'tertiary').length),
      userCount: normalizeCount(secondary.user_count),
      user_count: normalizeCount(secondary.user_count),
      legacyUserCount,
      legacy_user_count: legacyUserCount,
      children,
      raw: secondary,
    }
  })
}
