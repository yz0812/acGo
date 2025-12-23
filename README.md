# 签到管理系统 (ACGO)

一个轻量级的自动签到管理系统，支持通过 Curl 命令配置签到任务，使用 Cron 表达式定时执行。

## 功能特性

- ✅ **Curl 命令支持**：直接粘贴浏览器复制的 curl 命令，自动解析
- ✅ **定时任务**：使用 Cron 表达式灵活配置执行时间
- ✅ **随机时间窗口**：支持在指定时间段内随机执行签到（如 9:00-9:30）
- ✅ **失败重试**：可配置重试次数和重试间隔
- ✅ **密码保护**：Web 界面需要密码登录，支持在线修改密码
- ✅ **签到记录**：完整的签到日志记录，支持分页查看
- ✅ **自动清理**：可配置自动清理旧的签到记录
- ✅ **手动触发**：支持立即执行签到
- ✅ **账号导入导出**：支持批量导入导出账号配置
- ✅ **Webhook 通知**：支持签到完成后的 Webhook 回调通知
- ✅ **系统设置**：Web 界面管理所有系统配置
- ✅ **轻量级**：基于 SQLite，无需额外数据库

## 技术栈

- **后端**：Flask 3.0
- **定时任务**：APScheduler 3.10
- **数据库**：SQLite + Peewee ORM
- **HTTP 请求**：Requests（自定义 Curl 解析器）
- **前端**：原生 HTML/CSS/JavaScript
- **容器化**：Docker（多平台支持：amd64/arm64）

## 实例
![图片](./img/img.png)

## 快速开始

### 方式 1：Docker 部署（推荐）

#### 使用 GHCR 预构建镜像（最快）

直接使用 GitHub Container Registry 的预构建镜像，无需本地构建：

```bash
# 拉取最新镜像（支持 amd64 和 arm64）
docker pull ghcr.io/your-username/acgo:latest

# 运行容器
docker run -d \
  --name acgo \
  -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  -e ADMIN_PASSWORD=acgo123321 \
  -e AUTO_CLEAN_LOGS=false \
  -e MAX_LOGS_COUNT=500 \
  ghcr.io/your-username/acgo:latest

# 访问系统
# 浏览器打开 http://localhost:5000
# 默认密码：acgo123321
```

**可用标签：**
- `latest` - 最新稳定版本
- `v1.0.0` - 指定版本号
- `20231223120000` - 时间戳版本
- `sha-abc1234` - Git commit 版本

#### 使用 Docker Compose（最简单）

```bash
# 1. 克隆或下载项目
git clone <repository-url>
cd acgo

# 2. 启动服务
docker-compose up -d

# 3. 访问系统
# 浏览器打开 http://localhost:5000
# 默认密码：acgo123321
```

#### 使用 Docker 命令

```bash
# 1. 构建镜像
docker build -t acgo:latest .

# 2. 运行容器
docker run -d \
  --name acgo \
  -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  -e ADMIN_PASSWORD=acgo123321 \
  -e AUTO_CLEAN_LOGS=false \
  -e MAX_LOGS_COUNT=500 \
  acgo:latest

# 3. 查看日志
docker logs -f acgo
```

#### 自定义配置

编辑 `docker-compose.yml` 中的环境变量：

```yaml
environment:
  - ADMIN_PASSWORD=your_password  # 修改管理员密码
  - SECRET_KEY=your_secret_key    # 修改密钥（可选）
```

### 方式 2：本地部署

#### 1. 安装依赖

```bash
pip install -r requirements.txt
```

#### 2. 配置环境变量（可选）

复制 `.env.example` 为 `.env` 并修改配置：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
# 管理员密码（可选，默认：acgo123321）
ADMIN_PASSWORD=your_secure_password

# Flask 密钥（可选，已有安全默认值）
SECRET_KEY=your-random-secret-key

# 自动清理签到记录（可选，默认：false）
# 设置为 true 启用自动清理，每天凌晨 3:00 执行
AUTO_CLEAN_LOGS=false

# 最大签到记录数（可选，默认：500）
# 当启用自动清理时，保留最新的 N 条记录
MAX_LOGS_COUNT=500
```

**注意**：首次启动后，所有配置（包括密码）都会保存到数据库中，后续可以通过 Web 界面的"系统设置"进行修改，无需再修改环境变量。

#### 3. 启动服务

```bash
python app.py
```

**注意**：首次启动时会自动创建数据库和表结构，无需手动初始化。

服务将在 `http://0.0.0.0:5000` 启动。

#### 4. 访问系统

打开浏览器访问 `http://localhost:5000`，使用配置的密码登录（默认：acgo123321）。

## 使用说明

### 添加签到账号

1. 点击"添加账号"按钮
2. 填写账号名称
3. 粘贴完整的 curl 命令（从浏览器开发者工具复制）
4. 配置 Cron 表达式（例如：`0 8 * * *` 表示每天 8 点）
5. 设置重试次数和重试间隔
6. 保存

### Curl 命令示例

```bash
curl 'https://api.example.com/checkin' \
  -H 'Authorization: Bearer your_token' \
  -H 'Content-Type: application/json' \
  --data-raw '{"user_id": 123}'
```

### Cron 表达式说明

支持两种格式：

#### 1. 标准 Cron 表达式

格式：`分 时 日 月 周`

常用示例：
- `0 8 * * *` - 每天 8:00 执行
- `0 */6 * * *` - 每 6 小时执行
- `0 0 * * 0` - 每周日 0:00 执行
- `30 9 1 * *` - 每月 1 号 9:30 执行
- `30 14 * * 1-5` - 每周一到周五 14:30 执行

#### 2. 随机时间窗口（新功能）

格式：`R(开始时间-结束时间) 日 月 周`

这个功能可以让签到在指定的时间窗口内随机执行，避免固定时间签到被检测。

常用示例：
- `R(09:00-09:30) * * *` - 每天 9:00-9:30 之间随机执行
- `R(08:00-08:15) * * 1-5` - 每周一到周五 8:00-8:15 随机执行
- `R(20:00-22:00) 1 * *` - 每月 1 号 20:00-22:00 随机执行
- `R(07:00-07:10) * * 0,6` - 每周六日 7:00-7:10 随机执行

**工作原理**：系统会在窗口开始时间触发任务，然后随机延迟 0 到窗口长度的时间后执行签到。

## 项目结构

```
acgo/
├── app.py              # Flask 主程序
├── models.py           # 数据库模型
├── auth.py             # 认证模块
├── scheduler.py        # 定时任务调度
├── requirements.txt    # 依赖清单
├── .env.example        # 环境变量示例
├── templates/          # HTML 模板
│   ├── login.html      # 登录页
│   └── index.html      # 主界面
├── static/             # 静态资源
│   └── style.css       # 样式文件
└── acgo.db             # SQLite 数据库（自动生成）
```

## API 接口

### 账号管理

- `GET /api/accounts` - 获取账号列表
- `POST /api/accounts` - 创建账号
- `PUT /api/accounts/<id>` - 更新账号
- `DELETE /api/accounts/<id>` - 删除账号
- `GET /api/accounts/export` - 导出所有账号
- `POST /api/accounts/import` - 批量导入账号

### 签到操作

- `POST /api/checkin/<id>` - 手动立即签到
- `GET /api/logs` - 获取签到记录（支持分页）
- `GET /api/stats` - 获取统计数据
- `DELETE /api/logs/clear` - 清除签到记录

### 系统配置

- `GET /api/system/config` - 获取系统配置
- `POST /api/system/config` - 保存系统配置
- `POST /api/system/password` - 修改管理员密码

### Webhook 配置

- `GET /api/webhook/config` - 获取 Webhook 配置
- `POST /api/webhook/config` - 保存 Webhook 配置
- `POST /api/webhook/test` - 测试 Webhook

## 系统设置

点击右上角的"系统设置"按钮，可以在 Web 界面中管理以下配置：

### 1. 修改管理员密码

- 需要输入旧密码验证
- 新密码至少 6 位
- 修改成功后需要重新登录

### 2. 签到记录自动清理

- **启用自动清理**：开启后，每天凌晨 3:00 自动执行清理
- **最大记录数**：保留最新的 N 条签到记录（最小 100 条）
- 超出限制的旧记录会被自动删除

### 3. Webhook 通知

- 支持在签到完成后发送 Webhook 通知
- 可自定义请求方法（POST/GET）和请求头
- 可选择是否包含完整的签到响应内容

## 注意事项

1. **密码安全**：务必修改默认密码，首次启动后可通过"系统设置"修改
2. **Curl 命令**：确保包含完整的请求头和请求体
3. **时区问题**：Cron 表达式使用服务器本地时区
4. **日志清理**：建议启用自动清理功能，避免数据库过大
5. **随机窗口**：使用随机时间窗口时，确保窗口不跨越午夜（暂不支持）
6. **配置持久化**：所有配置保存在数据库中，备份 `data/acgo.db` 即可保留所有数据

## CI/CD 自动构建

项目使用 GitHub Actions 自动构建并推送 Docker 镜像到 GHCR。

### 触发条件

- **Push 到 main/master 分支**：自动构建并推送 `latest` 标签
- **创建版本标签**（如 `v1.0.0`）：自动构建并推送版本标签
- **Pull Request**：仅构建测试，不推送镜像

### 多平台支持

自动构建支持以下平台：
- `linux/amd64` - x86_64 架构（常规服务器、PC）
- `linux/arm64` - ARM64 架构（树莓派 4、Apple Silicon、ARM 服务器）

### 发布新版本

```bash
# 1. 创建版本标签
git tag v1.0.0

# 2. 推送标签到 GitHub
git push origin v1.0.0

# 3. GitHub Actions 自动构建并推送镜像
# 镜像地址：ghcr.io/your-username/acgo:v1.0.0
```

### 查看构建状态

访问仓库的 Actions 标签页查看构建进度和日志。

### 镜像可见性设置

首次推送后，GHCR 包默认是私有的。如需公开访问：

1. 访问 `https://github.com/your-username?tab=packages`
2. 点击 `acgo` 包
3. 点击 "Package settings"
4. 在 "Danger Zone" 中选择 "Change visibility" → "Public"

## 常见问题

### Q: 如何获取 Curl 命令？

A: 
1. 打开浏览器开发者工具（F12）
2. 切换到 Network 标签
3. 手动执行一次签到操作
4. 找到对应的请求，右键选择 "Copy as cURL"

### Q: 签到失败怎么办？

A: 
1. 检查 Curl 命令是否完整
2. 检查 Token 是否过期
3. 查看签到记录中的错误信息
4. 尝试手动执行一次签到

### Q: 如何修改定时任务？

A: 直接在账号管理中点击"编辑"，修改 Cron 表达式后保存即可。

### Q: 如何使用随机时间窗口？

A: 在添加或编辑账号时，Cron 表达式使用 `R(开始时间-结束时间) 日 月 周` 格式，例如 `R(09:00-09:30) * * *` 表示每天 9:00-9:30 之间随机执行。

### Q: 如何启用自动清理？

A: 点击右上角"系统设置"按钮，勾选"启用自动清理"，设置最大记录数后保存即可。系统会在每天凌晨 3:00 自动清理超出限制的旧记录。

### Q: 如何修改管理员密码？

A: 点击右上角"系统设置"按钮，在"修改管理员密码"区域输入旧密码和新密码，点击"修改密码"即可。修改成功后需要重新登录。

### Q: 如何批量导入账号？

A: 点击"导入账号"按钮，选择之前导出的 JSON 文件即可。重名账号会自动重命名。

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！
