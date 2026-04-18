import test from 'node:test'
import assert from 'node:assert/strict'

import { buildDepartmentRenderTree } from './departmentManagementTreeModel.js'

test('buildDepartmentRenderTree inserts synthetic legacy-remediation leaf when legacy users exist', () => {
  const nodes = buildDepartmentRenderTree([
    {
      id: 11,
      name: '软件工程系',
      tertiary_count: 1,
      user_count: 3,
      legacy_user_count: 2,
      tertiary_items: [{ id: 111, name: '软件工程教研室', user_count: 1 }],
    },
  ])

  assert.equal(nodes[0].children[0].nodeType, 'legacy_pending')
  assert.equal(nodes[0].children[0].userCount, 2)
})
