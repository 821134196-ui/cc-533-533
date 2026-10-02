#!/usr/bin/env bash
# 一条本地命令：重建演示数据（含缺件案例）并启动服务。
#   ./run.sh [--no-reset] [端口]
set -euo pipefail
cd "$(dirname "$0")"

PORT=8000
RESET=1
for arg in "$@"; do
  case "$arg" in
    --no-reset) RESET=0 ;;
    *) PORT="$arg" ;;
  esac
done

if [ "$RESET" = "1" ]; then
  echo "▶ 重建数据库与演示数据（含缺件 / 替代色 / 损坏索赔案例）…"
  python3 -m fulfillment.cli reseed
fi

echo "▶ 启动服务 http://127.0.0.1:${PORT}"
exec python3 -m fulfillment.server "$PORT"
