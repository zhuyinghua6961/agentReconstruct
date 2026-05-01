import test from 'node:test'
import assert from 'node:assert/strict'

import {
  formatStageDuration,
  getMessageStageTimingModel,
  normalizeStageTimings,
} from './stageTimings.js'

test('normalizes patent and fastQA stage timings as milliseconds', () => {
  const model = normalizeStageTimings({
    stage1: 12054,
    stage2: 16210,
    stage25: 8.9,
    stage3: 24.5,
    stage4: 50524,
  })

  assert.equal(model.hasTimings, true)
  assert.equal(model.family, 'generation-stage')
  assert.equal(model.totalLabel, '1m18.8s')
  assert.equal(model.slowest.key, 'stage4')
  assert.equal(model.slowest.durationLabel, '50.5s')
  assert.deepEqual(model.entries.map((entry) => entry.key), ['stage1', 'stage2', 'stage25', 'stage3', 'stage4'])
  assert.equal(model.entries[1].description, '向量检索与重排')
})

test('normalizes highThinking step timings as seconds', () => {
  const model = normalizeStageTimings({
    step1_parallel: 3.2,
    step2_pre_answer: 1.1,
    step3_retrieval: 7.8,
    step4_synthesis: 11.4,
    step5_check_revise: 2.5,
    step5_check_loop_1: 1.4,
    step5_issue_total: 4,
    step5_revise_rounds: 2,
    total: 26.0,
  })

  assert.equal(model.family, 'thinking-step')
  assert.equal(model.totalMs, 26000)
  assert.equal(model.totalLabel, '26.0s')
  assert.equal(model.slowest.key, 'step4_synthesis')
  assert.equal(model.entries.find((entry) => entry.key === 'step3_retrieval').description, '检索补充')
  assert.equal(model.entries.find((entry) => entry.key === 'step5_check_loop_1').durationLabel, '1.4s')
  assert.equal(model.entries.some((entry) => entry.key === 'step5_issue_total'), false)
  assert.equal(model.entries.some((entry) => entry.key === 'step5_revise_rounds'), false)
})

test('formats durations for ms, seconds, and minutes', () => {
  assert.equal(formatStageDuration(8.9), '9ms')
  assert.equal(formatStageDuration(1200), '1.2s')
  assert.equal(formatStageDuration(78811), '1m18.8s')
})

test('extracts timings from message metadata fallbacks', () => {
  const model = getMessageStageTimingModel({
    metadata: {
      stage_timings_ms: { stage2: 1000 },
    },
  })

  assert.equal(model.hasTimings, true)
  assert.equal(model.entries[0].durationLabel, '1.0s')
})

test('ignores invalid timing values', () => {
  const model = normalizeStageTimings({
    stage1: 'bad',
    stage2: -1,
    stage3: Number.POSITIVE_INFINITY,
    stage4: 20,
  })

  assert.deepEqual(model.entries.map((entry) => entry.key), ['stage4'])
})
