import test from 'node:test'
import assert from 'node:assert/strict'

import { formatStreamingAnswer } from '../src/utils/index.js'

function installMinimalDom() {
  global.document = {
    createElement() {
      let text = ''
      return {
        set textContent(value) {
          text = String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;')
        },
        get innerHTML() {
          return text
        },
      }
    },
  }
}

test('formatStreamingAnswer renders markdown tables during streaming', () => {
  installMinimalDom()
  const input = [
    '| 材料 | 厚度 |',
    '| --- | --- |',
    '| 电极A | 120 um |',
    '| 电极B | 180 um |',
    '',
    '[DOI: 10.1000/demo]',
  ].join('\n')

  const html = formatStreamingAnswer(input)

  assert.match(html, /<table>/i)
  assert.match(html, /<td>120 um<\/td>/i)
  assert.match(html, /class="doi-link"/i)
  assert.match(html, /data-doi="10\.1000\/demo"/i)
})
