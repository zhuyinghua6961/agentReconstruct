import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))

function readSource(relativePath) {
  const fullPath = join(currentDir, ...relativePath)
  if (!existsSync(fullPath)) {
    return ''
  }
  return readFileSync(fullPath, 'utf8')
}

const panelSource = readSource(['UsageStatsPanel.vue'])
const adminSource = readSource(['..', 'views', 'AdminDashboard.vue'])
const adminServiceSource = readSource(['..', 'services', 'admin.js'])
const heartbeatSource = readSource(['..', 'composables', 'useActivityHeartbeat.js'])
const activitySource = readSource(['..', 'services', 'activity.js'])
const appSource = readSource(['..', 'App.vue'])

test('UsageStatsPanel exposes usage metrics columns', () => {
  assert.match(panelSource, /文献检索/)
  assert.match(panelSource, /专利检索/)
  assert.match(panelSource, /活跃使用/)
  assert.match(panelSource, /15 分钟无操作/)
  assert.match(panelSource, /普通问答/)
  assert.match(panelSource, /文件问答/)
  assert.match(panelSource, /getUsageStats/)
  assert.match(panelSource, /exportUsageStats/)
  assert.match(panelSource, /导出 CSV/)
  assert.match(panelSource, /导出 Excel/)
  assert.match(panelSource, /sort_by/)
})

test('AdminDashboard includes usage stats tab', () => {
  assert.match(adminSource, /UsageStatsPanel/)
  assert.match(adminSource, /数据统计/)
  assert.match(adminSource, /activeAdminTab === 'stats'/)
})

test('admin service exposes usage stats api', () => {
  assert.match(adminServiceSource, /getUsageStats/)
  assert.match(adminServiceSource, /exportUsageStats/)
  assert.match(adminServiceSource, /\/usage-stats/)
})

test('activity heartbeat is mounted globally for authenticated routes', () => {
  assert.match(appSource, /useActivityHeartbeat/)
  assert.match(heartbeatSource, /markInteraction/)
  assert.match(heartbeatSource, /IDLE_TIMEOUT_MS/)
  assert.match(heartbeatSource, /LEADER_STORAGE_KEY/)
  assert.match(heartbeatSource, /finalizeSent/)
  assert.match(activitySource, /\/heartbeat/)
})
