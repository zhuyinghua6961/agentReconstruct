import test from 'node:test'
import assert from 'node:assert/strict'

async function loadChatRequestContextUtils() {
  try {
    return await import('./chatRequestContext.js')
  } catch {
    return {}
  }
}

test('buildChatRequestContext uses the originating chat snapshot instead of ambient current-chat state', async () => {
  const { buildChatRequestContext } = await loadChatRequestContextUtils()

  assert.equal(typeof buildChatRequestContext, 'function')

  const requestChat = {
    pdf_list: [{ file_id: 11 }, { file_id: 12 }],
    excel_list: [{ file_id: 22 }],
    messages: [
      { role: 'user', content: 'older question' },
      {
        role: 'assistant',
        content: 'older answer',
        metadata: {
          route: 'thinking',
          used_files: [{ file_id: 12 }, { file_id: 22 }, { file_id: 12 }],
        },
      },
    ],
  }
  const otherCurrentChat = {
    pdf_list: [{ file_id: 99 }],
    excel_list: [],
    messages: [
      {
        role: 'assistant',
        content: 'other answer',
        metadata: {
          route: 'fast',
          used_files: [{ file_id: 99 }],
        },
      },
    ],
  }
  const sessionState = {
    newlyUploadedPdfIds: [22, 99, 22],
  }

  const context = buildChatRequestContext({
    chat: requestChat,
    sessionState,
    selectedFileIds: [12, 22, 99, 12],
  })

  assert.deepEqual(context, {
    newly_uploaded_ids: [22],
    all_available_ids: [11, 12, 22],
    selected_ids: [12, 22, 99],
    last_focus_ids: [12, 22],
    last_turn_route: 'thinking',
  })
  assert.notDeepEqual(context.all_available_ids, otherCurrentChat.pdf_list.map((item) => item.file_id))
  assert.notEqual(context.last_turn_route, 'fast')
})
