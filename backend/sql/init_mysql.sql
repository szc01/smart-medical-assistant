-- ==========================================
-- 智能医疗助手 - MySQL 初始化脚本
-- 用途：创建用户认证库与 users 表
-- 用法：
--   mysql -u root -p --default-character-set=utf8mb4 < backend/sql/init_mysql.sql
-- 说明：
--   应用启动时也会自动建表（见 main.py），本脚本用于手动初始化 /
--   在 Docker 首次启动时挂载执行。
-- ==========================================

CREATE DATABASE IF NOT EXISTS `medical_assistant`
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `medical_assistant`;

CREATE TABLE IF NOT EXISTS `users` (
  `id`            INT           NOT NULL AUTO_INCREMENT COMMENT '用户 ID',
  `username`      VARCHAR(100)  NOT NULL COMMENT '用户名',
  `email`         VARCHAR(100)  NOT NULL COMMENT '邮箱',
  `password_hash` VARCHAR(255)  NOT NULL COMMENT 'bcrypt 密码哈希',
  `created_at`    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_users_username` (`username`),
  UNIQUE KEY `ix_users_email` (`email`)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci
  COMMENT='用户账号表';
