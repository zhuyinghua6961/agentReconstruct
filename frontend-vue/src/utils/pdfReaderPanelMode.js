export function resolvePdfReaderInitialPanelMode(_locationHints) {
  return 'summary'
}

export function isPdfReaderPanelActive(currentMode, expectedMode) {
  return String(currentMode || '').trim() === String(expectedMode || '').trim()
}
