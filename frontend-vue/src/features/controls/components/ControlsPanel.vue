<script setup>
import { ref } from 'vue';

const props = defineProps({
  kbInfo: { type: Object, default: () => ({}) },
  uploading: { type: Boolean, default: false },
  pdfName: { type: String, default: '' },
  usePdf: { type: Boolean, default: false },
  authUser: { type: Object, default: null },
  quotaItems: { type: Array, default: () => [] },
  activeFiles: { type: Array, default: () => [] },
  isAdmin: { type: Boolean, default: false },
  adminQuotaLoading: { type: Boolean, default: false },
  adminQuotaError: { type: String, default: '' },
  adminQuotaConfigs: { type: Array, default: () => [] },
  adminTargetUserId: { type: [String, Number], default: '' },
  adminTargetUserQuotas: { type: Array, default: () => [] },
});

const emit = defineEmits([
  'refresh-kb',
  'clear-cache',
  'upload-pdf',
  'upload-excel',
  'clear-pdf',
  'toggle-use-pdf',
  'download-file',
  'admin-refresh-configs',
  'admin-target-user-input',
  'admin-load-user-quotas',
  'admin-update-limit',
  'admin-toggle-active',
  'admin-save-config',
  'admin-reset-user-quota',
]);

const pdfInput = ref(null);
const excelInput = ref(null);

function openPdfPicker() {
  pdfInput.value?.click();
}

function openExcelPicker() {
  excelInput.value?.click();
}

function onPdfChange(event) {
  const file = event.target.files?.[0];
  if (file) {
    emit('upload-pdf', file);
    event.target.value = '';
  }
}

function onExcelChange(event) {
  const file = event.target.files?.[0];
  if (file) {
    emit('upload-excel', file);
    event.target.value = '';
  }
}
</script>

<template>
  <aside class="controls">
    <section class="panel">
      <h2>账户与配额</h2>
      <p>当前用户: {{ authUser?.username || '未登录（本地模式）' }}</p>
      <p>角色: {{ authUser?.role || '-' }}</p>
      <p v-if="quotaItems.length === 0">暂无配额数据</p>
      <ul v-else class="quota-list">
        <li v-for="item in quotaItems" :key="item.quota_type" class="quota-item">
          <span class="quota-name">{{ item.quota_name || item.quota_type }}</span>
          <span class="quota-value">{{ item.current }}/{{ item.limit }}</span>
        </li>
      </ul>
    </section>

    <section v-if="isAdmin" class="panel">
      <h2>配额管理（管理员）</h2>
      <div class="btn-row">
        <button class="action-btn" :disabled="adminQuotaLoading" @click="emit('admin-refresh-configs')">
          刷新配置
        </button>
      </div>
      <p v-if="adminQuotaError" class="hint admin-error">{{ adminQuotaError }}</p>
      <ul v-if="adminQuotaConfigs.length > 0" class="admin-config-list">
        <li v-for="cfg in adminQuotaConfigs" :key="cfg.quota_type" class="admin-config-item">
          <p class="admin-config-title">{{ cfg.quota_name || cfg.quota_type }}</p>
          <div class="admin-row">
            <label>类型</label>
            <span>{{ cfg.quota_type }}</span>
          </div>
          <div class="admin-row">
            <label>默认上限</label>
            <input
              class="admin-input"
              type="number"
              min="0"
              :value="cfg.editDefaultLimit"
              @change="emit('admin-update-limit', { quotaType: cfg.quota_type, value: $event.target.value })"
            />
          </div>
          <label class="switch-row">
            <input
              type="checkbox"
              :checked="cfg.editIsActive"
              @change="emit('admin-toggle-active', { quotaType: cfg.quota_type, value: $event.target.checked })"
            />
            <span>启用</span>
          </label>
          <button
            class="action-btn"
            :disabled="adminQuotaLoading"
            @click="emit('admin-save-config', cfg.quota_type)"
          >
            保存配置
          </button>
        </li>
      </ul>

      <div class="pdf-box">
        <p class="hint">用户配额管理</p>
        <div class="admin-row admin-row-fill">
          <input
            class="admin-input"
            type="text"
            inputmode="numeric"
            placeholder="目标用户 ID"
            :value="adminTargetUserId"
            @input="emit('admin-target-user-input', $event.target.value)"
          />
          <button
            class="action-btn ghost"
            :disabled="adminQuotaLoading"
            @click="emit('admin-load-user-quotas')"
          >
            查询
          </button>
        </div>
        <ul v-if="adminTargetUserQuotas.length > 0" class="quota-list">
          <li v-for="item in adminTargetUserQuotas" :key="item.quota_type" class="quota-item">
            <span class="quota-name">{{ item.quota_name || item.quota_type }}</span>
            <span class="quota-value">{{ item.current }}/{{ item.limit }}</span>
            <button
              class="text-btn"
              :disabled="adminQuotaLoading"
              @click="emit('admin-reset-user-quota', item.quota_type)"
            >
              重置
            </button>
          </li>
        </ul>
      </div>
    </section>

    <section class="panel">
      <h2>知识库</h2>
      <p>Neo4j: {{ kbInfo?.source_stats?.neo4j ?? '-' }}</p>
      <p>Chroma: {{ kbInfo?.source_stats?.chromadb ?? '-' }}</p>
      <div class="btn-row">
        <button class="action-btn" @click="emit('refresh-kb')">刷新 KB</button>
        <button class="action-btn ghost" @click="emit('clear-cache')">清缓存</button>
      </div>
    </section>

    <section class="panel">
      <h2>文档</h2>
      <input ref="pdfInput" hidden type="file" accept="application/pdf" @change="onPdfChange" />
      <input ref="excelInput" hidden type="file" accept=".xls,.xlsx,.csv" @change="onExcelChange" />

      <div class="btn-col">
        <button class="action-btn" :disabled="uploading" @click="openPdfPicker">上传 PDF</button>
        <button class="action-btn ghost" :disabled="uploading" @click="openExcelPicker">上传 Excel/CSV</button>
      </div>

      <div class="pdf-box">
        <label class="switch-row">
          <input
            type="checkbox"
            :checked="usePdf"
            @change="emit('toggle-use-pdf', $event.target.checked)"
          />
          <span>提问时使用已上传 PDF</span>
        </label>
        <p class="hint">当前文件: {{ pdfName || '无' }}</p>
        <button class="text-btn danger" @click="emit('clear-pdf')">清除 PDF 上下文</button>
      </div>

      <div class="pdf-box">
        <p class="hint">会话文件: {{ activeFiles.length }}</p>
        <ul v-if="activeFiles.length > 0" class="file-list">
          <li v-for="file in activeFiles" :key="file.id" class="file-item">
            <span class="file-name" :title="file.file_name">{{ file.file_name }}</span>
            <button class="text-btn" @click="emit('download-file', file)">下载</button>
          </li>
        </ul>
      </div>
    </section>
  </aside>
</template>
