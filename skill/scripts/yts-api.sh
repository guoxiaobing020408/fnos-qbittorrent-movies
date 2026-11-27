#!/usr/bin/env bash
# =============================================================================
# YTS 电影搜索 —— 入口壳
#
# 实际逻辑在同目录的 yts.py（纯 Python 标准库实现，不需要 curl / jq）。
# 这个壳只做一件事：找个能用的 python 解释器，把参数原样转过去。
#
# 之所以保留 .sh 入口，是为了让调用方式稳定：
#   ./yts-api.sh search "the matrix" --limit 5
# =============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMPL="$HERE/yts.py"

if [[ ! -f "$IMPL" ]]; then
  printf '错误：找不到实现文件 %s\n' "$IMPL" >&2
  exit 127
fi

# 依次尝试 python3 / python
for candidate in "${PYTHON:-}" python3 python; do
  [[ -n "$candidate" ]] || continue
  if command -v "$candidate" >/dev/null 2>&1; then
    exec "$candidate" "$IMPL" "$@"
  fi
done

printf '错误：找不到 python3 或 python，请先安装 Python 3.8+\n' >&2
exit 127
