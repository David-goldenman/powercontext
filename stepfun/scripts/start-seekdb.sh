#!/bin/bash
# 启动本地 seekdb server（StepFun 赛道：seekdb 作为数据层）
# 用法：bash scripts/start-seekdb.sh [端口] [数据目录]
set -euo pipefail

PORT="${1:-2881}"
BASE="${2:-./.seekdb}"

if ! command -v seekdb >/dev/null 2>&1; then
  cat <<'EOF'
[错误] 未找到 seekdb 可执行文件。

安装方式（任选其一）：
  1) 官方脚本 / 发行包：见 https://github.com/oceanbase/seekdb
  2) 已有编译产物：将 seekdb 加入 PATH，或用 SEEKDB_BIN 指定完整路径

验证：seekdb --version
EOF
  exit 1
fi

SEEKDB_BIN="${SEEKDB_BIN:-seekdb}"
mkdir -p "$BASE"

echo "启动 seekdb：port=$PORT base-dir=$BASE"
"$SEEKDB_BIN" --base-dir "$BASE" --port "$PORT" \
  --parameter datafile_size=2G \
  --parameter log_disk_size=2G

echo "等待就绪…"
for i in $(seq 1 30); do
  if (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then
    echo "seekdb 已在 127.0.0.1:$PORT 监听"
    exit 0
  fi
  sleep 1
done

echo "[警告] 30 秒内未检测到端口 $PORT，请检查 $BASE/log/seekdb.log"
exit 1
