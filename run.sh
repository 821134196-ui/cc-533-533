#!/usr/bin/env bash
# 一条命令启动手作工作室履约台（自动建库、播种含缺件案例的演示数据）。
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
echo "启动中… 浏览器打开 http://127.0.0.1:${PORT}"
exec python3 app.py --port "${PORT}" "$@"
