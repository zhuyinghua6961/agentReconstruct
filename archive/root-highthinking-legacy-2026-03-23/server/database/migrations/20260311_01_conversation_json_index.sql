-- Add conversation JSON index fields to conversations table (idempotent).

SET @has_chat_json_local_path := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'conversations'
      AND COLUMN_NAME = 'chat_json_local_path'
);

SET @sql := IF(
    @has_chat_json_local_path = 0,
    "ALTER TABLE conversations ADD COLUMN chat_json_local_path VARCHAR(1024) NULL AFTER updated_at",
    "SELECT 'conversations.chat_json_local_path already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_chat_json_storage_ref := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'conversations'
      AND COLUMN_NAME = 'chat_json_storage_ref'
);

SET @sql := IF(
    @has_chat_json_storage_ref = 0,
    "ALTER TABLE conversations ADD COLUMN chat_json_storage_ref VARCHAR(1024) NULL AFTER chat_json_local_path",
    "SELECT 'conversations.chat_json_storage_ref already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_chat_json_hash := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'conversations'
      AND COLUMN_NAME = 'chat_json_hash'
);

SET @sql := IF(
    @has_chat_json_hash = 0,
    "ALTER TABLE conversations ADD COLUMN chat_json_hash CHAR(64) NULL AFTER chat_json_storage_ref",
    "SELECT 'conversations.chat_json_hash already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_chat_json_size_bytes := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'conversations'
      AND COLUMN_NAME = 'chat_json_size_bytes'
);

SET @sql := IF(
    @has_chat_json_size_bytes = 0,
    "ALTER TABLE conversations ADD COLUMN chat_json_size_bytes BIGINT NULL AFTER chat_json_hash",
    "SELECT 'conversations.chat_json_size_bytes already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_chat_json_version := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'conversations'
      AND COLUMN_NAME = 'chat_json_version'
);

SET @sql := IF(
    @has_chat_json_version = 0,
    "ALTER TABLE conversations ADD COLUMN chat_json_version BIGINT NOT NULL DEFAULT 0 AFTER chat_json_size_bytes",
    "SELECT 'conversations.chat_json_version already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_chat_json_updated_at := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'conversations'
      AND COLUMN_NAME = 'chat_json_updated_at'
);

SET @sql := IF(
    @has_chat_json_updated_at = 0,
    "ALTER TABLE conversations ADD COLUMN chat_json_updated_at TIMESTAMP NULL DEFAULT NULL AFTER chat_json_version",
    "SELECT 'conversations.chat_json_updated_at already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_chat_json_sync_status := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'conversations'
      AND COLUMN_NAME = 'chat_json_sync_status'
);

SET @sql := IF(
    @has_chat_json_sync_status = 0,
    "ALTER TABLE conversations ADD COLUMN chat_json_sync_status ENUM('ok','local_only','sync_failed') NOT NULL DEFAULT 'sync_failed' AFTER chat_json_updated_at",
    "SELECT 'conversations.chat_json_sync_status already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_sync_status_index := (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'conversations'
      AND INDEX_NAME = 'idx_conversations_chat_json_sync'
);

SET @sql := IF(
    @has_sync_status_index = 0,
    "CREATE INDEX idx_conversations_chat_json_sync ON conversations(chat_json_sync_status)",
    "SELECT 'idx_conversations_chat_json_sync already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
