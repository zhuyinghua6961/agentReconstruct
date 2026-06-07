import test from 'node:test'
import assert from 'node:assert/strict'
import {
  DEFAULT_DEPARTMENT_CREATE_TARGET_LEVEL,
  buildDepartmentCreateState,
} from './departmentCreateModel.js'

const departmentTree = [
  {
    id: 1,
    name: '计算机学院',
    secondary_items: [
      { id: 11, name: '软件工程系', tertiary_items: [] },
    ],
  },
]

test('department create model defaults to tertiary creation', () => {
  const state = buildDepartmentCreateState()

  assert.equal(DEFAULT_DEPARTMENT_CREATE_TARGET_LEVEL, 'tertiary')
  assert.equal(state.targetLevel, 'tertiary')
  assert.equal(state.needsTertiary, true)
})

test('department create model allows tertiary creation under an existing secondary department', () => {
  const state = buildDepartmentCreateState({
    departmentTree,
    targetLevel: 'tertiary',
    primaryMode: 'existing',
    secondaryMode: 'existing',
    selectedPrimaryId: 1,
    selectedSecondaryId: 11,
    tertiaryName: '人工智能教研室',
  })

  assert.equal(state.needsTertiary, true)
  assert.equal(state.canSelectSecondary, true)
  assert.equal(state.canSubmit, true)
  assert.equal(state.selectedPrimary.id, 1)
  assert.equal(state.secondaryItems.length, 1)
})

test('department create model requires a secondary name when creating a new secondary in a tertiary chain', () => {
  const state = buildDepartmentCreateState({
    departmentTree,
    targetLevel: 'tertiary',
    primaryMode: 'existing',
    secondaryMode: 'new',
    selectedPrimaryId: 1,
    secondaryName: '',
    tertiaryName: '人工智能教研室',
  })

  assert.equal(state.canSubmit, false)
})
