<script setup>
defineProps({
  show: {
    type: Boolean,
    default: false,
  },
  title: {
    type: String,
    default: '强制删除确认',
  },
  impactText: {
    type: String,
    default: '',
  },
  warningText: {
    type: String,
    default: '',
  },
  password: {
    type: String,
    default: '',
  },
  submitting: {
    type: Boolean,
    default: false,
  },
  error: {
    type: String,
    default: '',
  },
  confirmText: {
    type: String,
    default: '确认强制删除',
  },
})

const emit = defineEmits(['cancel', 'confirm', 'update:password'])
</script>

<template>
  <div v-if="show" class="modal-overlay" @click.self="emit('cancel')">
    <div class="force-delete-modal" role="dialog" aria-modal="true" :aria-label="title">
      <div class="modal-header">
        <div>
          <p class="modal-kicker">高风险操作</p>
          <h3>{{ title }}</h3>
        </div>
        <button class="close-btn" type="button" :disabled="submitting" @click="emit('cancel')">x</button>
      </div>

      <div class="modal-body">
        <div class="impact-box">
          <p>{{ impactText }}</p>
          <p v-if="warningText" class="force-delete-warning">{{ warningText }}</p>
        </div>

        <label class="password-field">
          <span>管理员密码</span>
          <input
            :value="password"
            type="password"
            autocomplete="current-password"
            placeholder="输入当前管理员密码"
            :disabled="submitting"
            @input="emit('update:password', $event.target.value)"
            @keydown.enter.prevent="emit('confirm')"
          >
        </label>

        <div v-if="error" class="force-delete-error">
          {{ error }}
        </div>
      </div>

      <div class="modal-footer">
        <button class="btn-secondary" type="button" :disabled="submitting" @click="emit('cancel')">
          取消
        </button>
        <button class="btn-danger" type="button" :disabled="submitting" @click="emit('confirm')">
          {{ submitting ? '删除中...' : confirmText }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(17, 24, 39, 0.48);
  padding: 20px;
}

.force-delete-modal {
  width: min(520px, 100%);
  max-height: calc(100vh - 40px);
  overflow-y: auto;
  border-radius: 8px;
  background: white;
  box-shadow: 0 24px 48px rgba(15, 23, 42, 0.22);
}

.modal-header,
.modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 20px;
}

.modal-header {
  border-bottom: 1px solid #e5e7eb;
}

.modal-header h3 {
  margin: 4px 0 0;
  color: #991b1b;
  font-size: 18px;
}

.modal-kicker {
  margin: 0;
  color: #b91c1c;
  font-size: 12px;
  font-weight: 700;
}

.close-btn {
  width: 32px;
  height: 32px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: white;
  color: #6b7280;
  cursor: pointer;
}

.modal-body {
  display: grid;
  gap: 16px;
  padding: 20px;
}

.impact-box {
  display: grid;
  gap: 8px;
  border: 1px solid #fecaca;
  border-radius: 8px;
  background: #fff7f7;
  padding: 14px;
}

.impact-box p {
  margin: 0;
  color: #374151;
  font-size: 14px;
  line-height: 1.6;
}

.force-delete-warning,
.force-delete-error {
  color: #b91c1c;
  font-size: 13px;
}

.password-field {
  display: grid;
  gap: 8px;
}

.password-field span {
  color: #374151;
  font-size: 13px;
  font-weight: 700;
}

.password-field input {
  border: 1px solid #d1d5db;
  border-radius: 8px;
  padding: 10px 12px;
}

.modal-footer {
  justify-content: flex-end;
  border-top: 1px solid #e5e7eb;
}

.btn-secondary,
.btn-danger {
  border: 1px solid transparent;
  border-radius: 8px;
  cursor: pointer;
  padding: 9px 14px;
}

.btn-secondary {
  border-color: #d1d5db;
  background: #f3f4f6;
  color: #374151;
}

.btn-danger {
  background: #dc2626;
  color: white;
}

.btn-danger:hover:not(:disabled) {
  background: #b91c1c;
}

.btn-secondary:disabled,
.btn-danger:disabled,
.close-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
</style>
