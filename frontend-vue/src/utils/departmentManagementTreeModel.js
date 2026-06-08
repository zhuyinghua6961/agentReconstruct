function normalizeCount(value) {
  const normalized = Number(value)
  return Number.isFinite(normalized) ? normalized : 0
}

export function buildDepartmentRenderTree(secondaryItems) {
  return (Array.isArray(secondaryItems) ? secondaryItems : []).map((secondary) => {
    const children = []
    const directUserCount = normalizeCount(secondary?.direct_user_count ?? secondary?.legacy_user_count)

    if (directUserCount > 0) {
      children.push({
        id: `direct-secondary-${secondary.id}`,
        nodeKey: `direct-secondary-${secondary.id}`,
        nodeType: 'secondary_direct',
        node_type: 'secondary_direct',
        name: '直属二级部门成员',
        userCount: directUserCount,
        user_count: directUserCount,
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
      directUserCount,
      direct_user_count: directUserCount,
      legacyUserCount: directUserCount,
      legacy_user_count: directUserCount,
      children,
      raw: secondary,
    }
  })
}

export function buildDepartmentRenderPrimary(primary) {
  const children = []
  const directUserCount = normalizeCount(primary?.direct_user_count ?? primary?.primary_direct_user_count)
  const renderSecondaryItems = buildDepartmentRenderTree(primary?.secondary_items)

  if (directUserCount > 0) {
    children.push({
      id: `direct-primary-${primary.id}`,
      nodeKey: `direct-primary-${primary.id}`,
      nodeType: 'primary_direct',
      node_type: 'primary_direct',
      name: '直属一级部门成员',
      userCount: directUserCount,
      user_count: directUserCount,
      status: 'active',
      primaryId: primary.id,
    })
  }

  return {
    ...(primary || {}),
    directUserCount,
    direct_user_count: directUserCount,
    renderSecondaryItems,
    children: [
      ...children,
      ...renderSecondaryItems,
    ],
  }
}
