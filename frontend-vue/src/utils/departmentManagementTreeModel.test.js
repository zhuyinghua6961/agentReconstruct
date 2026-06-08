import test from 'node:test'
import assert from 'node:assert/strict'

import { buildDepartmentRenderPrimary } from './departmentManagementTreeModel.js'
import { buildDepartmentRenderTree } from './departmentManagementTreeModel.js'

test('buildDepartmentRenderTree inserts secondary-direct member leaf when direct members exist', () => {
  const nodes = buildDepartmentRenderTree([
    {
      id: 11,
      name: '软件工程系',
      tertiary_count: 1,
      user_count: 3,
      direct_user_count: 2,
      tertiary_items: [{ id: 111, name: '软件工程教研室', user_count: 1 }],
    },
  ])

  assert.equal(nodes[0].children[0].nodeType, 'secondary_direct')
  assert.equal(nodes[0].children[0].name, '直属二级部门成员')
  assert.equal(nodes[0].children[0].userCount, 2)
})

test('buildDepartmentRenderTree keeps legacy count as a backward compatible secondary-direct alias', () => {
  const nodes = buildDepartmentRenderTree([
    {
      id: 11,
      name: '软件工程系',
      tertiary_count: 0,
      user_count: 2,
      legacy_user_count: 2,
      tertiary_items: [],
    },
  ])

  assert.equal(nodes[0].children[0].nodeType, 'secondary_direct')
  assert.equal(nodes[0].children[0].userCount, 2)
})

test('buildDepartmentRenderPrimary inserts primary-direct member leaf before secondary items', () => {
  const primary = buildDepartmentRenderPrimary({
    id: 1,
    name: '计算机学院',
    direct_user_count: 3,
    secondary_items: [
      { id: 11, name: '软件工程系', tertiary_items: [] },
    ],
  })

  assert.equal(primary.children[0].nodeType, 'primary_direct')
  assert.equal(primary.children[0].name, '直属一级部门成员')
  assert.equal(primary.children[0].userCount, 3)
  assert.equal(primary.children[1].nodeType, 'secondary')
})
