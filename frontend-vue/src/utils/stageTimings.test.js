import test from 'node:test'
import assert from 'node:assert/strict'

import {
  formatStageDuration,
  getMessageStageTimingModel,
  getStepTimingDurationLabel,
  normalizeStageTimings,
} from './stageTimings.js'

test('normalizes patent and fastQA stage timings as milliseconds', () => {
  const model = normalizeStageTimings({
    stage1: 12054,
    stage2: 16210,
    stage25: 8.9,
    stage3: 24.5,
    stage35: 230.5,
    stage4: 50524,
  })

  assert.equal(model.hasTimings, true)
  assert.equal(model.family, 'generation-stage')
  assert.equal(model.totalLabel, '1m19.1s')
  assert.equal(model.slowest.key, 'stage4')
  assert.equal(model.slowest.durationLabel, '50.5s')
  assert.deepEqual(model.entries.map((entry) => entry.key), ['stage1', 'stage2', 'stage25', 'stage3', 'stage35', 'stage4'])
  assert.equal(model.entries[4].label, '阶段3.5')
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

test('metadata terminal timings override stale top-level partial timings after reload', () => {
  const model = getMessageStageTimingModel({
    timings: { stage1: 1000 },
    metadata: {
      timings: { stage1: 1100, stage2: 2200 },
    },
  })

  assert.deepEqual(model.entries.map((entry) => [entry.key, entry.durationLabel]), [
    ['stage1', '1.1s'],
    ['stage2', '2.2s'],
  ])
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

test('maps highThinking visible step keys to detailed timing keys', () => {
  const message = {
    timings: {
      step1_parallel: 3.2,
      step2_pre_answer: 1.1,
      step3_retrieval: 7.8,
      step4_synthesis: 11.4,
      step5_check_revise: 2.5,
      total: 26.0,
    },
  }

  assert.equal(getStepTimingDurationLabel(message, { step: 'step1', title: '阶段1' }), '3.2s')
  assert.equal(getStepTimingDurationLabel(message, { step: 'step2', title: '阶段2' }), '1.1s')
  assert.equal(getStepTimingDurationLabel(message, { step: 'step3', title: '阶段3' }), '7.8s')
  assert.equal(getStepTimingDurationLabel(message, { step: 'step4', title: '阶段4' }), '11.4s')
})

test('prefers specific highThinking step5 totals before aggregate fallback', () => {
  const message = {
    timings: {
      step5_check_revise: 9.0,
      step5_check_total: 3.0,
      step5_revise_total: 6.0,
    },
  }

  assert.equal(getStepTimingDurationLabel(message, { step: 'step5_check', title: '阶段5A' }), '3.0s')
  assert.equal(getStepTimingDurationLabel(message, { step: 'step5_revise', title: '阶段5B' }), '6.0s')
  assert.equal(getStepTimingDurationLabel(message, { step: 'step5', title: '阶段5' }), '9.0s')
})

test('maps generation stage titles and keys to stage timing labels', () => {
  const message = {
    metadata: {
      timings: {
        stage1: 1000,
        stage25: 2500,
        stage35: 3500,
      },
    },
  }

  assert.equal(getStepTimingDurationLabel(message, { step: 'stage1', title: '阶段一' }), '1.0s')
  assert.equal(getStepTimingDurationLabel(message, { step: 'stage25', title: '阶段二点五' }), '2.5s')
  assert.equal(getStepTimingDurationLabel(message, { step: 'stage35', title: '阶段3.5' }), '3.5s')
  assert.equal(getStepTimingDurationLabel(message, { step: 'thinking_5', title: '阶段3.5' }), '3.5s')
})

test('uses step data elapsed_ms for intent substep without adding it to total timings', () => {
  const message = {
    metadata: {
      timings: {
        stage1: 1000,
      },
    },
  }

  assert.equal(
    getStepTimingDurationLabel(message, {
      step: 'intent_detect',
      title: '意图识别',
      data: { elapsed_ms: 123.4 },
    }),
    '123ms'
  )
})
