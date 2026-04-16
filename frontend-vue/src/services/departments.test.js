import test from 'node:test'
import assert from 'node:assert/strict'
import { mergePreservedDepartmentTree } from './departments.js'

test('mergePreservedDepartmentTree appends current disabled binding when it is missing from selectable tree', () => {
  const tree = [
    {
      id: 2,
      name: '化学学院',
      secondary_items: [{ id: 21, name: '材料系' }],
    },
  ]

  const merged = mergePreservedDepartmentTree(tree, {
    primary_department_id: 1,
    primary_department_name: '计算机学院',
    secondary_department_id: 11,
    secondary_department_name: '软件工程系',
    department_effective_status: 'disabled',
  })

  assert.equal(merged.length, 2)
  assert.equal(merged[1].id, 1)
  assert.match(merged[1].name, /已停用/)
  assert.equal(merged[1].secondary_items[0].id, 11)
  assert.match(merged[1].secondary_items[0].name, /已停用/)
})

test('mergePreservedDepartmentTree injects only the missing disabled secondary under an existing primary', () => {
  const tree = [
    {
      id: 1,
      name: '计算机学院',
      secondary_items: [{ id: 12, name: '人工智能系' }],
    },
  ]

  const merged = mergePreservedDepartmentTree(tree, {
    primary_department_id: 1,
    primary_department_name: '计算机学院',
    secondary_department_id: 11,
    secondary_department_name: '软件工程系',
    department_effective_status: 'disabled',
  })

  assert.equal(merged.length, 1)
  assert.equal(merged[0].secondary_items.length, 2)
  assert.equal(merged[0].secondary_items[1].id, 11)
  assert.match(merged[0].secondary_items[1].name, /已停用/)
})

test('mergePreservedDepartmentTree leaves active selections unchanged', () => {
  const tree = [
    {
      id: 1,
      name: '计算机学院',
      secondary_items: [{ id: 11, name: '软件工程系' }],
    },
  ]

  const merged = mergePreservedDepartmentTree(tree, {
    primary_department_id: 1,
    primary_department_name: '计算机学院',
    secondary_department_id: 11,
    secondary_department_name: '软件工程系',
    department_effective_status: 'active',
  })

  assert.deepEqual(merged, tree)
})
