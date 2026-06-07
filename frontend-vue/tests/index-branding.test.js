import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = dirname(fileURLToPath(import.meta.url))
const frontendRoot = join(currentDir, '..')
const source = readFileSync(join(frontendRoot, 'index.html'), 'utf8')

test('index document exposes the product title and favicon', () => {
  assert.match(source, /<title>磷酸铁锂知识图谱<\/title>/)
  assert.match(source, /<link\s+rel="icon"\s+type="image\/svg\+xml"\s+href="\/favicon\.svg"\s*\/?>/)
  assert.ok(existsSync(join(frontendRoot, 'public', 'favicon.svg')))
})
