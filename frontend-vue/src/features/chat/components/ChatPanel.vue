<script setup>
import { computed, nextTick, ref, watch } from 'vue';
import { formatAnswer } from '../../../utils';

const props = defineProps({
  session: { type: Object, required: true },
  streaming: { type: Boolean, default: false },
});

const emit = defineEmits(['send', 'stop']);

const draft = ref('');
const messagesRef = ref(null);

const renderedMessages = computed(() => {
  return (props.session?.messages || []).map((msg) => ({
    ...msg,
    html: formatAnswer(msg.content || ''),
  }));
});

function trySend() {
  const value = draft.value.trim();
  if (!value || props.streaming) {
    return;
  }
  emit('send', value);
  draft.value = '';
}

function onEnter(event) {
  if (event.shiftKey) return;
  event.preventDefault();
  trySend();
}

async function scrollToBottom() {
  await nextTick();
  const el = messagesRef.value;
  if (el) {
    el.scrollTop = el.scrollHeight;
  }
}

watch(() => props.session?.messages?.length, scrollToBottom);
watch(() => props.streaming, scrollToBottom);
</script>

<template>
  <section class="chat-main">
    <header class="chat-header">
      <h2>{{ session?.title || '会话' }}</h2>
      <div class="status" :class="{ active: streaming }">{{ streaming ? '流式输出中' : '就绪' }}</div>
    </header>

    <div ref="messagesRef" class="messages">
      <article
        v-for="(msg, idx) in renderedMessages"
        :key="`${msg.ts}-${idx}`"
        :class="['msg', msg.role]"
      >
        <div class="role">{{ msg.role === 'user' ? '你' : '助手' }}</div>
        <div class="bubble markdown-body" v-html="msg.html"></div>
      </article>
    </div>

    <footer class="composer">
      <textarea
        v-model="draft"
        class="editor"
        rows="3"
        placeholder="输入问题，Enter 发送，Shift+Enter 换行"
        @keydown.enter="onEnter"
      />
      <div class="composer-actions">
        <button class="action-btn ghost" :disabled="!streaming" @click="emit('stop')">停止</button>
        <button class="action-btn" :disabled="streaming" @click="trySend">发送</button>
      </div>
    </footer>
  </section>
</template>
