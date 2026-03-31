import test from 'node:test'
import assert from 'node:assert/strict'

import { formatAnswer, formatStreamingAnswer } from '../src/utils/index.js'

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

test('formatAnswer repairs polluted DOI links and splits concatenated DOI tokens', () => {
  installMinimalDom()
  const input = [
    '[DOI: 10.1016j.est.2024.113859]',
    '[DOI: 10.1016j.jpowsour.2005.03.09910.1016j.jpowsour.2013.06.070]',
  ].join(' ')

  const html = formatAnswer(input)

  assert.match(html, /data-doi="10\.1016\/j\.est\.2024\.113859"/i)
  assert.match(html, /data-doi="10\.1016\/j\.jpowsour\.2005\.03\.099"/i)
  assert.match(html, /data-doi="10\.1016\/j\.jpowsour\.2013\.06\.070"/i)
  assert.doesNotMatch(html, /data-doi="10\.1016j/i)
})

test('formatAnswer renders DOI links with parenthesized suffix intact', () => {
  installMinimalDom()
  const input = '参考文献 (doi=10.1016/S0378-7753(03)00297-0)'

  const html = formatAnswer(input)

  assert.match(html, /data-doi="10\.1016\/S0378-7753\(03\)00297-0"/i)
  assert.match(html, />10\.1016\/S0378-7753\(03\)00297-0<\/a>/i)
})

test('formatAnswer renders inline formulas with superscript and subscript', () => {
  installMinimalDom()
  const input = '容量衰减可写作 $Q_{loss} = k x^2$，材料可表示为 Li_{1-x}CoO_2。'

  const html = formatAnswer(input)

  assert.doesNotMatch(html, /\$Q_\{loss\} = k x\^2\$/)
  assert.match(html, /<sub>loss<\/sub>/i)
  assert.match(html, /<sup>2<\/sup>/i)
  assert.match(html, /Li<sub>1-x<\/sub>CoO<sub>2<\/sub>/i)
})

test('formatStreamingAnswer preserves inline math markup during streaming', () => {
  installMinimalDom()
  const input = '容量衰减满足 $Q_{loss} = k x^2$。'

  const html = formatStreamingAnswer(input)

  assert.match(html, /<sub>loss<\/sub>/i)
  assert.match(html, /<sup>2<\/sup>/i)
})

test('formatStreamingAnswer does not render malformed DOI underscores as subscripts', () => {
  installMinimalDom()
  const input = '参考 doi:10.10881742-6596_25841_012046) 的实验设置。'

  const html = formatStreamingAnswer(input)

  assert.match(html, /10\.10881742-6596_25841_012046\)/i)
  assert.doesNotMatch(html, /<sub>/i)
})

test('formatAnswer does not treat raw DOI fragments as math markup', () => {
  installMinimalDom()
  const input = '补充参考 doi:10.1039c2jm15273h 和 doi:10.1016\/S0378-7753(03)00297-0。'

  const html = formatAnswer(input)

  assert.match(html, /10\.1039c2jm15273h/i)
  assert.match(html, /10\.1016\/S0378-7753\(03\)00297-0/i)
  assert.doesNotMatch(html, /<sub>|<sup>/i)
})

test('formatAnswer still renders ordinary numeric math starting with 10 dot as superscript', () => {
  installMinimalDom()
  const input = '倍率关系可写作 $10.2^3$。'

  const html = formatAnswer(input)

  assert.match(html, /10\.2<sup>3<\/sup>/i)
})
