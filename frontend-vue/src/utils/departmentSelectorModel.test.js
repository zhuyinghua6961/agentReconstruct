import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildSearchMatches,
  buildSecondarySelectionState,
  shouldClearTertiarySelection,
  selectSearchMatch,
} from './departmentSelectorModel.js'

const tree = [
  {
    id: 1,
    name: '计算机学院',
    secondary_items: [
      {
        id: 11,
        name: '软件工程系',
        selectable: true,
        tertiary_items: [
          { id: 111, name: '软件工程教研室' },
        ],
      },
      {
        id: 12,
        name: '人工智能系',
        selectable: false,
        disabled_reason: '暂无三级部门，请联系管理员维护',
        tertiary_items: [],
      },
    ],
  },
]

test('buildSearchMatches returns full tertiary paths only', () => {
  const matches = buildSearchMatches(tree, '软件')
  assert.deepEqual(matches.map(item => item.path), [
    '计算机学院 / 软件工程系 / 软件工程教研室',
  ])
})

test('buildSecondarySelectionState keeps secondary without tertiary visible but unselectable', () => {
  const state = buildSecondarySelectionState(tree[0].secondary_items[1])
  assert.equal(state.selectable, false)
  assert.match(state.disabledReason, /暂无三级部门/)
})

test('selectSearchMatch fills all three ids from one full-path result', () => {
  const selected = selectSearchMatch({
    primaryId: 1,
    secondaryId: 11,
    tertiaryId: 111,
  })
  assert.deepEqual(selected, {
    primaryId: 1,
    secondaryId: 11,
    tertiaryId: 111,
  })
})

test('shouldClearTertiarySelection preserves current disabled tertiary binding when it still exists', () => {
  const secondary = {
    id: 11,
    name: '软件工程系',
    selectable: false,
    disabled_reason: '暂无三级部门，请联系管理员维护',
    tertiary_items: [
      { id: 111, name: '软件工程教研室（当前绑定，已停用）' },
    ],
  }

  assert.equal(shouldClearTertiarySelection(secondary, 111), false)
  assert.equal(shouldClearTertiarySelection(secondary, 999), true)
  assert.equal(shouldClearTertiarySelection(secondary, null), false)
})
