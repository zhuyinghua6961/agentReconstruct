import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))

function readSource(filename) {
  const fullPath = join(currentDir, filename)
  if (!existsSync(fullPath)) {
    return ''
  }
  return readFileSync(fullPath, 'utf8')
}

const panelSource = readSource('PersonnelManagementPanel.vue')
const lookupSource = readSource('PersonnelLookupSelect.vue')
const batchImportSource = readSource('PersonnelBatchImportDialog.vue')
const importResultSource = readSource('PersonnelImportResultDialog.vue')
const adminServiceSource = readSource('../services/admin.js')

test('PersonnelManagementPanel renders account list filters and status filter', () => {
  assert.match(panelSource, /searchEmployeeNo/)
  assert.match(panelSource, /searchFullName/)
  assert.match(panelSource, /statusFilter/)
  assert.match(panelSource, /工号/)
  assert.match(panelSource, /姓名/)
  assert.match(panelSource, /状态/)
})

test('PersonnelManagementPanel exposes create edit status and bindings actions', () => {
  assert.match(panelSource, /handleCreatePersonnel/)
  assert.match(panelSource, /handleEditPersonnel/)
  assert.match(panelSource, /handleTogglePersonnelStatus/)
  assert.match(panelSource, /toggleBindings/)
  assert.match(panelSource, /绑定账号数/)
})

test('PersonnelManagementPanel wires batch import and template download', () => {
  assert.match(panelSource, /PersonnelBatchImportDialog/)
  assert.match(panelSource, /PersonnelImportResultDialog/)
  assert.match(panelSource, /downloadPersonnelImportTemplate/)
  assert.match(panelSource, /handlePersonnelImportSuccess/)
  assert.match(batchImportSource, /employee_no/)
  assert.match(batchImportSource, /full_name/)
  assert.match(batchImportSource, /verification_code/)
  assert.match(batchImportSource, /status/)
  assert.match(importResultSource, /工号/)
  assert.match(importResultSource, /姓名/)
})

test('Personnel import result dialog supports created updated summary and statuses', () => {
  assert.match(importResultSource, /getPersonnelImportSuccessCount/)
  assert.match(importResultSource, /filterPersonnelImportDetails/)
  assert.match(importResultSource, /getPersonnelImportResultText/)
})

test('PersonnelManagementPanel lazy loads bindings when expanding a personnel row', () => {
  assert.match(panelSource, /expandedPersonnelIds/)
  assert.match(panelSource, /bindingsByPersonnelId/)
  assert.match(panelSource, /bindingsLoadingByPersonnelId/)
  assert.match(panelSource, /bindingsErrorByPersonnelId/)
  assert.match(panelSource, /getPersonnelBindings/)
})

test('PersonnelLookupSelect supports keyword search and active-only selection', () => {
  assert.match(lookupSource, /keyword/)
  assert.match(lookupSource, /fetchOptions\(nextKeyword = keyword\.value, \{ pageSize = 20/)
  assert.match(lookupSource, /getPersonnel\(\{\s*keyword:\s*searchKeyword,\s*status:\s*'active',\s*page_size:\s*pageSize\s*\}\)/)
  assert.match(lookupSource, /employee_no \/\ full_name|employee_no.*full_name/)
  assert.match(lookupSource, /isActiveOption/)
})

test('PersonnelLookupSelect supports parent-preloaded options and local keyword filtering', () => {
  assert.match(lookupSource, /initialOptions/)
  assert.match(lookupSource, /setOptions\(/)
  assert.match(lookupSource, /includes\(normalizedKeyword\)/)
  assert.match(lookupSource, /filteredOptions/)
})

test('admin service exposes personnel management APIs', () => {
  assert.match(adminServiceSource, /getPersonnel\(/)
  assert.match(adminServiceSource, /createPersonnel\(/)
  assert.match(adminServiceSource, /updatePersonnel\(/)
  assert.match(adminServiceSource, /updatePersonnelStatus\(/)
  assert.match(adminServiceSource, /getPersonnelBindings\(/)
  assert.match(adminServiceSource, /batchImportPersonnel\(/)
  assert.match(adminServiceSource, /downloadPersonnelImportTemplate\(/)
  assert.match(adminServiceSource, /bindUserPersonnel\(/)
  assert.match(adminServiceSource, /unbindUserPersonnel\(/)
})
