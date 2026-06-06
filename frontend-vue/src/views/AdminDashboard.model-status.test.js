import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import assert from 'node:assert/strict'

const currentDir = dirname(fileURLToPath(import.meta.url))
const adminSource = readFileSync(join(currentDir, 'AdminDashboard.vue'), 'utf8')
const adminServiceSource = readFileSync(join(currentDir, '../services/admin.js'), 'utf8')

test('AdminDashboard exposes model status tab and model test action', () => {
  assert.match(adminSource, /key:\s*'models'/)
  assert.match(adminSource, /模型状态/)
  assert.match(adminSource, /activeAdminTab === 'models'/)
  assert.match(adminSource, /fetchModelStatus/)
  assert.match(adminSource, /testModelEndpoint/)
  assert.match(adminSource, /点击测试/)
  assert.match(adminSource, /auth_mode/)
  assert.match(adminSource, /模式/)
  assert.match(adminSource, /model-endpoint-list/)
  assert.match(adminSource, /model-endpoint-card/)
  assert.match(adminSource, /model-route-grid/)
  assert.match(adminSource, /formatModelDimensionSummary/)
  assert.match(adminSource, /detected_dimension/)
  assert.match(adminSource, /expected_dimension/)
  assert.doesNotMatch(adminSource, /<table class="model-status-table"/)
  assert.doesNotMatch(adminSource, /item\.provider/)
})

test('adminApi exposes model status list and test endpoints', () => {
  assert.match(adminServiceSource, /getModelStatus/)
  assert.match(adminServiceSource, /testModelStatus/)
  assert.match(adminServiceSource, /\/model-status/)
  assert.match(adminServiceSource, /\/model-status\/test/)
})
