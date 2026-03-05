#!/usr/bin/env bash
# ============================================================
#  编译 .proto → Python (服务端) + Dart (客户端)
#  用法: cd project_root && bash protos/build_protos.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PROTO_DIR="$PROJECT_ROOT/protos"
PY_OUT="$PROJECT_ROOT/server/app/generated"
DART_OUT="$PROJECT_ROOT/client/lib/generated"

# 创建输出目录
mkdir -p "$PY_OUT" "$DART_OUT"

echo "==> 编译 Python protobuf ..."
python -m grpc_tools.protoc \
  -I "$PROTO_DIR" \
  --python_out="$PY_OUT" \
  "$PROTO_DIR"/*.proto

# 生成 __init__.py 方便导入
touch "$PY_OUT/__init__.py"

echo "==> 编译 Dart protobuf ..."
protoc \
  -I "$PROTO_DIR" \
  --dart_out="$DART_OUT" \
  "$PROTO_DIR"/*.proto

echo "==> 完成! Python → $PY_OUT  |  Dart → $DART_OUT"
