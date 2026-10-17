-- ==========================================
-- 智能医疗助手 - PostgreSQL 初始化脚本
-- 用途：创建三个库
--   smart_short    短记忆（LangGraph Checkpoint / 对话历史）
--   smart_long     长记忆（用户画像 / 医疗历史）
--   smart_session  会话管理（会话、消息、文档元数据）
-- 用法：
--   psql -U postgres -f backend/sql/init_postgres.sql
-- 说明：
--   三个库内的表由应用启动时自动创建
--   （PostgresSaver.setup() / PostgresStore.setup() / create_all）。
-- ==========================================

SELECT 'CREATE DATABASE smart_short'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'smart_short')\gexec

SELECT 'CREATE DATABASE smart_long'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'smart_long')\gexec

SELECT 'CREATE DATABASE smart_session'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'smart_session')\gexec
