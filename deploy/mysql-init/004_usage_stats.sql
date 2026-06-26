-- Usage statistics tables for admin dashboard (activity events, online sessions, daily rollups).

USE `agentcode`;

CREATE TABLE IF NOT EXISTS `user_activity_events` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `user_id` bigint NOT NULL,
  `event_type` varchar(32) NOT NULL,
  `occurred_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `trace_id` varchar(64) DEFAULT NULL,
  `conversation_id` bigint DEFAULT NULL,
  `metadata` json DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_user_activity_user_occurred` (`user_id`, `occurred_at`),
  KEY `idx_user_activity_type_occurred` (`event_type`, `occurred_at`),
  CONSTRAINT `fk_user_activity_events_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `user_online_sessions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `user_id` bigint NOT NULL,
  `session_id` varchar(64) NOT NULL,
  `started_at` timestamp NOT NULL,
  `ended_at` timestamp NOT NULL,
  `active_seconds` int unsigned NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  KEY `idx_user_online_sessions_user_started` (`user_id`, `started_at`),
  CONSTRAINT `fk_user_online_sessions_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `user_daily_stats` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `user_id` bigint NOT NULL,
  `stat_date` date NOT NULL,
  `ask_query_count` int unsigned NOT NULL DEFAULT '0',
  `file_qa_count` int unsigned NOT NULL DEFAULT '0',
  `literature_search_count` int unsigned NOT NULL DEFAULT '0',
  `patent_search_count` int unsigned NOT NULL DEFAULT '0',
  `active_seconds` int unsigned NOT NULL DEFAULT '0',
  `last_active_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_daily_stats_user_date` (`user_id`, `stat_date`),
  KEY `idx_user_daily_stats_stat_date` (`stat_date`),
  CONSTRAINT `fk_user_daily_stats_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
