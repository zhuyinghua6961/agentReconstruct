-- One-time cleanup for inflated active-usage metrics collected before the
-- interaction-based heartbeat rollout (double finalize, multi-tab races,
-- tab-visible counting). Safe to re-run: only clears active_seconds > 0
-- and online session rows; event counts are preserved.

USE `agentcode`;

DELETE FROM `user_online_sessions`;

UPDATE `user_daily_stats`
SET `active_seconds` = 0
WHERE `active_seconds` > 0;
