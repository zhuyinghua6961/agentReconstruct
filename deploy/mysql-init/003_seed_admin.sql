-- Initial administrator seed for LiFeO4Agent deployments.
-- This script is intentionally idempotent: it creates the bootstrap admin only
-- when the username does not exist, and it does not reset a changed password.

USE `agentcode`;

SET @lifeo4_default_admin_hash := 'pbkdf2_sha256$120000$daa41997f72e67a45a78c9fa3f45c55b$fb7154bc11eaeb476133a82415e2515f8bb99e7c5190cd5c711ab24124e1361a';

INSERT INTO `users` (
  `username`,
  `password_hash`,
  `role`,
  `user_type`,
  `status`,
  `is_first_login`,
  `must_set_security_questions`,
  `password_updated_at`
) VALUES (
  'admin',
  @lifeo4_default_admin_hash,
  'admin',
  1,
  'active',
  1,
  1,
  NOW()
) ON DUPLICATE KEY UPDATE
  `username` = `username`;

INSERT INTO `password_history` (`user_id`, `password_hash`)
SELECT
  `u`.`id`,
  @lifeo4_default_admin_hash
FROM `users` AS `u`
WHERE `u`.`username` = 'admin'
  AND `u`.`password_hash` = @lifeo4_default_admin_hash
  AND NOT EXISTS (
    SELECT 1
    FROM `password_history` AS `ph`
    WHERE `ph`.`user_id` = `u`.`id`
      AND `ph`.`password_hash` = @lifeo4_default_admin_hash
  );
