<script setup>
import KatexMath from './KatexMath.vue'

const props = defineProps({
  tokens: { type: Array, default: () => [] },
})

const emit = defineEmits(['open-doi', 'open-patent'])

function safeLinkHref(href) {
  const value = String(href || '').trim()
  if (!value) return ''
  if (/^(?:https?:|mailto:|\/|#)/i.test(value)) return value
  return ''
}

function onDoi(token) {
  const doi = String(token?.doi || token?.text || '').trim()
  if (doi) emit('open-doi', doi)
}

function onPatent(token) {
  const patentId = String(token?.patentId || token?.text || '').trim()
  if (patentId) emit('open-patent', patentId)
}
</script>

<template>
  <template v-for="(token, index) in props.tokens" :key="`${token?.type || 'token'}-${index}-${token?.raw || token?.text || ''}`">
    <template v-if="token.type === 'text' || token.type === 'escape' || token.type === 'html'">{{ token.text || token.raw || '' }}</template>
    <br v-else-if="token.type === 'br'">
    <strong v-else-if="token.type === 'strong'">
      <MarkdownInlineTokens :tokens="token.tokens || []" @open-doi="emit('open-doi', $event)" @open-patent="emit('open-patent', $event)" />
    </strong>
    <em v-else-if="token.type === 'em'">
      <MarkdownInlineTokens :tokens="token.tokens || []" @open-doi="emit('open-doi', $event)" @open-patent="emit('open-patent', $event)" />
    </em>
    <del v-else-if="token.type === 'del'">
      <MarkdownInlineTokens :tokens="token.tokens || []" @open-doi="emit('open-doi', $event)" @open-patent="emit('open-patent', $event)" />
    </del>
    <code v-else-if="token.type === 'codespan'">{{ token.text || '' }}</code>
    <a
      v-else-if="token.type === 'link' && safeLinkHref(token.href)"
      :href="safeLinkHref(token.href)"
      :title="token.title || undefined"
      target="_blank"
      rel="noreferrer noopener"
    >
      <MarkdownInlineTokens :tokens="token.tokens || []" @open-doi="emit('open-doi', $event)" @open-patent="emit('open-patent', $event)" />
    </a>
    <span v-else-if="token.type === 'link'">
      <MarkdownInlineTokens :tokens="token.tokens || []" @open-doi="emit('open-doi', $event)" @open-patent="emit('open-patent', $event)" />
    </span>
    <button v-else-if="token.type === 'doiLink'" type="button" class="doi-link markdown-link-button" :data-doi="token.doi" @click="onDoi(token)">
      {{ token.text || token.doi }}
    </button>
    <button v-else-if="token.type === 'patentLink'" type="button" class="doi-link patent-link markdown-link-button" :data-patent-id="token.patentId" @click="onPatent(token)">
      {{ token.text || token.patentId }}
    </button>
    <KatexMath v-else-if="token.type === 'inlineMath'" :text="token.text || ''" />
    <template v-else>{{ token.text || token.raw || '' }}</template>
  </template>
</template>
