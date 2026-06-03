<script setup>
import { computed } from 'vue'
import { renderKatexToHtml } from './markdownPipeline.js'

const props = defineProps({
  text: { type: String, default: '' },
  display: { type: Boolean, default: false },
})

const html = computed(() => renderKatexToHtml(props.text, props.display))
</script>

<template>
  <span
    v-if="!display"
    class="math-inline"
    v-html="html"
  ></span>
  <div
    v-else
    class="math-block"
    v-html="html"
  ></div>
</template>
