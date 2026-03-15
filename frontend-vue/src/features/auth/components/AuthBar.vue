<script setup>
import { ref } from 'vue';

defineProps({
  user: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
});

const emit = defineEmits(['login', 'register', 'logout', 'refresh-quota']);

const username = ref('');
const password = ref('');

function onLogin() {
  emit('login', {
    username: username.value.trim(),
    password: password.value,
  });
}

function onRegister() {
  emit('register', {
    username: username.value.trim(),
    password: password.value,
  });
}
</script>

<template>
  <section class="auth-bar">
    <template v-if="user">
      <div class="auth-user">
        <strong>{{ user.username }}</strong>
        <span>({{ user.role }})</span>
      </div>
      <div class="auth-actions">
        <button class="action-btn ghost" :disabled="loading" @click="emit('refresh-quota')">
          刷新配额
        </button>
        <button class="action-btn ghost" :disabled="loading" @click="emit('logout')">
          退出
        </button>
      </div>
    </template>
    <template v-else>
      <input
        v-model="username"
        class="auth-input"
        placeholder="用户名"
        autocomplete="username"
      />
      <input
        v-model="password"
        class="auth-input"
        placeholder="密码"
        type="password"
        autocomplete="current-password"
      />
      <div class="auth-actions">
        <button class="action-btn" :disabled="loading" @click="onLogin">登录</button>
        <button class="action-btn ghost" :disabled="loading" @click="onRegister">注册</button>
      </div>
    </template>
    <p v-if="error" class="auth-error">{{ error }}</p>
  </section>
</template>

