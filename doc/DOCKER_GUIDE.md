# Docker 部署指南

本指南介绍如何使用Docker部署QGIS Agent的PostgreSQL数据库。

## 前置条件

- Docker 20.10+
- Docker Compose 2.0+

## 快速开始

### 1. 启动PostgreSQL容器

```bash
# 启动容器（后台运行）
docker-compose up -d

# 查看容器状态
docker-compose ps

# 查看容器日志
docker-compose logs -f postgres
```

### 2. 验证容器运行

```bash
# 检查容器是否正常运行
docker ps | grep qgis_postgres

# 测试数据库连接
docker exec -it qgis_postgres psql -U postgres -d qgis_db -c "SELECT version();"
```

### 3. 初始化数据库

```bash
# 运行数据库初始化脚本
uv run python scripts/setup_database.py
```

## Docker管理命令

### 查看容器状态

```bash
# 查看运行中的容器
docker ps

# 查看所有容器（包括停止的）
docker ps -a

# 查看容器详细信息
docker inspect qgis_postgres
```

### 查看日志

```bash
# 查看实时日志
docker-compose logs -f postgres

# 查看最近100行日志
docker-compose logs --tail=100 postgres
```

### 进入容器

```bash
# 进入容器的Shell
docker exec -it qgis_postgres bash

# 直接连接到PostgreSQL
docker exec -it qgis_postgres psql -U postgres -d qgis_db
```

### 备份与恢复

```bash
# 备份数据库
docker exec qgis_postgres pg_dump -U postgres qgis_db > backup_$(date +%Y%m%d_%H%M%S).sql

# 恢复数据库
docker exec -i qgis_postgres psql -U postgres qgis_db < backup.sql
```

### 重启容器

```bash
# 重启容器
docker-compose restart postgres

# 停止并启动容器
docker-compose down && docker-compose up -d
```

### 停止容器

```bash
# 停止容器
docker-compose down

# 停止并删除数据卷（慎用！）
docker-compose down -v
```

## 故障排查

### 容器无法启动

```bash
# 查看容器日志
docker-compose logs postgres

# 检查端口是否被占用
netstat -ano | findstr :5432  # Windows
lsof -i :5432  # Mac/Linux

# 检查Docker资源
docker system df
```

### 无法连接到数据库

```bash
# 测试数据库连接
docker exec -it qgis_postgres pg_isready -U postgres

# 检查网络连接
docker network inspect qgis-agentv2_default

# 查看容器IP
docker inspect qgis_postgres | grep IPAddress
```

### 数据丢失

```bash
# 检查数据卷
docker volume ls

# 检查数据卷详情
docker volume inspect qgis-agentv2_postgres_data

# 如果误删除了数据卷，只能从备份恢复
```

## 性能优化

### 调整PostgreSQL配置

编辑`docker-compose.yml`，添加自定义配置：

```yaml
services:
  postgres:
    command:
      - postgres
      - -c
      - shared_buffers=256MB
      - -c
      - max_connections=200
```

### 资源限制

```yaml
services:
  postgres:
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 1G
        reservations:
          cpus: '0.5'
          memory: 512M
```

## 安全注意事项

⚠️ **开发环境配置**

当前配置使用`POSTGRES_HOST_AUTH_METHOD: trust`，允许所有IP连接，仅适用于开发环境。

⚠️ **生产环境配置**

生产环境请修改以下配置：

1. 修改默认密码
2. 使用SSL连接
3. 限制允许的IP地址
4. 定期备份数据

## 相关链接

- [Docker文档](https://docs.docker.com/)
- [Docker Compose文档](https://docs.docker.com/compose/)
- [PostgreSQL文档](https://www.postgresql.org/docs/)
- [pgvector文档](https://github.com/pgvector/pgvector)
