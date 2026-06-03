<script setup>
import KatexMath from './KatexMath.vue'
import MarkdownCodeBlock from './MarkdownCodeBlock.vue'
import MarkdownInlineTokens from './MarkdownInlineTokens.vue'

const props = defineProps({
  tokens: { type: Array, default: () => [] },
})

const emit = defineEmits(['open-doi', 'open-patent'])

function headingTag(token) {
  const depth = Math.min(6, Math.max(1, Number(token?.depth || 1)))
  return `h${depth}`
}

function listTag(token) {
  return token?.ordered ? 'ol' : 'ul'
}

function orderedStart(token) {
  const start = Number(token?.start || 0)
  return token?.ordered && start > 1 ? start : undefined
}

function cellAlign(cell) {
  const align = String(cell?.align || '').trim()
  return align ? { textAlign: align } : null
}
</script>

<template>
  <template v-for="(token, index) in props.tokens" :key="`${token?.type || 'token'}-${index}-${token?.raw || token?.text || ''}`">
    <hr v-if="token.type === 'hr'">

    <component :is="headingTag(token)" v-else-if="token.type === 'heading'">
      <MarkdownInlineTokens :tokens="token.tokens || []" @open-doi="emit('open-doi', $event)" @open-patent="emit('open-patent', $event)" />
    </component>

    <p v-else-if="token.type === 'paragraph'">
      <MarkdownInlineTokens :tokens="token.tokens || []" @open-doi="emit('open-doi', $event)" @open-patent="emit('open-patent', $event)" />
    </p>

    <template v-else-if="token.type === 'text'">
      <MarkdownInlineTokens v-if="token.tokens" :tokens="token.tokens" @open-doi="emit('open-doi', $event)" @open-patent="emit('open-patent', $event)" />
      <p v-else>{{ token.text || token.raw || '' }}</p>
    </template>

    <component :is="listTag(token)" v-else-if="token.type === 'list'" :start="orderedStart(token)">
      <li v-for="(item, itemIndex) in token.items || []" :key="`${item.raw || item.text || ''}-${itemIndex}`">
        <MarkdownTokens :tokens="item.tokens || []" @open-doi="emit('open-doi', $event)" @open-patent="emit('open-patent', $event)" />
      </li>
    </component>

    <blockquote v-else-if="token.type === 'blockquote'">
      <MarkdownTokens :tokens="token.tokens || []" @open-doi="emit('open-doi', $event)" @open-patent="emit('open-patent', $event)" />
    </blockquote>

    <MarkdownCodeBlock
      v-else-if="token.type === 'code'"
      :code="token.text || ''"
      :language="token.lang || ''"
    />

    <div v-else-if="token.type === 'table'" class="markdown-table-scroll">
      <table>
        <thead>
          <tr>
            <th v-for="(cell, cellIndex) in token.header || []" :key="`h-${cellIndex}`" :style="cellAlign(cell)">
              <MarkdownInlineTokens :tokens="cell.tokens || []" @open-doi="emit('open-doi', $event)" @open-patent="emit('open-patent', $event)" />
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, rowIndex) in token.rows || []" :key="`r-${rowIndex}`">
            <td v-for="(cell, cellIndex) in row" :key="`r-${rowIndex}-${cellIndex}`" :style="cellAlign(cell)">
              <MarkdownInlineTokens :tokens="cell.tokens || []" @open-doi="emit('open-doi', $event)" @open-patent="emit('open-patent', $event)" />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <p v-else-if="token.type === 'html'">{{ token.text || token.raw || '' }}</p>

    <KatexMath
      v-else-if="token.type === 'math'"
      :text="token.text || ''"
      display
    />
  </template>
</template>
