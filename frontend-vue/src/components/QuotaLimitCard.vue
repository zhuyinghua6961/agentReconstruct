<script setup>
const props = defineProps({
  card: { type: Object, required: true },
})
</script>

<template>
  <section class="quota-limit-card" :class="card.variant">
    <div class="quota-limit-card__icon">
      <span v-if="card.variant === 'quota_exceeded'">额度</span>
      <span v-else>状态</span>
    </div>

    <div class="quota-limit-card__body">
      <h3 class="quota-headline">{{ card.headline }}</h3>
      <p v-if="card.description" class="quota-description">{{ card.description }}</p>
      <p v-if="card.usageSummary" class="quota-usage">{{ card.usageSummary }}</p>
      <p v-if="card.resetText" class="quota-reset">{{ card.resetText }} 恢复</p>

      <ul v-if="card.windows && card.windows.length > 0" class="quota-windows">
        <li v-for="item in card.windows" :key="`${item.period}-${item.resetTime}`" class="quota-window-item">
          <span class="quota-window-period">{{ item.period }}</span>
          <span class="quota-window-usage">{{ item.current }} / {{ item.limit }}</span>
          <span class="quota-window-reset">{{ item.resetTime }}</span>
        </li>
      </ul>

      <a class="quota-action" :href="card.action?.to || '/profile'">
        {{ card.action?.label || '去个人中心查看配额' }}
      </a>
    </div>
  </section>
</template>

<style scoped>
.quota-limit-card {
  display: flex;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 14px;
  border: 1px solid #d7d2c5;
  background: linear-gradient(180deg, #fbf8ef 0%, #f4efe2 100%);
  color: #2f2418;
}

.quota-limit-card.quota_exceeded {
  border-color: #d4a66a;
  background: linear-gradient(180deg, #fff7ec 0%, #f6ead8 100%);
}

.quota-limit-card.system_unavailable {
  border-color: #c8d0d8;
  background: linear-gradient(180deg, #f7f8fa 0%, #eceff3 100%);
}

.quota-limit-card__icon {
  flex: 0 0 auto;
  min-width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  background: rgba(47, 36, 24, 0.08);
}

.quota-limit-card__body {
  flex: 1 1 auto;
  min-width: 0;
}

.quota-headline {
  margin: 0;
  font-size: 16px;
  line-height: 1.4;
}

.quota-description,
.quota-usage,
.quota-reset {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.5;
}

.quota-windows {
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 6px;
}

.quota-window-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px 12px;
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.58);
  font-size: 12px;
}

.quota-window-period,
.quota-window-usage,
.quota-window-reset {
  display: block;
}

.quota-window-reset {
  grid-column: 1 / -1;
  color: #6b5a44;
}

.quota-action {
  display: inline-flex;
  align-items: center;
  margin-top: 12px;
  color: #224d7f;
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
}

.quota-action:hover {
  text-decoration: underline;
}
</style>
