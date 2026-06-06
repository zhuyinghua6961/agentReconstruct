import test from 'node:test'
import assert from 'node:assert/strict'

import {
  formatQueryModeLabel,
  resolveActualQueryModeLabel,
  resolveActualQueryModeRaw,
} from './queryMode.js'

test('resolveActualQueryModeLabel prefers actual executed mode over query mode and route', () => {
  const label = resolveActualQueryModeLabel(
    {
      actual_mode: 'thinking',
      query_mode: 'fast',
      route: 'kb_qa',
    },
    {}
  )

  assert.equal(label, '深度模式')
})

test('resolveActualQueryModeLabel can skip route fallback for in-progress assistant badges', () => {
  assert.equal(resolveActualQueryModeLabel({ route: 'kb_qa' }, {}, { allowRouteFallback: false }), '')
})

test('resolveActualQueryModeRaw restores actual mode from message metadata', () => {
  assert.equal(resolveActualQueryModeRaw({}, { actual_mode: 'patent', query_mode: 'thinking' }), 'patent')
})

test('formatQueryModeLabel maps graph modes to knowledge graph', () => {
  assert.equal(formatQueryModeLabel('graph_kb'), '知识图谱')
  assert.equal(formatQueryModeLabel('neo4j'), '知识图谱')
})
