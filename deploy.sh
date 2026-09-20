#!/bin/sh
# Usage: sh deploy.sh [deployment-directory]
set -eu

fail() { printf '部署失败：%s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = Linux ] || fail '此脚本仅支持 Linux。'
[ "$#" -le 1 ] || fail '用法：sh deploy.sh [部署目录]'
for dependency in curl docker mktemp od tr sed; do
    command -v "$dependency" >/dev/null 2>&1 || fail "缺少命令：$dependency，请先安装。"
done
docker compose version >/dev/null 2>&1 || fail '请先安装 Docker Compose 插件。'
docker info >/dev/null 2>&1 || fail 'Docker 未启动或当前用户无权访问 Docker。'

deploy_dir=${1:-/data/acgo}
base_url=https://raw.githubusercontent.com/yz0812/acGo/main
umask 077
mkdir -p "$deploy_dir"
cd "$deploy_dir"
deploy_dir=$(pwd -P)
stage=$(mktemp -d "$deploy_dir/.deploy.XXXXXX")
trap 'rm -rf -- "$stage"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

download() {
    curl -fL --retry 3 --connect-timeout 15 --max-time 120 "$base_url/$1" -o "$2"
}
random_hex() { od -An -N "$1" -tx1 /dev/urandom | tr -d ' \n'; }

# Stage all downloads before publishing files; keep existing deployment settings.
if [ ! -f docker-compose.yml ]; then
    download docker-compose.yml "$stage/docker-compose.yml"
fi
new_env=false
if [ ! -f .env ]; then
    download .env.example "$stage/env.example"
    password=$(random_hex 16)
    secret=$(random_hex 32)
    [ "${#password}" -eq 32 ] && [ "${#secret}" -eq 64 ] || fail '随机凭据生成失败。'
    sed -e "s/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=$password/" \
        -e "s/^SECRET_KEY=.*/SECRET_KEY=$secret/" \
        "$stage/env.example" > "$stage/.env"
    new_env=true
fi
if [ -f "$stage/docker-compose.yml" ]; then
    mv "$stage/docker-compose.yml" docker-compose.yml
fi
if [ "$new_env" = true ]; then
    mv "$stage/.env" .env
fi
chmod 600 .env

# Explicit files prevent an unrelated parent-directory Compose file from being used.
docker compose --env-file .env -f docker-compose.yml config --quiet
docker compose --env-file .env -f docker-compose.yml up -d --pull always --wait --wait-timeout 120

printf '\n部署完成，服务已通过健康检查。\n部署目录：%s\n访问地址：http://服务器IP:5000\n' "$deploy_dir"
if [ "$new_env" = true ]; then
    printf '初始化管理员密码：%s\n（已有数据库的密码保持不变。）\n' "$password"
else
    printf '已有 .env 和数据库已保留，请使用原管理员密码登录。\n'
fi
