import test from 'node:test'
import assert from 'node:assert/strict'

function installMinimalDocumentStub() {
  if (globalThis.document?.createElement) return
  globalThis.document = {
    createElement() {
      return {
        _text: '',
        set textContent(value) {
          this._text = String(value ?? '')
        },
        get innerHTML() {
          return this._text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;')
        },
      }
    },
  }
}

async function loadRenderUtils() {
  installMinimalDocumentStub()
  return await import('./index.js')
}

test('legacy patent citations stay clickable without exposing raw patent_id text', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '结论成立 (patent_id=CN100420075C)'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.match(html, /class="doi-link patent-link"/)
    assert.match(html, /data-patent-id="CN100420075C"/)
    assert.match(html, />CN100420075C</)
    assert.doesNotMatch(html, />patent_id=CN100420075C</)
  }
})

test('readable patent citations are linkified in new-path rendering', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '可参考专利号 CN118645714A，并结合 (CN115692635A) 的电压窗口。'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.match(html, /class="doi-link patent-link"/)
    assert.match(html, /data-patent-id="CN118645714A"/)
    assert.match(html, /data-patent-id="CN115692635A"/)
    assert.match(html, />CN118645714A<\/a>/)
    assert.match(html, />CN115692635A<\/a>/)
    assert.doesNotMatch(html, /patent_id=/)
  }
})

test('inline bullet patterns after a clause are normalized into a real list', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '结论如下：- 充电上限约 3.65V - 放电下限约 2.5V'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.match(html, /结论如下：/)
    assert.match(html, /<li>充电上限约 3\.65V<\/li>/)
    assert.match(html, /<li>放电下限约 2\.5V<\/li>/)
    assert.doesNotMatch(html, /：- 充电上限/)
  }
})

test('single inline bullet is normalized early during streaming', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '结论如下：- 充电上限约 3.65V'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.match(html, /结论如下：/)
    assert.match(html, /<li>充电上限约 3\.65V<\/li>/)
    assert.doesNotMatch(html, /：- 充电上限/)
  }
})

test('ordinary hyphenated text is not mistaken for an inline list', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '该路线属于高电压-高密度体系，电压范围：-20 至 30 mV 波动。'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.doesNotMatch(html, /<li>/)
    assert.match(html, /高电压-高密度体系/)
    assert.match(html, /-20 至 30 mV 波动/)
  }
})

test('inline list normalization does not split ordinary item separators inside one bullet', async () => {
  const { formatAnswer, formatStreamingAnswer } = await loadRenderUtils()
  const input = '结论如下：- 电压窗口 A - B 段更稳定'

  const finalHtml = formatAnswer(input)
  const streamingHtml = formatStreamingAnswer(input)

  for (const html of [finalHtml, streamingHtml]) {
    assert.match(html, /<li>电压窗口 A - B 段更稳定<\/li>/)
    assert.doesNotMatch(html, /<li>B 段更稳定<\/li>/)
  }
})
