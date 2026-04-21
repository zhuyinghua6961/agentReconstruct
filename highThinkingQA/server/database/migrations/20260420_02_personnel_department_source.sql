SET @has_personnel_primary_department_id := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'personnel_records'
      AND COLUMN_NAME = 'primary_department_id'
);
SET @sql := IF(
    @has_personnel_primary_department_id = 0,
    "ALTER TABLE personnel_records ADD COLUMN primary_department_id BIGINT NULL AFTER remarks",
    "SELECT 'personnel_records.primary_department_id already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_personnel_secondary_department_id := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'personnel_records'
      AND COLUMN_NAME = 'secondary_department_id'
);
SET @sql := IF(
    @has_personnel_secondary_department_id = 0,
    "ALTER TABLE personnel_records ADD COLUMN secondary_department_id BIGINT NULL AFTER primary_department_id",
    "SELECT 'personnel_records.secondary_department_id already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_personnel_tertiary_department_id := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'personnel_records'
      AND COLUMN_NAME = 'tertiary_department_id'
);
SET @sql := IF(
    @has_personnel_tertiary_department_id = 0,
    "ALTER TABLE personnel_records ADD COLUMN tertiary_department_id BIGINT NULL AFTER secondary_department_id",
    "SELECT 'personnel_records.tertiary_department_id already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_personnel_primary_department_idx := (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'personnel_records'
      AND INDEX_NAME = 'idx_personnel_primary_department_id'
);
SET @sql := IF(
    @has_personnel_primary_department_idx = 0,
    "ALTER TABLE personnel_records ADD KEY idx_personnel_primary_department_id (primary_department_id)",
    "SELECT 'idx_personnel_primary_department_id already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_personnel_secondary_department_idx := (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'personnel_records'
      AND INDEX_NAME = 'idx_personnel_secondary_department_id'
);
SET @sql := IF(
    @has_personnel_secondary_department_idx = 0,
    "ALTER TABLE personnel_records ADD KEY idx_personnel_secondary_department_id (secondary_department_id)",
    "SELECT 'idx_personnel_secondary_department_id already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_personnel_tertiary_department_idx := (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'personnel_records'
      AND INDEX_NAME = 'idx_personnel_tertiary_department_id'
);
SET @sql := IF(
    @has_personnel_tertiary_department_idx = 0,
    "ALTER TABLE personnel_records ADD KEY idx_personnel_tertiary_department_id (tertiary_department_id)",
    "SELECT 'idx_personnel_tertiary_department_id already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_personnel_primary_department_fk := (
    SELECT COUNT(*)
    FROM information_schema.TABLE_CONSTRAINTS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'personnel_records'
      AND CONSTRAINT_NAME = 'fk_personnel_primary_department'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);
SET @sql := IF(
    @has_personnel_primary_department_fk = 0,
    "ALTER TABLE personnel_records ADD CONSTRAINT fk_personnel_primary_department FOREIGN KEY (primary_department_id) REFERENCES primary_departments(id) ON DELETE SET NULL",
    "SELECT 'fk_personnel_primary_department already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_personnel_secondary_department_fk := (
    SELECT COUNT(*)
    FROM information_schema.TABLE_CONSTRAINTS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'personnel_records'
      AND CONSTRAINT_NAME = 'fk_personnel_secondary_department'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);
SET @sql := IF(
    @has_personnel_secondary_department_fk = 0,
    "ALTER TABLE personnel_records ADD CONSTRAINT fk_personnel_secondary_department FOREIGN KEY (secondary_department_id) REFERENCES secondary_departments(id) ON DELETE SET NULL",
    "SELECT 'fk_personnel_secondary_department already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_personnel_tertiary_department_fk := (
    SELECT COUNT(*)
    FROM information_schema.TABLE_CONSTRAINTS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'personnel_records'
      AND CONSTRAINT_NAME = 'fk_personnel_tertiary_department'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);
SET @sql := IF(
    @has_personnel_tertiary_department_fk = 0,
    "ALTER TABLE personnel_records ADD CONSTRAINT fk_personnel_tertiary_department FOREIGN KEY (tertiary_department_id) REFERENCES tertiary_departments(id) ON DELETE SET NULL",
    "SELECT 'fk_personnel_tertiary_department already exists'"
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
