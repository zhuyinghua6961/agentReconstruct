export function resolvePdfReaderInitialPanelMode(locationHints) {
  return Array.isArray(locationHints) && locationHints.length > 0 ? 'citations' : 'summary'
}

export function isPdfReaderPanelActive(currentMode, expectedMode) {
  return String(currentMode || '').trim() === String(expectedMode || '').trim()
}
