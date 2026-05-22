-- Reference department seed for LiFeO4Agent deployments.
-- Intentionally includes only the concrete battery-material department tree.
-- Test/demo departments such as a/a1/a2/a3/a11 are excluded.
-- User, personnel, conversation, and quota usage data remain user-state data and are not seeded.

SET NAMES utf8mb4;

USE `agentcode`;

INSERT INTO `primary_departments` (`id`, `name`, `status`) VALUES
  (2, '电池材料技术研究中心', 'active')
ON DUPLICATE KEY UPDATE
  `name` = VALUES(`name`),
  `status` = VALUES(`status`);

INSERT INTO `secondary_departments` (`id`, `primary_department_id`, `name`, `status`) VALUES
  (4, 2, '正极材料研究所', 'active'),
  (5, 2, '装备工程化研究所', 'active'),
  (6, 2, '材料应用研究所', 'active')
ON DUPLICATE KEY UPDATE
  `primary_department_id` = VALUES(`primary_department_id`),
  `name` = VALUES(`name`),
  `status` = VALUES(`status`);

INSERT INTO `tertiary_departments` (`id`, `secondary_department_id`, `name`, `status`) VALUES
  (1, 4, '磷酸铁锂材料开发', 'active'),
  (2, 4, '磷酸铁材料开发', 'active'),
  (3, 4, '正极材料工艺研究', 'active'),
  (4, 5, '装备开发', 'active'),
  (5, 5, '产业链优化与工程化', 'active'),
  (6, 6, '电芯设计', 'active'),
  (7, 6, '电芯工程设备和工艺', 'active'),
  (8, 6, '电芯测试', 'active'),
  (9, 6, '先进表征技术开发', 'active')
ON DUPLICATE KEY UPDATE
  `secondary_department_id` = VALUES(`secondary_department_id`),
  `name` = VALUES(`name`),
  `status` = VALUES(`status`);
