#!/usr/bin/env bash
# 字幕获取 / 转换 / 安装 —— 入口壳，实际逻辑在 subtitle.py
# 纯 Python 标准库实现，不需要 jq / curl / unzip
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python "$DIR/subtitle.py" "$@"
