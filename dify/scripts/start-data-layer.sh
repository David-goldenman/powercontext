#!/usr/bin/env bash
# 启动「离谱发明工坊」的数据层，并接入 Dify 的 compose 网络。
#
# 作用：把 seekdb + fusion-api 叠加到 Dify 的 compose 项目上，
#      使 fusion-api 与 Dify 的 api 容器同处一个默认网络，
#      DSL 里用服务名 http://fusion-api:3000 就能直接访问。
#
# 用法：
#   bash scripts/start-data-layer.sh [Dify 的 docker 目录]
#   Dify 的 docker 目录默认 /opt/dify/docker，或用环境变量 DIFY_DOCKER_DIR 指定。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIFY_ROOT="$(dirname "$SCRIPT_DIR")"                 # 本仓库的 dify/ 目录
DIFY_DOCKER_DIR="${1:-${DIFY_DOCKER_DIR:-/opt/dify/docker}}"

if [ ! -f "$DIFY_DOCKER_DIR/docker-compose.yaml" ]; then
  echo "找不到 $DIFY_DOCKER_DIR/docker-compose.yaml" >&2
  echo "请把 Dify 的 docker 目录作为第一个参数传入，或设置 DIFY_DOCKER_DIR。" >&2
  exit 1
fi

# compose 的 build 上下文按 project-directory 解析，必须给绝对路径
export FUSION_BACKEND_DIR="$DIFY_ROOT/backend"

echo "==> 数据层源码目录: $FUSION_BACKEND_DIR"
echo "==> Dify compose 目录: $DIFY_DOCKER_DIR"

cd "$DIFY_DOCKER_DIR"
docker compose \
  -f docker-compose.yaml \
  -f "$DIFY_ROOT/docker-compose.data.yml" \
  up -d --build fusion-seekdb fusion-api

echo
echo "==> 等待 fusion-api 就绪…"
for i in $(seq 1 60); do
  if docker exec fusion-api wget -qO- http://127.0.0.1:3000/health 2>/dev/null | grep -q '"ok":true'; then
    echo "==> 就绪：$(docker exec fusion-api wget -qO- http://127.0.0.1:3000/health)"
    echo
    echo "接下来在 Dify 里：应用 → 导入 DSL → dsl/fusion_lab.yml"
    exit 0
  fi
  sleep 3
done

echo "fusion-api 未在预期时间内就绪，最近日志：" >&2
docker logs --tail 30 fusion-api >&2
exit 1
