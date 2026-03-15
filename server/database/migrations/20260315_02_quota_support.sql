CREATE TABLE IF NOT EXISTS quota_configs (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    quota_type VARCHAR(64) NOT NULL,
    quota_name VARCHAR(128) NOT NULL,
    period VARCHAR(32) NOT NULL DEFAULT 'daily',
    period_days INT NULL DEFAULT NULL,
    default_limit INT NOT NULL DEFAULT 0,
    daily_limit INT NULL DEFAULT NULL,
    weekly_limit INT NULL DEFAULT NULL,
    monthly_limit INT NULL DEFAULT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_quota_configs_type (quota_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_quota_usage (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    quota_type VARCHAR(64) NOT NULL,
    period_key VARCHAR(64) NOT NULL,
    used_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user_quota_usage (user_id, quota_type, period_key),
    KEY idx_user_quota_usage_user (user_id, quota_type),
    CONSTRAINT fk_user_quota_usage_user
        FOREIGN KEY (user_id) REFERENCES users (id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_quota_overrides (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    quota_type VARCHAR(64) NOT NULL,
    custom_limit INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user_quota_overrides (user_id, quota_type),
    KEY idx_user_quota_overrides_user (user_id, quota_type),
    CONSTRAINT fk_user_quota_overrides_user
        FOREIGN KEY (user_id) REFERENCES users (id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
