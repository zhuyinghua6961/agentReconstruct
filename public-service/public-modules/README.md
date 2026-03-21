# 公共能力模块文档

基于 `/home/cqy/worktrees/fastapi-version/backend/app/modules` 的实际代码整理。

本目录按“明确属于公共能力”的模块拆分文档，每份文档都对应重新阅读后的代码事实，不再混在总览里。

模块列表：

- `01-auth.md`
- `02-admin-users.md`
- `03-quota.md`
- `04-conversation.md`
- `05-uploads.md`
- `06-documents.md`
- `07-storage.md`
- `08-system.md`
- `99-known-issues-and-risks.md`

建议先读：

- 如果目标是识别哪些能力可以抽成独立公共后端，先看 `01-auth.md` 到 `08-system.md`
- 如果目标是直接梳理现网已确认 bug、契约偏差和迁移风险，优先看 `99-known-issues-and-risks.md`
- 如果目标是逐模块开工拆分，再进入各模块子目录中的 `README.md` 和分篇细读文档

已进一步细拆的模块：

- `auth/README.md`
- `auth/01-api-and-token-model.md`
- `auth/02-password-policy-and-account-state.md`
- `auth/03-repository-schema-compat.md`
- `auth/04-first-login-security-questions-and-reset.md`
- `auth/05-frontend-session-and-compat-notes.md`
- `auth/06-dependencies-and-integration-points.md`
- `admin_users/README.md`
- `admin_users/01-api-guards-and-contracts.md`
- `admin_users/02-user-lifecycle-and-state-transitions.md`
- `admin_users/03-batch-import-and-template-pipeline.md`
- `admin_users/04-dependencies-shared-schema-and-boundaries.md`
- `admin_users/05-frontend-dashboard-and-contract-gaps.md`
- `conversation/README.md`
- `conversation/01-api-and-contracts.md`
- `conversation/02-data-model-and-json-store.md`
- `conversation/03-cache-and-read-path.md`
- `conversation/04-outbox-and-remote-sync.md`
- `conversation/05-upload-processing-state-machine.md`
- `conversation/06-gateway-hooks-and-write-path.md`
- `uploads/README.md`
- `uploads/01-api-and-contracts.md`
- `uploads/02-save-path-runtime-and-storage.md`
- `uploads/03-auth-and-quota.md`
- `uploads/04-conversation-binding-and-processing.md`
- `uploads/05-frontend-and-compat-notes.md`
- `documents/README.md`
- `documents/01-api-auth-and-quota.md`
- `documents/02-pdf-asset-access-and-summary.md`
- `documents/03-translation-and-cache.md`
- `documents/04-literature-content-and-reference-preview.md`
- `documents/05-frontend-and-compat-notes.md`
- `quota/README.md`
- `quota/01-api-and-admin-surface.md`
- `quota/02-config-model-and-window-calculation.md`
- `quota/03-deps-precheck-and-finalize.md`
- `quota/04-repository-and-cache.md`
- `quota/05-frontend-and-management-ui.md`
- `storage/README.md`
- `storage/01-backend-selection-and-storage-ref.md`
- `storage/02-paper-pdf-cache-and-mirror.md`
- `storage/03-conversation-json-download-and-cleanup.md`
- `storage/04-legacy-paper-helper-and-call-site-migration.md`
- `storage/05-runtime-tests-and-frontend-usage.md`
- `system/README.md`
- `system/01-api-health-and-http-semantics.md`
- `system/02-background-status-and-cache-debug.md`
- `system/03-kb-runtime-and-cache-ops.md`
- `system/04-runtime-dependencies-and-schema-gaps.md`
- `system/05-frontend-usage-and-security-boundaries.md`

配套总览文档：

- `/home/cqy/worktrees/public-service/public-capabilities-inventory.md`
- `/home/cqy/worktrees/public-service/backend-dependency-map.md`
- `/home/cqy/worktrees/public-service/backend-module-drilldown.md`
- `/home/cqy/worktrees/public-service/gateway-public-backend-protocol-alignment.md`
- `/home/cqy/worktrees/public-service/public-backend-extraction-task-list.md`
- `/home/cqy/worktrees/public-service/public-backend-extraction-phase0-phase1-tickets.md`

代码骨架：

- `/home/cqy/worktrees/public-service/backend/README.md`
- `/home/cqy/worktrees/public-service/backend/app/main.py`
