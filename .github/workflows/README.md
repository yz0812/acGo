# GitHub Actions Workflow 说明

## 文件说明

### docker-publish.yml

自动构建并推送 Docker 镜像到 GitHub Container Registry (GHCR)。

## Workflow 配置详解

### 触发条件

```yaml
on:
  push:
    branches: [ "main", "master" ]  # 推送到主分支时触发
    tags: [ 'v*.*.*' ]              # 推送版本标签时触发（如 v1.0.0）
  pull_request:
    branches: [ "main", "master" ]  # PR 时仅构建测试，不推送
```

### 镜像标签策略

Workflow 会自动生成以下标签：

1. **分支标签**：`main`, `master`
2. **版本标签**：`v1.0.0`, `1.0`, `1`（从 Git tag 提取）
3. **latest 标签**：仅在默认分支时添加
4. **时间戳标签**：`20231223120000`（每次构建唯一）
5. **SHA 标签**：`sha-abc1234`（Git commit SHA）

### 多平台构建

支持以下平台：
- `linux/amd64` - x86_64 架构
- `linux/arm64` - ARM64 架构

### 构建缓存

使用 GitHub Actions Cache 加速构建：
- `cache-from: type=gha` - 从缓存读取
- `cache-to: type=gha,mode=max` - 写入缓存（最大模式）

## 使用说明

### 首次设置

1. **推送代码到 GitHub**：
   ```bash
   git add .
   git commit -m "Add GitHub Actions workflow"
   git push origin main
   ```

2. **等待自动构建**：
   - 访问仓库的 "Actions" 标签页
   - 查看构建进度和日志

3. **设置镜像可见性**（可选）：
   - 访问 `https://github.com/your-username?tab=packages`
   - 点击 `acgo` 包 → "Package settings"
   - 在 "Danger Zone" 中选择 "Change visibility" → "Public"

### 发布新版本

```bash
# 1. 创建版本标签
git tag v1.0.0

# 2. 推送标签
git push origin v1.0.0

# 3. 自动触发构建
# 镜像将推送到：ghcr.io/your-username/acgo:v1.0.0
```

### 使用预构建镜像

```bash
# 拉取最新版本
docker pull ghcr.io/your-username/acgo:latest

# 拉取指定版本
docker pull ghcr.io/your-username/acgo:v1.0.0

# 运行容器
docker run -d \
  --name acgo \
  -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  -e ADMIN_PASSWORD=acgo123321 \
  ghcr.io/your-username/acgo:latest
```

## 权限说明

Workflow 使用 `GITHUB_TOKEN` 自动认证，无需额外配置 secrets。

所需权限：
- `contents: read` - 读取仓库代码
- `packages: write` - 推送镜像到 GHCR

## 构建时间

- **首次构建**：约 5-10 分钟（无缓存）
- **后续构建**：约 2-5 分钟（有缓存）
- **多平台构建**：时间约为单平台的 1.5-2 倍

## 故障排查

### 构建失败

1. 检查 Actions 日志中的错误信息
2. 确认 Dockerfile.multistage 语法正确
3. 确认 requirements.txt 中的依赖可安装

### 推送失败

1. 检查仓库是否启用了 Actions
2. 确认 Workflow 权限设置正确
3. 检查 GITHUB_TOKEN 是否有 packages:write 权限

### 镜像拉取失败

1. 确认镜像已成功推送（查看 Actions 日志）
2. 如果是私有镜像，需要先登录：
   ```bash
   echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin
   ```

## 自定义配置

### 修改构建平台

编辑 `docker-publish.yml` 中的 `platforms` 字段：

```yaml
platforms: linux/amd64,linux/arm64,linux/arm/v7
```

### 修改 Dockerfile

编辑 `docker-publish.yml` 中的 `file` 字段：

```yaml
file: ./Dockerfile  # 使用标准 Dockerfile
```

### 添加构建参数

```yaml
build-args: |
  PYTHON_VERSION=3.11
  BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
```

## 参考资料

- [GitHub Actions 文档](https://docs.github.com/en/actions)
- [Docker Build Push Action](https://github.com/docker/build-push-action)
- [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
