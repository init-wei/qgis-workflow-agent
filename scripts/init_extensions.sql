-- PostgreSQL扩展初始化脚本
-- 该文件会在容器启动时自动执行

-- 使用容器 POSTGRES_DB 指定的当前项目数据库；不切换到其他数据库。

-- 启用PostGIS扩展（空间数据支持）
CREATE EXTENSION IF NOT EXISTS postgis;

-- 启用pg_trgm扩展（三元组文本匹配）
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 注意: pgvector扩展需要手动安装或使用支持的镜像
-- 如果不需要向量搜索功能，可以暂时跳过
-- CREATE EXTENSION IF NOT EXISTS vector;

-- 验证扩展是否安装成功
SELECT extname, extversion FROM pg_extension
WHERE extname IN ('postgis', 'pg_trgm')
ORDER BY extname;
