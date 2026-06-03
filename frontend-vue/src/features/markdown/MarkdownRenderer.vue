<script setup>
import { computed } from 'vue'
import 'katex/dist/katex.min.css'
import MarkdownTokens from './MarkdownTokens.vue'
import { parseMarkdownContent } from './markdownPipeline.js'

const props = defineProps({
  content: { type: String, default: '' },
  tokens: { type: Array, default: null },
  streaming: { type: Boolean, default: false },
  variant: { type: String, default: 'message' },
})

const emit = defineEmits(['open-doi', 'open-patent'])

const renderModel = computed(() => {
  if (Array.isArray(props.tokens)) {
    return {
      normalized: '',
      tokens: props.tokens,
      diagnostics: {},
    }
  }
  return parseMarkdownContent(props.content, {
    streaming: props.streaming,
    variant: props.variant,
  })
})

const classNames = computed(() => [
  'markdown-renderer',
  `markdown-renderer-${props.variant || 'message'}`,
  {
    'markdown-renderer-streaming': props.streaming,
  },
])
</script>

<template>
  <div :class="classNames">
    <MarkdownTokens
      :tokens="renderModel.tokens"
      @open-doi="emit('open-doi', $event)"
      @open-patent="emit('open-patent', $event)"
    />
  </div>
</template>
