#!/bin/sh
# 容器启动入口：依赖已在镜像构建阶段安装，此处直接启动服务。
set -e

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"

echo "启动口述史事件时间线核对服务：http://${HOST}:${PORT}"
exec uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
