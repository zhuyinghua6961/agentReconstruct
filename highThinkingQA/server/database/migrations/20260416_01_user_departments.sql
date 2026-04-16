CREATE TABLE IF NOT EXISTS primary_departments (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    status ENUM('active', 'disabled') NOT NULL DEFAULT 'active',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_primary_departments_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS secondary_departments (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    primary_department_id BIGINT NOT NULL,
    name VARCHAR(128) NOT NULL,
    status ENUM('active', 'disabled') NOT NULL DEFAULT 'active',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_secondary_departments_primary_name (primary_department_id, name),
    KEY idx_secondary_departments_primary (primary_department_id),
    CONSTRAINT fk_secondary_departments_primary
        FOREIGN KEY (primary_department_id) REFERENCES primary_departments(id)
        ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET @has_users_primary_department_id := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND COLUMN_NAME = 'primary_department_id'
);
SET @sql := IF(
    @has_users_primary_department_id = 0,
    "ALTER TABLE users ADD COLUMN primary_department_id BIGINT NULL AFTER must_set_security_questions",
    "SELECT 'users.primary_department_id already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_users_secondary_department_id := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND COLUMN_NAME = 'secondary_department_id'
);
SET @sql := IF(
    @has_users_secondary_department_id = 0,
    "ALTER TABLE users ADD COLUMN secondary_department_id BIGINT NULL AFTER primary_department_id",
    "SELECT 'users.secondary_department_id already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_users_primary_department_idx := (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND INDEX_NAME = 'idx_users_primary_department_id'
);
SET @sql := IF(
    @has_users_primary_department_idx = 0,
    "ALTER TABLE users ADD KEY idx_users_primary_department_id (primary_department_id)",
    "SELECT 'idx_users_primary_department_id already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_users_secondary_department_idx := (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND INDEX_NAME = 'idx_users_secondary_department_id'
);
SET @sql := IF(
    @has_users_secondary_department_idx = 0,
    "ALTER TABLE users ADD KEY idx_users_secondary_department_id (secondary_department_id)",
    "SELECT 'idx_users_secondary_department_id already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_users_primary_department_fk := (
    SELECT COUNT(*)
    FROM information_schema.TABLE_CONSTRAINTS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND CONSTRAINT_NAME = 'fk_users_primary_department'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);
SET @sql := IF(
    @has_users_primary_department_fk = 0,
    "ALTER TABLE users ADD CONSTRAINT fk_users_primary_department FOREIGN KEY (primary_department_id) REFERENCES primary_departments(id) ON DELETE SET NULL",
    "SELECT 'fk_users_primary_department already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_users_secondary_department_fk := (
    SELECT COUNT(*)
    FROM information_schema.TABLE_CONSTRAINTS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND CONSTRAINT_NAME = 'fk_users_secondary_department'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);
SET @sql := IF(
    @has_users_secondary_department_fk = 0,
    "ALTER TABLE users ADD CONSTRAINT fk_users_secondary_department FOREIGN KEY (secondary_department_id) REFERENCES secondary_departments(id) ON DELETE SET NULL",
    "SELECT 'fk_users_secondary_department already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
