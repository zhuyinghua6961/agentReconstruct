<script setup>
defineProps({
  sessions: { type: Array, required: true },
  activeSessionId: { type: String, required: true },
});

const emit = defineEmits(['new', 'select', 'delete', 'clear-all']);

const sidebarTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

function formatTime(ts) {
  const date = new Date(ts)
  if (Number.isNaN(date.getTime())) return ''
  return sidebarTimeFormatter.format(date)
}
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-head">
      <h1>会话</h1>
      <button class="text-btn danger" @click="emit('clear-all')">清空</button>
    </div>

    <button class="new-btn" @click="emit('new')">+ 新建会话</button>

    <ul class="session-list">
      <li
        v-for="item in sessions"
        :key="item.id"
        :class="['session-item', { active: item.id === activeSessionId }]"
        @click="emit('select', item.id)"
      >
        <div class="title" :title="item.title">{{ item.title }}</div>
        <div class="meta-row">
          <span class="time">{{ formatTime(item.createdAt) }}</span>
          <button
            class="text-btn"
            @click.stop="emit('delete', item.id)"
          >删除</button>
        </div>
      </li>
    </ul>
  </aside>
</template>
