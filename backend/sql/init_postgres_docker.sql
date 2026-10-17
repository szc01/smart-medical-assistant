-- ==========================================
-- Docker Compose 专用 PostgreSQL 初始化脚本
-- 由 docker-entrypoint-initdb.d 在首次启动时执行，
-- 此时是全新实例，无需判重。
-- ==========================================

CREATE DATABASE smart_short;
CREATE DATABASE smart_long;
CREATE DATABASE smart_session;
