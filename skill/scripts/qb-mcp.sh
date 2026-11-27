#!/usr/bin/env bash
# qBittorrent MCP 客户端（懒猫微服 / qbittorrent-mcp 1.0.0）
# Streamable HTTP，仅支持 POST /mcp，凭证走 Authorization: Bearer <key>
set -uo pipefail

# Git Bash / MSYS2 会把 /vol1/xxx 这类参数自动改写成 Windows 路径，
# 远程 Linux 上的绝对路径必须原样透传。
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

CFG="${QBIT_MCP_CONFIG:-$HOME/.workbuddy/mcp.json}"
# MSYS_NO_PATHCONV 下 /c/Users/... 这种 MSYS 路径原生 python 打不开，先转成 Windows 形式
if command -v cygpath >/dev/null 2>&1; then
  CFG="$(cygpath -w "$CFG" 2>/dev/null || printf '%s' "$CFG")"
fi

usage() {
  cat <<'EOF'
qBittorrent MCP 客户端

说明：
  通过 MCP（JSON-RPC over HTTP，POST /mcp）控制 qBittorrent。
  自动从 ~/.workbuddy/mcp.json 的 mcpServers.qbittorrent 读取 url 与
  Authorization: Bearer <key>，不需要手工传地址和密钥。

用法：
  ./qb-mcp.sh <命令> [参数]

命令：
  doctor                       一键自检：配置 / MCP 连通 / qBittorrent 状态 / 磁盘
  info                         qBittorrent 版本与 MCP 服务信息
  status                       全局传输状态（速度/连接数/累计量）
  list [filter] [--limit N]    任务列表；filter: all/downloading/seeding/
                               completed/stopped/active/paused/errored
  detail <hash>                单任务详情（属性 + 文件列表）
  add <magnet|http(s)-torrent> [--save-path P] [--category C] [--stopped]
                               添加任务
  start <hash...>              开始 / 恢复
  stop <hash...>               暂停
  delete <hash...> [--files]   删除（--files 连文件一起删）
  api <path> [--method M] [--params JSON]
                               通用 API 透传，path 如 app/preferences
  upload-limit [--set BYTES]   查看全局上传限速；--set 直接设置
                               不加 --set 且当前未限制时，设为 128 KiB/s
  tools                        列出 MCP 暴露的全部工具
  call <tool> [json]           直接调用任意 MCP 工具（raw 的别名）
  raw <tool> [json]            同上，输出原始 JSON

配置（环境变量，均可省略）：
  QBIT_MCP_CONFIG   mcp.json 路径，默认 ~/.workbuddy/mcp.json
  QBIT_MCP_URL      覆盖服务地址，如 http://192.168.1.100:8081/mcp
  QBIT_MCP_TOKEN    覆盖 API Key（不带 "Bearer " 前缀）

依赖：bash、python（3.8+，仅用标准库 urllib）
EOF
}

die() { printf '错误：%s\n\n' "$*" >&2; exit 2; }

# ---------- 读取配置 ----------
CONF_OUT="$(QBIT_CFG="$CFG" QB_U="${QBT_MCP_URL:-${QBIT_MCP_URL:-}}" QB_T="${QBT_MCP_KEY:-${QBIT_MCP_TOKEN:-}}" python - <<'PY'
import json, os
url = os.environ.get('QB_U', '')
tok = os.environ.get('QB_T', '')
if not url or not tok:
    try:
        d = json.load(open(os.environ['QBIT_CFG'], encoding='utf-8'))
        s = (d.get('mcpServers') or {}).get('qbittorrent') or {}
        url = url or s.get('url', '')
        auth = (s.get('headers') or {}).get('Authorization', '')
        if not tok and auth.lower().startswith('bearer '):
            tok = auth[7:].strip()
        if not tok:
            tok = (s.get('headers') or {}).get('X-Api-Key', '')
    except Exception:
        pass
print(url.strip() or '-')
print(tok.strip() or '-')
PY
)" || die "读取配置失败：$CFG"

MCP_URL="$(printf '%s' "$CONF_OUT" | sed -n '1p')"
MCP_TOKEN="$(printf '%s' "$CONF_OUT" | sed -n '2p')"
[[ "$MCP_URL" == "-" ]] && MCP_URL="http://127.0.0.1:8081/mcp"
[[ "$MCP_TOKEN" == "-" ]] && die "缺少 API Key。请在 $CFG 的 mcpServers.qbittorrent.headers.Authorization 写入 \"Bearer <key>\"，或设置 QBIT_MCP_TOKEN"

# ---------- MCP 调用 ----------
# rpc <tool_name> <arguments_json> [fmt]
# fmt: json(原样裁剪) | info | status | list | detail | pres | plain
rpc() {
  QB_URL="$MCP_URL" QB_TOKEN="$MCP_TOKEN" QB_TOOL="$1" QB_ARGS="${2:-{\}}" QB_FMT="${3:-plain}" python - <<'PY'
import json, os, sys, urllib.request, urllib.error

url   = os.environ['QB_URL']
token = os.environ['QB_TOKEN']
tool  = os.environ['QB_TOOL']
try:
    args = json.loads(os.environ.get('QB_ARGS') or '{}')
except json.JSONDecodeError as e:
    print('错误：参数不是合法 JSON：%s' % e); sys.exit(2)
fmt = os.environ.get('QB_FMT', 'plain')

payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": tool, "arguments": args}}
req = urllib.request.Request(
    url, data=json.dumps(payload).encode('utf-8'), method='POST',
    headers={'Content-Type': 'application/json',
             'Accept': 'application/json, text/event-stream',
             'Authorization': 'Bearer ' + token})

try:
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode('utf-8', 'replace')
except urllib.error.HTTPError as e:
    code = e.code
    detail = e.read().decode('utf-8', 'replace')[:300]
    if code == 401:
        print('错误：401 未授权。API Key 不正确，请检查 %s 或环境变量 QBIT_MCP_TOKEN。' % os.environ.get('QBIT_MCP_CONFIG', 'mcp.json'))
    elif code == 404:
        print('错误：404 路径不对。MCP 服务只接受 POST /mcp，请确认地址 %s。' % url)
    else:
        print('错误：HTTP %s — %s' % (code, detail))
    sys.exit(1)
except urllib.error.URLError as e:
    print('错误：连不上 %s（%s）。请确认这台机器能访问该地址，且 qbittorrent-mcp 服务在运行。' % (url, e.reason))
    sys.exit(1)

# 兼容 SSE 分帧
text = body.strip()
if text.startswith('event:') or text.startswith('data:'):
    parts = [l[5:].strip() for l in text.splitlines() if l.startswith('data:')]
    text = ''.join(parts) or '{}'

try:
    rpc_resp = json.loads(text)
except json.JSONDecodeError:
    print('错误：服务返回的不是 JSON：%s' % text[:300]); sys.exit(1)

if 'error' in rpc_resp:
    err = rpc_resp['error']
    msg = err.get('message', err) if isinstance(err, dict) else err
    print('错误：MCP 调用失败 — %s' % msg); sys.exit(1)

res = rpc_resp.get('result') or {}
chunks = [c.get('text', '') for c in (res.get('content') or []) if c.get('type') == 'text']
raw = '\n'.join(chunks).strip()
if res.get('isError'):
    print('错误：%s' % (raw or '工具执行失败')); sys.exit(1)

if fmt == 'json':
    print(raw); sys.exit(0)

def load():
    try:
        return json.loads(raw)
    except Exception:
        return None

def human(n):
    try: n = float(n or 0)
    except Exception: return '-'
    for u in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if abs(n) < 1024 or u == 'TiB':
            return ('%.2f %s' % (n, u)) if u != 'B' else ('%d B' % n)
        n /= 1024.0

def pct(p):
    try: return '%.1f%%' % (float(p or 0) * 100)
    except Exception: return '-'

def eta(sec):
    try: sec = int(sec)
    except Exception: return '-'
    if sec <= 0 or sec >= 8640000: return '-'
    d, sec = divmod(sec, 86400); h, sec = divmod(sec, 3600); m, s = divmod(sec, 60)
    return ('%dd' % d if d else '') + ('%dh' % h if h or d else '') + ('%dm' % m if m or h or d else '') + '%ds' % s

STATE_CN = {
 'downloading': '下载中', 'stalledDL': '等待中', 'metaDL': '取元数据',
 'forcedDL': '强制下载', 'uploading': '做种', 'stalledUP': '做种-无请求',
 'forcedUP': '强制做种', 'pausedDL': '已暂停', 'pausedUP': '已完成',
 'stoppedDL': '已停止', 'stoppedUP': '已完成', 'queuedDL': '排队中',
 'queuedUP': '排队做种', 'checkingDL': '校验中', 'checkingUP': '校验中',
 'error': '出错', 'missingFiles': '文件丢失', 'moving': '移动中',
 'unknown': '未知', 'allocating': '分配空间',
}

data = load()
if data is None:
    print(raw); sys.exit(0)

if fmt == 'info':
    print('qBittorrent 版本：%s' % data.get('qbit_version', '-'))
    m = data.get('mcp_server') or {}
    print('MCP 服务：%s %s' % (m.get('name', '-'), m.get('version', '')))
elif fmt == 'status':
    conn = data.get('connection_status', '-')
    print('连接状态：%s' % conn)
    print('下载速度：%s/s' % human(data.get('dl_info_speed')))
    print('上传速度：%s/s' % human(data.get('up_info_speed')))
    print('累计下载：%s' % human(data.get('alltime_dl')))
    print('累计上传：%s' % human(data.get('alltime_ul')))
    if 'dl_rate_limit' in data:
        print('下载限速：%s' % ('未限制' if not data.get('dl_rate_limit') else human(data['dl_rate_limit']) + '/s'))
    if 'up_rate_limit' in data:
        print('上传限速：%s' % ('未限制' if not data.get('up_rate_limit') else human(data['up_rate_limit']) + '/s'))
elif fmt == 'list':
    if isinstance(data, dict):
        ts = data.get('torrents') or []
        total = data.get('total', len(ts))
    else:
        ts = data or []
        total = len(ts)
    if not ts:
        print('没有任务。')
    else:
        print('共 %d 个任务\n' % total)
        for t in ts:
            st = t.get('state', '')
            print('· %s' % (t.get('name') or '-'))
            print('  状态 %s | 进度 %s | 大小 %s' % (
                STATE_CN.get(st, st or '-'), pct(t.get('progress')), human(t.get('size'))))
            print('  下载 %s/s | 上传 %s/s | 剩余 %s' % (
                human(t.get('dlspeed')), human(t.get('upspeed')), eta(t.get('eta'))))
            print('  种子 %s / 连接 %s | 分享率 %s | 顺序下载 %s' % (
                t.get('num_seeds', 0), t.get('num_leechs', 0),
                ('%.2f' % t['ratio']) if isinstance(t.get('ratio'), (int, float)) else '-',
                '开启' if t.get('seq_dl') else '关闭'))
            print('  Hash %s' % t.get('hash', '-'))
            print('  路径 %s' % (t.get('save_path') or '-'))
            print()
elif fmt == 'detail':
    # 实际结构：{hash, properties:{...}, files:[...]}
    p = data.get('properties') or {}
    h = data.get('hash') or p.get('hash') or '-'
    print('Hash：%s' % h)
    if p.get('name'):
        print('名称：%s' % p.get('name'))
    print('保存路径：%s' % (p.get('save_path') or '-'))
    print('总大小：%s' % human(p.get('total_size')))
    print('分享率：%s' % (p.get('share_ratio', '-')))
    print('已完成：%s' % human(p.get('completed')))
    for key in ('dl_speed', 'up_speed'):
        if key in p:
            print('%s：%s/s' % ('下载速度' if key == 'dl_speed' else '上传速度', human(p[key])))
    if p.get('dl_limit') not in (None, -1, 0):
        print('单任务下载限速：%s/s' % human(p['dl_limit']))
    if p.get('up_limit') not in (None, -1, 0):
        print('单任务上传限速：%s/s' % human(p['up_limit']))
    if p.get('addition_date', -1) and p.get('addition_date') > 0:
        import datetime
        try:
            print('添加时间：%s' % datetime.datetime.fromtimestamp(p['addition_date']).strftime('%Y-%m-%d %H:%M:%S'))
        except Exception:
            pass
    if p.get('comment'):
        print('备注：%s' % p['comment'])
    files = data.get('files') or []
    if files:
        print('\n文件（%d）：' % len(files))
        for f in files[:40]:
            print('  %-10s %s' % (human(f.get('size')), f.get('name')))
        if len(files) > 40:
            print('  … 其余 %d 个省略' % (len(files) - 40))
    else:
        print('\n（暂无文件列表，任务可能还没取到元数据）')
elif fmt == 'pres':
    lim = data.get('up_limit', 0)
    print('%s' % ('0' if lim in (0, -1, '0', '-1') else lim))
else:
    print(json.dumps(data, ensure_ascii=False, indent=2) if not isinstance(data, str) else data)
PY
}

# 列出 MCP 暴露的工具（tools/list）
list_tools() {
  QB_URL="$MCP_URL" QB_TOKEN="$MCP_TOKEN" python - <<'PY'
import json, os, sys, urllib.request, urllib.error
url, token = os.environ['QB_URL'], os.environ['QB_TOKEN']
payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
req = urllib.request.Request(
    url, data=json.dumps(payload).encode('utf-8'), method='POST',
    headers={'Content-Type': 'application/json',
             'Accept': 'application/json, text/event-stream',
             'Authorization': 'Bearer ' + token})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode('utf-8', 'replace')
except urllib.error.HTTPError as e:
    print('错误：HTTP %s（密钥无效或服务异常）' % e.code); sys.exit(1)
except urllib.error.URLError as e:
    print('错误：连不上 %s（%s）' % (url, e.reason)); sys.exit(1)
text = body.strip()
if text.startswith('event:') or text.startswith('data:'):
    text = ''.join(l[5:].strip() for l in text.splitlines() if l.startswith('data:')) or '{}'
d = json.loads(text)
tools = (d.get('result') or {}).get('tools') or []
print('共 %d 个工具\n' % len(tools))
for t in tools:
    req_p = (t.get('inputSchema') or {}).get('required') or []
    print('· %s' % t['name'])
    desc = (t.get('description') or '').replace('\n', ' ')
    print('  %s' % desc[:120])
    if req_p:
        print('  必填：%s' % ', '.join(req_p))
    print()
PY
}

# ---------- 参数解析 ----------
[[ $# -eq 0 ]] && { usage; exit 0; }

cmd="$1"; shift

case "$cmd" in
  -h|--help) usage; exit 0 ;;
  doctor)
    fails=0
    printf '=== 懒猫微服电影搜索 · 自检 ===\n\n'

    if [[ -f "$CFG" ]]; then
      printf '[1] 配置文件     OK    %s\n' "$CFG"
    else
      printf '[1] 配置文件     FAIL  找不到 %s\n' "$CFG"; fails=$((fails+1))
    fi

    if [[ -n "${MCP_TOKEN:-}" && "${MCP_TOKEN}" != "-" ]]; then
      printf '[2] API Key      OK    已读取（不显示明文）\n'
    else
      printf '[2] API Key      FAIL  没读到，检查 mcp.json 或设 QBT_MCP_KEY\n'; fails=$((fails+1))
    fi

    if doc_out="$(rpc qb_app_info '{}' info 2>&1)"; then
      printf '[3] MCP 服务     OK    %s\n' "$(printf '%s' "$doc_out" | tr '\n' ' ' | sed 's/  */ /g' | cut -c1-80)"
    else
      printf '[3] MCP 服务     FAIL  %s\n' "$(printf '%s' "$doc_out" | head -1)"; fails=$((fails+1))
    fi

    if doc_out="$(rpc qb_global_status '{}' status 2>&1)"; then
      printf '[4] qBittorrent  OK    %s\n' "$(printf '%s' "$doc_out" | head -1)"
    else
      printf '[4] qBittorrent  FAIL  %s\n' "$(printf '%s' "$doc_out" | head -1)"; fails=$((fails+1))
    fi

    if doc_json="$(rpc qb_api_request '{"method":"GET","path":"sync/maindata"}' json 2>/dev/null)"; then
      doc_line="$(printf '%s' "$doc_json" | python -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('无法解析'); raise SystemExit
ss = d.get('server_state') or {}
free = ss.get('free_space_on_disk') or 0
print('剩余磁盘 %.1f GB | 队列上限 %s' % (free/1073741824.0, ss.get('queueing')))
" 2>/dev/null)"
      printf '[5] 磁盘/队列    OK    %s\n' "${doc_line:-未知}"
    else
      printf '[5] 磁盘/队列    FAIL  取不到 sync/maindata\n'; fails=$((fails+1))
    fi

    # 注意：不要用 curl -o /dev/null —— MSYS_NO_PATHCONV=1 下 /dev/null 会原样传给
    # Windows 版 curl，写不进去导致退出码非 0 而误报；python urllib 在这台机器上
    # SSL 握手会超时。所以直接读正文判断。
    if yts_body="$(curl -s -m 15 "${YTS_API_BASE_URL:-https://movies-api.accel.li/api/v2}/list_movies.json?limit=1" 2>/dev/null)"; then
      if printf '%s' "$yts_body" | grep -q '"status":[[:space:]]*"ok"'; then
        yts_cnt="$(printf '%s' "$yts_body" | sed -n 's/.*"movie_count":[[:space:]]*\([0-9]*\).*/\1/p' | head -1)"
        printf '[6] YTS 搜索     OK    接口正常，片库 %s 部\n' "${yts_cnt:-?}"
      else
        printf '[6] YTS 搜索     FAIL  接口返回异常：%s\n' "$(printf '%s' "$yts_body" | head -c 120)"
        fails=$((fails+1))
      fi
    else
      printf '[6] YTS 搜索     FAIL  访问不了 YTS API（网络/域名/DNS）\n'; fails=$((fails+1))
    fi

    printf '\n'
    if ((fails == 0)); then
      printf '结论：全链路正常，可以直接搜片、加任务。\n'
      printf '提示：若报 file_open 错误，先确认目标目录已授权给应用（见 docs/02-飞牛授权.md）。\n'
    else
      printf '结论：%d 项异常，按上面 FAIL 的提示排查。\n' "$fails"
      exit 1
    fi
    ;;
  info)
    rpc qb_app_info '{}' info
    ;;
  status)
    rpc qb_global_status '{}' status
    ;;
  list)
    filter="all"; limit=""
    while (($#)); do
      case "$1" in
        --limit) limit="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) filter="$1"; shift ;;
      esac
    done
    # 用原生 torrents/info：字段完整（含 seq_dl / ratio / eta），
    # qb_list_torrents 只返回精简字段，看不到顺序下载状态。
    rpc qb_api_request "$(python - "$filter" "$limit" <<'PY'
import json,sys
f = sys.argv[1]; lim = sys.argv[2]
p = {'filter': f or 'all'}
if lim: p['limit'] = int(lim)
print(json.dumps({'method':'GET','path':'torrents/info','params':p}, ensure_ascii=False))
PY
)" list
    ;;
  detail)
    [[ -n "${1:-}" ]] || die "detail 需要 hash"
    rpc qb_torrent_detail "$(python -c "import json,sys;print(json.dumps({'hash':sys.argv[1]}))" "$1")" detail
    ;;
  add)
    url=""; savepath=""; category=""; stopped="false"; sequential="true"
    while (($#)); do
      case "$1" in
        --save-path) savepath="${2:-}"; shift 2 ;;
        --category) category="${2:-}"; shift 2 ;;
        --stopped) stopped="true"; shift ;;
        --no-sequential) sequential="false"; shift ;;
        -h|--help) usage; exit 0 ;;
        -*) die "add 未知参数：$1" ;;
        *)
          if [[ -z "$url" ]]; then url="$1"
          elif [[ -z "$savepath" ]]; then savepath="$1"
          else die "add 参数过多：$1"
          fi
          shift ;;
      esac
    done
    [[ -n "$url" ]] || die "add 需要 magnet 或 .torrent 的 HTTP(S) 地址"

    # 首选原生 torrents/add：能一次带上 sequentialDownload，并返回 added_torrent_ids，
    # 不需要事后猜哪个任务是新的（猜错会改掉别人正在下的任务）。
    add_rc=0
    resp="$(rpc qb_api_request "$(python - "$url" "$savepath" "$category" "$stopped" "$sequential" <<'PY'
import json,sys
url,sp,cat,stopped,seq = sys.argv[1:6]
p = {'urls': url}
if sp:  p['savepath'] = sp
if cat: p['category'] = cat
p['stopped'] = 'true' if stopped == 'true' else 'false'
p['sequentialDownload'] = 'true' if seq == 'true' else 'false'
print(json.dumps({'method':'POST','path':'torrents/add','params':p}, ensure_ascii=False))
PY
)" json 2>&1)" || add_rc=$?

    if ((add_rc != 0)); then
      case "$resp" in
        *409*)
          die "这个任务已经在队列里了（qBittorrent 返回冲突），没有重复添加。"
          ;;
      esac
      # 原生接口不可用时回退到 MCP 专用工具（此时不再保证顺序下载）
      printf '原生 add 未成功（%s），回退到 MCP 添加工具…\n' "$(printf '%s' "$resp" | tr '\n' ' ' | cut -c1-120)"
      rpc qb_add_torrent "$(python - "$url" "$savepath" "$category" "$stopped" <<'PY'
import json,sys
url,sp,cat,stopped = sys.argv[1:5]
a={'urls':[url]}
if sp: a['savepath']=sp
if cat: a['category']=cat
a['stopped']= (stopped=='true')
print(json.dumps(a, ensure_ascii=False))
PY
)" json
      exit $?
    fi

    printf '%s' "$resp" | python -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('已提交任务。'); raise SystemExit
ids = d.get('added_torrent_ids') or []
fail = d.get('failure_count') or 0
if ids:
    print('已添加 %d 个任务：%s' % (len(ids), ', '.join(ids)))
elif fail:
    print('qBittorrent 拒绝了这次添加（failure_count=%s）。' % fail)
else:
    print('已提交任务（任务可能已在队列中）。')
"
    printf '顺序下载：%s\n' "$([[ "$sequential" == "true" ]] && echo 已开启 || echo 未开启)"
    ;;
  start|stop)
    [[ $# -gt 0 ]] || die "$cmd 需要至少一个 hash"
    tool=qb_start_torrents; [[ "$cmd" == "stop" ]] && tool=qb_stop_torrents
    rpc "$tool" "$(python -c "import json,sys;print(json.dumps({'hashes':sys.argv[1:]}))" "$@")" json
    ;;
  delete)
    [[ $# -gt 0 ]] || die "delete 需要至少一个 hash"
    files="false"; hashes=()
    while (($#)); do
      case "$1" in
        --files) files="true"; shift ;;
        *) hashes+=("$1"); shift ;;
      esac
    done
    rpc qb_delete_torrents "$(python -c "import json,sys;print(json.dumps({'hashes':sys.argv[1:-1],'delete_files':sys.argv[-1]=='true'}))" "${hashes[@]}" "$files")" json
    ;;
  api)
    [[ -n "${1:-}" ]] || die "api 需要 path，例如 app/preferences"
    path="$1"; shift
    method="GET"; params=""
    while (($#)); do
      case "$1" in
        --method) method="${2:-}"; shift 2 ;;
        --params) params="${2:-}"; shift 2 ;;
        *) die "api 未知参数：$1" ;;
      esac
    done
    rpc qb_api_request "$(python - "$path" "$method" "$params" <<'PY'
import json,sys
path,method,params = sys.argv[1:4]
a={'path':path,'method':method}
if params:
    try: a['params']=json.loads(params)
    except Exception: a['params']=params
print(json.dumps(a, ensure_ascii=False))
PY
)" json
    ;;
  upload-limit)
    # 注意：MCP 屏蔽了 app/setPreferences（全局配置类端点），
    # 限速必须走 transfer/setUploadLimit，这个端点允许调用。
    if [[ "${1:-}" == "--set" ]]; then
      bytes_val="${2:-}"
      [[ -n "$bytes_val" ]] || die "--set 需要字节数，例如 131072"
      rpc qb_api_request "$(python -c "import json,sys;print(json.dumps({'method':'POST','path':'transfer/setUploadLimit','params':{'limit':int(sys.argv[1])}}))" "$bytes_val")" json >/dev/null
      printf '已设置上传限速 %s B/s\n' "$bytes_val"
    else
      lim="$(rpc qb_api_request "$(python -c "import json;print(json.dumps({'method':'GET','path':'app/preferences'}))")" pres)"
      if [[ "$lim" == "0" ]]; then
        rpc qb_api_request "$(python -c "import json;print(json.dumps({'method':'POST','path':'transfer/setUploadLimit','params':{'limit':131072}}))")" json >/dev/null
        printf '上传限速未设置，已自动设为 128 KiB/s (131072 B/s)\n'
      elif [[ "$lim" =~ ^[0-9]+$ ]]; then
        printf '当前上传限速：%s (%s B/s)\n' "$(python -c "
import sys
b=int(sys.argv[1])
print('%.2f MiB/s'%(b/1048576) if b>=1048576 else '%.0f KiB/s'%(b/1024))" "$lim")" "$lim"
      else
        printf '%s\n' "$lim"
      fi
    fi
    ;;
  tools)
    list_tools
    ;;
  raw|call)
    [[ -n "${1:-}" ]] || die "$cmd 需要工具名，例如 qb_app_info"
    rpc "$1" "${2:-{\}}" json
    ;;
  *)
    die "未知命令：$cmd"
    ;;
esac
