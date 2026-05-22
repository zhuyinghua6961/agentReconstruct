
-- Schema-only bootstrap SQL exported from the local user-level MySQL agentcode database.
-- Source: /home/cqy/mysql, socket /home/cqy/mysql/mysql.sock, database agentcode.
-- Intended use: Docker MySQL initialization under deploy/mysql-init/.
-- Data rows are intentionally excluded; this file creates the deployment schema only.

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

CREATE DATABASE /*!32312 IF NOT EXISTS*/ `agentcode` /*!40100 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci */ /*!80016 DEFAULT ENCRYPTION='N' */;

USE `agentcode`;
DROP TABLE IF EXISTS `conversation_files`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `conversation_files` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `conversation_id` bigint NOT NULL,
  `user_id` bigint NOT NULL,
  `file_type` enum('pdf','excel') NOT NULL,
  `file_name` varchar(255) NOT NULL,
  `local_path` varchar(1024) DEFAULT NULL,
  `storage_ref` varchar(1024) DEFAULT NULL,
  `content_type` varchar(128) DEFAULT NULL,
  `size_bytes` bigint DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_conversation_files_conversation` (`conversation_id`,`created_at`),
  KEY `idx_conversation_files_user` (`user_id`,`created_at`),
  KEY `idx_conversation_files_conversation_user` (`conversation_id`,`user_id`),
  CONSTRAINT `fk_conversation_files_conversation_user` FOREIGN KEY (`conversation_id`, `user_id`) REFERENCES `conversations` (`id`, `user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `conversation_json_outbox`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `conversation_json_outbox` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `conversation_id` bigint NOT NULL,
  `user_id` bigint NOT NULL,
  `json_version` bigint NOT NULL,
  `local_path` varchar(1024) NOT NULL,
  `object_name` varchar(1024) NOT NULL,
  `content_hash` char(64) DEFAULT NULL,
  `status` enum('pending','processing','done','failed','dead') NOT NULL DEFAULT 'pending',
  `attempt_count` int NOT NULL DEFAULT '0',
  `next_retry_at` timestamp NULL DEFAULT NULL,
  `processing_started_at` timestamp NULL DEFAULT NULL,
  `last_error` text,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_chat_json_outbox_conv_ver` (`conversation_id`,`json_version`),
  KEY `idx_chat_json_outbox_status_due` (`status`,`next_retry_at`,`created_at`),
  KEY `idx_chat_json_outbox_conversation` (`conversation_id`,`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `conversation_messages`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `conversation_messages` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `conversation_id` bigint NOT NULL,
  `user_id` bigint NOT NULL,
  `role` enum('user','assistant') NOT NULL,
  `content` mediumtext NOT NULL,
  `metadata_json` json DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_messages_conversation_created` (`conversation_id`,`created_at`),
  KEY `idx_messages_conversation_user` (`conversation_id`,`user_id`),
  CONSTRAINT `fk_messages_conversation_user` FOREIGN KEY (`conversation_id`, `user_id`) REFERENCES `conversations` (`id`, `user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `conversations`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `conversations` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `user_id` bigint NOT NULL,
  `title` varchar(255) NOT NULL DEFAULT 'New Conversation',
  `message_count` int NOT NULL DEFAULT '0',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `chat_json_local_path` varchar(1024) DEFAULT NULL,
  `chat_json_storage_ref` varchar(1024) DEFAULT NULL,
  `chat_json_hash` char(64) DEFAULT NULL,
  `chat_json_size_bytes` bigint DEFAULT NULL,
  `chat_json_version` bigint NOT NULL DEFAULT '0',
  `chat_json_updated_at` timestamp NULL DEFAULT NULL,
  `chat_json_sync_status` enum('ok','local_only','sync_failed') NOT NULL DEFAULT 'sync_failed',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_conversations_id_user` (`id`,`user_id`),
  KEY `idx_conversations_user_updated` (`user_id`,`updated_at`),
  KEY `idx_conversations_chat_json_sync` (`chat_json_sync_status`),
  CONSTRAINT `fk_conversations_user_id` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `password_history`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `password_history` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `user_id` bigint NOT NULL,
  `password_hash` varchar(255) NOT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_password_history_user_created` (`user_id`,`created_at` DESC),
  CONSTRAINT `fk_password_history_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `personnel_records`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `personnel_records` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `employee_no` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `full_name` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `verification_code_hash` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` enum('active','disabled') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'active',
  `remarks` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `primary_department_id` bigint DEFAULT NULL,
  `secondary_department_id` bigint DEFAULT NULL,
  `tertiary_department_id` bigint DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_personnel_records_employee_no` (`employee_no`),
  KEY `idx_personnel_records_status` (`status`),
  KEY `idx_personnel_primary_department_id` (`primary_department_id`),
  KEY `idx_personnel_secondary_department_id` (`secondary_department_id`),
  KEY `idx_personnel_tertiary_department_id` (`tertiary_department_id`),
  CONSTRAINT `fk_personnel_primary_department` FOREIGN KEY (`primary_department_id`) REFERENCES `primary_departments` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_personnel_secondary_department` FOREIGN KEY (`secondary_department_id`) REFERENCES `secondary_departments` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_personnel_tertiary_department` FOREIGN KEY (`tertiary_department_id`) REFERENCES `tertiary_departments` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `primary_departments`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `primary_departments` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` enum('active','disabled') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'active',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_primary_departments_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `quota_configs`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `quota_configs` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `quota_type` varchar(64) NOT NULL,
  `quota_name` varchar(128) NOT NULL,
  `period` enum('daily','weekly','monthly','custom_days','none') NOT NULL DEFAULT 'daily',
  `period_days` int unsigned DEFAULT NULL,
  `default_limit` int unsigned NOT NULL DEFAULT '100',
  `daily_limit` int unsigned DEFAULT NULL,
  `weekly_limit` int unsigned DEFAULT NULL,
  `monthly_limit` int unsigned DEFAULT NULL,
  `is_active` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `quota_type` (`quota_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `secondary_departments`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `secondary_departments` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `primary_department_id` bigint NOT NULL,
  `name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` enum('active','disabled') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'active',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_secondary_departments_primary_name` (`primary_department_id`,`name`),
  KEY `idx_secondary_departments_primary` (`primary_department_id`),
  CONSTRAINT `fk_secondary_departments_primary` FOREIGN KEY (`primary_department_id`) REFERENCES `primary_departments` (`id`) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `tertiary_departments`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `tertiary_departments` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `secondary_department_id` bigint NOT NULL,
  `name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` enum('active','disabled') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'active',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_tertiary_departments_secondary_name` (`secondary_department_id`,`name`),
  KEY `idx_tertiary_departments_secondary` (`secondary_department_id`),
  CONSTRAINT `fk_tertiary_departments_secondary` FOREIGN KEY (`secondary_department_id`) REFERENCES `secondary_departments` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `user_quota_overrides`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `user_quota_overrides` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `user_id` bigint NOT NULL,
  `quota_type` varchar(64) NOT NULL,
  `custom_limit` int unsigned NOT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_quota_override` (`user_id`,`quota_type`),
  KEY `idx_user_quota_overrides_quota_type` (`quota_type`),
  CONSTRAINT `fk_user_quota_overrides_quota_type` FOREIGN KEY (`quota_type`) REFERENCES `quota_configs` (`quota_type`) ON DELETE CASCADE,
  CONSTRAINT `fk_user_quota_overrides_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `user_quota_usage`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `user_quota_usage` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `user_id` bigint NOT NULL,
  `quota_type` varchar(64) NOT NULL,
  `period_key` varchar(32) NOT NULL,
  `used_count` int unsigned NOT NULL DEFAULT '0',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_quota_period` (`user_id`,`quota_type`,`period_key`),
  KEY `idx_user_quota_type` (`user_id`,`quota_type`),
  KEY `idx_user_quota_usage_quota_type` (`quota_type`),
  CONSTRAINT `fk_user_quota_usage_quota_type` FOREIGN KEY (`quota_type`) REFERENCES `quota_configs` (`quota_type`) ON DELETE CASCADE,
  CONSTRAINT `fk_user_quota_usage_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `user_security_questions`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `user_security_questions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `user_id` bigint NOT NULL,
  `question` varchar(255) NOT NULL,
  `answer_hash` varchar(255) NOT NULL,
  `sort_order` tinyint unsigned NOT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_security_questions_user_sort` (`user_id`,`sort_order`),
  KEY `idx_user_security_questions_user` (`user_id`),
  CONSTRAINT `fk_user_security_questions_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `users`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `users` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `username` varchar(64) NOT NULL,
  `password_hash` varchar(255) NOT NULL,
  `role` enum('user','admin') NOT NULL DEFAULT 'user',
  `user_type` tinyint unsigned NOT NULL DEFAULT '3' COMMENT '1=admin,2=super,3=common',
  `status` enum('active','disabled') NOT NULL DEFAULT 'active',
  `is_first_login` tinyint(1) NOT NULL DEFAULT '0',
  `must_set_security_questions` tinyint(1) NOT NULL DEFAULT '0',
  `primary_department_id` bigint DEFAULT NULL,
  `secondary_department_id` bigint DEFAULT NULL,
  `tertiary_department_id` bigint DEFAULT NULL,
  `personnel_id` bigint DEFAULT NULL,
  `failed_login_attempts` int unsigned NOT NULL DEFAULT '0',
  `locked_until` datetime DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `password_updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `username` (`username`),
  KEY `idx_users_status` (`status`),
  KEY `idx_users_role` (`role`),
  KEY `idx_users_primary_department_id` (`primary_department_id`),
  KEY `idx_users_secondary_department_id` (`secondary_department_id`),
  KEY `idx_users_tertiary_department_id` (`tertiary_department_id`),
  KEY `idx_users_personnel_id` (`personnel_id`),
  CONSTRAINT `fk_users_personnel` FOREIGN KEY (`personnel_id`) REFERENCES `personnel_records` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_users_primary_department` FOREIGN KEY (`primary_department_id`) REFERENCES `primary_departments` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_users_secondary_department` FOREIGN KEY (`secondary_department_id`) REFERENCES `secondary_departments` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_users_tertiary_department` FOREIGN KEY (`tertiary_department_id`) REFERENCES `tertiary_departments` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;
