CREATE TABLE IF NOT EXISTS personnel_records (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    employee_no VARCHAR(64) NOT NULL,
    full_name VARCHAR(64) NOT NULL,
    verification_code_hash VARCHAR(255) NOT NULL,
    status ENUM('active', 'disabled') NOT NULL DEFAULT 'active',
    remarks VARCHAR(255) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_personnel_records_employee_no (employee_no),
    KEY idx_personnel_records_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET @has_users_personnel_id := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND COLUMN_NAME = 'personnel_id'
);

SET @users_personnel_after_column := (
    SELECT CASE
        WHEN EXISTS (
            SELECT 1
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'users'
              AND COLUMN_NAME = 'tertiary_department_id'
        ) THEN 'tertiary_department_id'
        WHEN EXISTS (
            SELECT 1
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'users'
              AND COLUMN_NAME = 'secondary_department_id'
        ) THEN 'secondary_department_id'
        WHEN EXISTS (
            SELECT 1
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'users'
              AND COLUMN_NAME = 'primary_department_id'
        ) THEN 'primary_department_id'
        ELSE NULL
    END
);

SET @sql := IF(
    @has_users_personnel_id = 0,
    IF(
        @users_personnel_after_column IS NOT NULL,
        CONCAT("ALTER TABLE users ADD COLUMN personnel_id BIGINT NULL AFTER ", @users_personnel_after_column),
        "ALTER TABLE users ADD COLUMN personnel_id BIGINT NULL"
    ),
    "SELECT 'users.personnel_id already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_users_personnel_idx := (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND INDEX_NAME = 'idx_users_personnel_id'
);
SET @sql := IF(
    @has_users_personnel_idx = 0,
    "ALTER TABLE users ADD KEY idx_users_personnel_id (personnel_id)",
    "SELECT 'idx_users_personnel_id already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_users_personnel_fk := (
    SELECT COUNT(*)
    FROM information_schema.TABLE_CONSTRAINTS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND CONSTRAINT_NAME = 'fk_users_personnel'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);
SET @sql := IF(
    @has_users_personnel_fk = 0,
    "ALTER TABLE users ADD CONSTRAINT fk_users_personnel FOREIGN KEY (personnel_id) REFERENCES personnel_records(id) ON DELETE SET NULL",
    "SELECT 'fk_users_personnel already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
