const EMPTY_MODEL = Object.freeze({
  hasTimings: false,
  family: 'generic',
  totalMs: 0,
  totalLabel: '',
  slowest: null,
  entries: [],
})

const GENERATION_STAGE_META = {
  stage1: { label: '阶段一', description: '问题规划与检索词生成', order: 10, unit: 'ms' },
  stage2: { label: '阶段二', description: '向量检索与重排', order: 20, unit: 'ms' },
  stage25: { label: '阶段二点五', description: '证据筛选与上下文压缩', order: 25, unit: 'ms' },
  stage3: { label: '阶段三', description: '提示词构建', order: 30, unit: 'ms' },
  stage35: { label: '阶段3.5', description: '候选证据重排', order: 35, unit: 'ms' },
  stage4: { label: '阶段四', description: '答案生成', order: 40, unit: 'ms' },
}

const THINKING_STEP_META = {
  step1_parallel: { label: 'Step 1', description: '直答与拆解并行', order: 10, unit: 's' },
  step2_pre_answer: { label: 'Step 2', description: '初步回答', order: 20, unit: 's' },
  step3_retrieval: { label: 'Step 3', description: '检索补充', order: 30, unit: 's' },
  step4_synthesis: { label: 'Step 4', description: '综合生成', order: 40, unit: 's' },
  step5_check_revise: { label: 'Step 5', description: '检查与修订', order: 50, unit: 's' },
  step5_check_total: { label: '检查累计', description: '质量检查累计', order: 60, unit: 's' },
  step5_revise_total: { label: '修订累计', description: '修订生成累计', order: 70, unit: 's' },
  total: { label: '总耗时', description: '全流程总耗时', order: 1000, unit: 's' },
}

const THINKING_COUNTER_KEYS = new Set([
  'step5_issue_total',
  'step5_revise_rounds',
])

const GENERATION_STEP_KEY_BY_TITLE = {
  阶段一: 'stage1',
  阶段二: 'stage2',
  阶段二点五: 'stage25',
  阶段2点5: 'stage25',
  '阶段2.5': 'stage25',
  阶段三: 'stage3',
  阶段三点五: 'stage35',
  阶段3点5: 'stage35',
  '阶段3.5': 'stage35',
  阶段四: 'stage4',
}

const THINKING_TIMING_KEYS_BY_STEP = {
  step1: ['step1_parallel'],
  step2: ['step2_pre_answer'],
  step3: ['step3_retrieval'],
  step4: ['step4_synthesis'],
  step5: ['step5_check_revise', 'step5_check_total', 'step5_revise_total'],
  step5_check: ['step5_check_total', 'step5_check_revise'],
  step5_revise: ['step5_revise_total', 'step5_check_revise'],
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function toFiniteNonNegativeNumber(value) {
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? number : null
}

function detectFamily(rawTimings) {
  const keys = Object.keys(rawTimings || {})
  if (keys.some((key) => Object.prototype.hasOwnProperty.call(GENERATION_STAGE_META, key))) {
    return 'generation-stage'
  }
  if (keys.some((key) => Object.prototype.hasOwnProperty.call(THINKING_STEP_META, key))) {
    return 'thinking-step'
  }
  return 'generic'
}

function getTimingMeta(key, family) {
  if (family === 'generation-stage' && GENERATION_STAGE_META[key]) {
    return GENERATION_STAGE_META[key]
  }
  if (family === 'thinking-step' && THINKING_STEP_META[key]) {
    return THINKING_STEP_META[key]
  }
  if (family === 'thinking-step' && key.startsWith('step')) {
    return { label: key, description: '', order: 900, unit: 's' }
  }
  if (key.endsWith('_ms')) {
    return { label: key, description: '', order: 900, unit: 'ms' }
  }
  if (key.endsWith('_s')) {
    return { label: key, description: '', order: 900, unit: 's' }
  }
  return { label: key, description: '', order: 900, unit: 'ms' }
}

function toDurationMs(value, unit) {
  return unit === 's' ? value * 1000 : value
}

export function formatStageDuration(durationMs) {
  const ms = toFiniteNonNegativeNumber(durationMs)
  if (ms === null) return ''
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  const minutes = Math.floor(ms / 60000)
  const seconds = (ms - minutes * 60000) / 1000
  return `${minutes}m${seconds.toFixed(1)}s`
}

export function normalizeStageTimings(rawTimings) {
  if (!isPlainObject(rawTimings)) return { ...EMPTY_MODEL, entries: [] }

  const family = detectFamily(rawTimings)
  const entries = []
  for (const [key, rawValue] of Object.entries(rawTimings)) {
    if (family === 'thinking-step' && THINKING_COUNTER_KEYS.has(key)) continue
    const value = toFiniteNonNegativeNumber(rawValue)
    if (value === null) continue
    const meta = getTimingMeta(key, family)
    const durationMs = toDurationMs(value, meta.unit)
    entries.push({
      key,
      label: meta.label,
      description: meta.description,
      durationMs,
      durationLabel: formatStageDuration(durationMs),
      displayOrder: meta.order,
    })
  }

  entries.sort((left, right) => {
    if (left.displayOrder !== right.displayOrder) return left.displayOrder - right.displayOrder
    return left.key.localeCompare(right.key)
  })

  if (entries.length === 0) return { ...EMPTY_MODEL, entries: [] }

  const explicitTotal = entries.find((entry) => entry.key === 'total')
  const totalMs = explicitTotal
    ? explicitTotal.durationMs
    : entries.reduce((sum, entry) => sum + entry.durationMs, 0)
  const stageEntries = entries.filter((entry) => entry.key !== 'total')
  const slowest = stageEntries.reduce((current, entry) => {
    if (!current || entry.durationMs > current.durationMs) return entry
    return current
  }, null)

  return {
    hasTimings: true,
    family,
    totalMs,
    totalLabel: formatStageDuration(totalMs),
    slowest,
    entries,
  }
}

export function getMessageStageTimingModel(message) {
  const metadata = isPlainObject(message?.metadata) ? message.metadata : {}
  const rawTimings = {
    ...(isPlainObject(message?.timings) ? message.timings : {}),
    ...(isPlainObject(metadata.stage_timings_ms) ? metadata.stage_timings_ms : {}),
    ...(isPlainObject(metadata.timings) ? metadata.timings : {}),
  }
  return normalizeStageTimings(rawTimings)
}

function normalizeStepTitleForTiming(step = {}) {
  const rawTitle = String(step?.title || '').trim()
  const rawMessage = String(step?.message || step?.content || '').trim()
  const title = rawTitle || rawMessage.split(/[：:]/)[0] || ''
  return title.replace(/\s+/g, '')
}

export function getStepTimingDurationLabel(message, step = {}) {
  const stepData = isPlainObject(step?.data) ? step.data : {}
  const stepElapsedMs = toFiniteNonNegativeNumber(stepData.elapsed_ms ?? stepData.elapsedMs)
  if (stepElapsedMs !== null) {
    return formatStageDuration(stepElapsedMs)
  }
  const stepKey = String(step?.step || '').trim()
  const title = normalizeStepTitleForTiming(step)
  const candidateKeys = [
    stepKey,
    GENERATION_STEP_KEY_BY_TITLE[title],
    ...(THINKING_TIMING_KEYS_BY_STEP[stepKey] || []),
  ].filter(Boolean)
  const model = getMessageStageTimingModel(message)
  const entriesByKey = new Map()
  model.entries.forEach((timing) => {
    entriesByKey.set(timing.key, timing)
    entriesByKey.set(String(timing.label || '').replace(/\s+/g, ''), timing)
  })
  const entry = candidateKeys.map((key) => entriesByKey.get(key)).find(Boolean)
  return entry?.durationLabel || ''
}
