# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

ACGO 是一个轻量级自动签到管理系统，支持通过 Curl 命令配置签到任务，使用 Cron 表达式定时执行。

## 常用命令

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务 (开发)
python run.py

# 服务运行在 http://0.0.0.0:5000
# 默认密码: acgo123321

# Docker 部署
docker-compose up -d

# Docker 构建
docker build -t acgo:latest .
```

## 技术栈

- **Python 3.x** + Flask 3.0
- **APScheduler 3.10** - 定时任务调度
- **Peewee ORM** + SQLite - 数据持久化
- **Requests** - HTTP 请求执行

## 架构概览

```
src/
├── app.py        # Flask 主程序，所有 API 路由定义
├── models.py     # Peewee ORM 模型 (Account, CheckinLog, Config)
├── scheduler.py  # APScheduler 调度器，curl 解析，签到执行逻辑
├── auth.py       # 登录验证装饰器
└── __init__.py   # 版本信息

run.py            # 启动入口
data/acgo.db      # SQLite 数据库 (自动生成)
```

## 核心模块职责

### `scheduler.py` - 调度核心
- `parse_curl_command()` - 解析浏览器复制的 curl 命令为 requests 参数
- `parse_random_cron()` - 支持随机时间窗口语法 `R(09:00-09:30) * * *`
- `execute_checkin()` - 执行签到，含重试逻辑和 Webhook 通知
- `add_job()` / `remove_job()` - 管理 APScheduler 定时任务
- `auto_clean_logs()` - 每日凌晨 3:00 自动清理旧日志

### `models.py` - 数据模型
- `Account` - 签到账号配置 (curl_command, cron_expr, retry_count 等)
- `CheckinLog` - 签到执行记录 (含请求/响应详情)
- `Config` - 系统配置 (密码、Webhook、自动清理设置)
- `init_db()` / `migrate_database()` - 数据库初始化和迁移

### `app.py` - API 路由
- 账号 CRUD: `/api/accounts`
- 手动签到: `/api/checkin/<id>`
- 日志查询: `/api/logs`
- 系统配置: `/api/system/config`, `/api/webhook/config`
- 导入导出: `/api/accounts/export`, `/api/accounts/import`

## 关键设计

1. **Curl 解析器**: 支持多种 curl 格式，自动提取 URL、Method、Headers、Cookies、Data
2. **随机时间窗口**: Cron 语法扩展 `R(HH:MM-HH:MM)` 实现签到时间随机化
3. **配置持久化**: 所有配置存储在 SQLite `configs` 表，首次启动从环境变量初始化
4. **数据库连接**: 使用 `db.connect(reuse_if_open=True)` 模式，`@app.teardown_appcontext` 自动关闭

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ADMIN_PASSWORD` | `acgo123321` | 管理员密码 |
| `SECRET_KEY` | (内置) | Flask session 密钥 |
| `AUTO_CLEAN_LOGS` | `false` | 启用自动清理日志 |
| `MAX_LOGS_COUNT` | `500` | 保留最新日志条数 |

## CI/CD

GitHub Actions 自动构建并推送 Docker 镜像到 GHCR：
- Push 到 main/master → 构建 `latest` 标签
- 创建 `v*.*.*` 标签 → 构建版本标签
- 支持 `linux/amd64` 和 `linux/arm64` 多平台
