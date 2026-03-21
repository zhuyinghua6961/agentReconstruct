-- Persistent outbox for conversation JSON object-storage sync retries.

CREATE TABLE IF NOT EXISTS conversation_json_outbox (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    conversation_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    json_version BIGINT NOT NULL,
    local_path VARCHAR(1024) NOT NULL,
    object_name VARCHAR(1024) NOT NULL,
    content_hash CHAR(64) NULL,
    status ENUM('pending','processing','done','failed','dead') NOT NULL DEFAULT 'pending',
    attempt_count INT NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMP NULL DEFAULT NULL,
    processing_started_at TIMESTAMP NULL DEFAULT NULL,
    last_error TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_chat_json_outbox_conv_ver (conversation_id, json_version),
    INDEX idx_chat_json_outbox_status_due (status, next_retry_at, created_at),
    INDEX idx_chat_json_outbox_conversation (conversation_id, created_at),
    CONSTRAINT fk_chat_json_outbox_conversation
        FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
