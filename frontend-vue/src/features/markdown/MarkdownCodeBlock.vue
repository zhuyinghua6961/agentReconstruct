<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  code: { type: String, default: '' },
  language: { type: String, default: '' },
})

const copied = ref(false)
let copiedTimer = null

const languageLabel = computed(() => {
  const value = String(props.language || '').trim().split(/\s+/)[0]
  return value || 'text'
})

async function copyCode() {
  const text = String(props.code || '')
  if (typeof navigator?.clipboard?.writeText === 'function') {
    await navigator.clipboard.writeText(text)
  }
  copied.value = true
  if (copiedTimer) window.clearTimeout(copiedTimer)
  copiedTimer = window.setTimeout(() => {
    copied.value = false
    copiedTimer = null
  }, 1200)
}
</script>

<template>
  <div class="markdown-code-block">
    <div class="markdown-code-toolbar">
      <span class="markdown-code-language">{{ languageLabel }}</span>
      <button type="button" class="markdown-code-copy" @click="copyCode">
        {{ copied ? '已复制' : '复制' }}
      </button>
    </div>
    <pre><code :class="language ? `language-${languageLabel}` : ''">{{ code }}</code></pre>
  </div>
</template>
