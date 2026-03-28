import test from 'node:test'
import assert from 'node:assert/strict'

import { buildCitationLocationsForDoi } from './citationEvidence.js'

test('returns existing doi_locations when present', () => {
  const locations = buildCitationLocationsForDoi({
    doi: '10.1/a',
    doiLocations: {
      '10.1/a': [{ page: 3, source_text: 'chunk A' }],
    },
    references: [
      { doi: '10.1/a', evidence_text: 'fallback chunk' },
    ],
  })

  assert.deepEqual(locations, [{ page: 3, source_text: 'chunk A' }])
})

test('falls back to richer reference object when doi_locations missing', () => {
  const locations = buildCitationLocationsForDoi({
    doi: '10.1/a',
    doiLocations: {},
    references: [
      {
        doi: '10.1/a',
        section_name: 'Intro',
        chunk_index: 7,
        evidence_text: '厚电极在高倍率下会出现显著浓差极化。',
        locator_confidence: 'section',
      },
    ],
  })

  assert.deepEqual(locations, [
    {
      section: 'Intro',
      chunk_index: 7,
      source_text: '厚电极在高倍率下会出现显著浓差极化。',
      source_preview: '厚电极在高倍率下会出现显著浓差极化。',
      confidence: 'section',
    },
  ])
})
