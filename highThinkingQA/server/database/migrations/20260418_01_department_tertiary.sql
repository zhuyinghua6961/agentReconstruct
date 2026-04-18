CREATE TABLE IF NOT EXISTS tertiary_departments (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    secondary_department_id BIGINT NOT NULL,
    name VARCHAR(128) NOT NULL,
    status ENUM('active', 'disabled') NOT NULL DEFAULT 'active',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_tertiary_departments_secondary_name (secondary_department_id, name),
    KEY idx_tertiary_departments_secondary (secondary_department_id),
    CONSTRAINT fk_tertiary_departments_secondary
        FOREIGN KEY (secondary_department_id) REFERENCES secondary_departments(id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET @has_users_tertiary_department_id := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND COLUMN_NAME = 'tertiary_department_id'
);
SET @sql := IF(
    @has_users_tertiary_department_id = 0,
    "ALTER TABLE users ADD COLUMN tertiary_department_id BIGINT NULL AFTER secondary_department_id",
    "SELECT 'users.tertiary_department_id already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_users_tertiary_department_idx := (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND INDEX_NAME = 'idx_users_tertiary_department_id'
);
SET @sql := IF(
    @has_users_tertiary_department_idx = 0,
    "ALTER TABLE users ADD KEY idx_users_tertiary_department_id (tertiary_department_id)",
    "SELECT 'idx_users_tertiary_department_id already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_users_tertiary_department_fk := (
    SELECT COUNT(*)
    FROM information_schema.TABLE_CONSTRAINTS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND CONSTRAINT_NAME = 'fk_users_tertiary_department'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);
SET @sql := IF(
    @has_users_tertiary_department_fk = 0,
    "ALTER TABLE users ADD CONSTRAINT fk_users_tertiary_department FOREIGN KEY (tertiary_department_id) REFERENCES tertiary_departments(id) ON DELETE SET NULL",
    "SELECT 'fk_users_tertiary_department already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
