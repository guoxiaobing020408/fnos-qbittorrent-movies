#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
字幕获取 / 转换 / 安装 —— 纯 Python 标准库，无第三方依赖。

背景：字幕站（字幕库 zimuku）的详情页下载按钮走 JS 渲染，直接扒 HTML 拿不到直链。
可行路径是访问 /dld/<id> 这个「高速下载页」，里面藏着带 token 的真实下载路径
（形如 /download/<base64token>/svr/l0），拿到后直接 GET 就能下到文件。

用法：
  subtitle.py fetch <id|URL> [-o 输出.srt] [--keep-raw]
      从字幕库按条目 id 下载字幕，自动解包（zip）并转换（ASS/SSA → SRT）

  subtitle.py convert <文件> [-o 输出.srt]
      把本地 ASS/SSA 转成 SRT；本身是 SRT 则只做规范化

  subtitle.py install <字幕文件> <影片目录> [--tag cht]
      复制成与视频同名的 .srt，使播放器把它当默认轨自动加载

  subtitle.py clean [--list] [--include-legacy]
      清理本工具产生的临时文件（见下方「临时文件」）

  subtitle.py doctor
      自检：能否连通字幕站、临时目录是否干净

临时文件策略（重要）：
  - 下载内容默认只在内存里处理，不落盘；加 --keep-raw 才保存到工作目录。
  - 工作目录固定为系统临时目录下的 wb-subtitle/，可环境变量 WB_SUBTITLE_TMP 覆盖。
  - 每次 fetch / convert 结束会清掉本次产生的中间文件；
    install 成功后，如果字幕文件位于工作目录内，也一并删除。
  - 跨会话残留用 clean 一次清空（只清 wb-subtitle/ 自己的工作目录，
    绝不动系统的其他临时文件）。

示例：
  subtitle.py fetch 187214 -o Interstellar.zh.srt
  subtitle.py install Interstellar.zh.srt "\\\\<NAS_IP>\\电影\\Interstellar (2014)"
  subtitle.py clean

退出码：0 成功；1 运行失败；2 参数错误
"""

import argparse
import glob
import io
import os
import re
import shutil
import socket
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

# 本工具专用的临时工作目录。所有中间产物都落在这里，
# 便于 install / clean 一次性收干净，不污染系统临时目录。
WORK_DIR = os.environ.get("WB_SUBTITLE_TMP") or os.path.join(
    tempfile.gettempdir(), "wb-subtitle")

# 旧版本（手工临时脚本）在系统临时目录里留下的文件名模式，
# 只做精确前缀匹配，避免误删别人的文件。
LEGACY_PATTERNS = [
    "sub_svr_*.bin",
    "sub_*.bin",
    "inter.bin",
    "12.12.The.Day.zh.srt",
    "Interstellar.zh.srt",
]

# 字幕库有多个镜像域名，按顺序探测
SITES = [
    "https://zimuku.org",
    "https://srtku.com",
    "https://zimuku.pw",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

TIMEOUT = 30

# 单条字幕超过这个时长视为台标/版权注释，丢弃
MAX_CUE_MS = 20000
MIN_CUE_MS = 200


def log(msg):
    print(msg, flush=True)


def fail(msg, code=1):
    print("错误：" + msg, file=sys.stderr, flush=True)
    sys.exit(code)


def workdir(create=False):
    if create:
        os.makedirs(WORK_DIR, exist_ok=True)
    return WORK_DIR


def inside_workdir(path):
    """判断路径是否在本工具的工作目录内（用于判断能否安全删除）"""
    try:
        p = os.path.abspath(path)
        w = os.path.abspath(WORK_DIR)
        return os.path.commonpath([p, w]) == w and p != w
    except (ValueError, OSError):
        return False


def safe_remove(path):
    """只删工作目录内的文件，其他位置一律拒绝"""
    if not os.path.exists(path):
        return False
    if not inside_workdir(path):
        log("跳过（不在工作目录内，不删）：%s" % path)
        return False
    try:
        os.remove(path)
        return True
    except OSError as e:
        log("删除失败 %s：%s" % (path, e))
        return False


def tidy_workdir():
    """工作目录空了就一并移除，不留空壳"""
    if os.path.isdir(WORK_DIR) and not os.listdir(WORK_DIR):
        try:
            os.rmdir(WORK_DIR)
            return True
        except OSError:
            pass
    return False


def http_get(url, referer=None, binary=False):
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
    return raw if binary else raw.decode("utf-8", "replace")


# ---------------- 下载 ----------------

TOKEN_RE = re.compile(r"/download/[A-Za-z0-9+/=_\-]+/svr/[a-z0-9]+")


def extract_id(arg):
    """从纯数字 id 或详情页 URL 里取出条目 id"""
    s = str(arg).strip()
    if s.isdigit():
        return s
    m = re.search(r"/detail/(\d+)", s) or re.search(r"/(\d+)(?:\.html)?/?$", s)
    if m:
        return m.group(1)
    fail("无法从 %r 解析出字幕条目 id（应形如 187214 或 .../detail/187214.html）" % arg, 2)


def resolve_download_url(sub_id):
    """访问 /dld/<id>，从中提取带 token 的下载路径。返回 (base, url)"""
    last_err = None
    for base in SITES:
        try:
            html = http_get("%s/dld/%s" % (base, sub_id),
                            referer="%s/detail/%s.html" % (base, sub_id))
        except Exception as e:
            last_err = "%s: %s" % (base, e)
            continue
        paths = TOKEN_RE.findall(html)
        if not paths:
            last_err = "%s: 页面里没找到下载路径（可能需要人机验证）" % base
            continue
        # 优先 l0（线路0），其次 y0，再取第一个
        paths.sort(key=lambda p: (0 if p.endswith("/l0") else
                                  1 if p.endswith("/y0") else 2))
        return base, base + paths[0]
    fail("拿不到下载地址。最后一次失败：%s\n"
         "如果所有镜像都失败，说明该条目需要人机验证或 id 不存在。\n"
         "处理：浏览器打开 https://zimuku.org/detail/%s.html 手动下载，"
         "再用 convert / install 两个子命令。" % (last_err, sub_id))


def download(raw_url, referer):
    log("下载：%s" % raw_url[:110] + ("…" if len(raw_url) > 110 else ""))
    raw = http_get(raw_url, referer=referer, binary=True)
    log("已下载 %s KB" % (len(raw) // 1024))
    return raw


# ---------------- 解包与格式识别 ----------------

def sniff(data):
    head = data[:512]
    if head[:2] == b"PK":
        return "zip"
    if head[:4] == b"Rar!":
        return "rar"
    if head[:6].startswith(b"7z\xbc\xaf"):
        return "7z"
    text = head.decode("utf-8", "ignore")
    if "[Script Info]" in text or "[Events]" in text or "[V4+ Styles]" in text:
        return "ass"
    if "-->" in text:
        return "srt"
    if text.lstrip().lower().startswith("webvtt"):
        return "vtt"
    return "unknown"


def decode_text(data):
    """按常见编码依次尝试解码"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def unzip_subtitle(data, tmp_dir="/tmp"):
    """从 zip 里挑出最合适的字幕文件（优先中文名/简体）"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        fail("压缩包损坏，无法解包")

    names = [n for n in zf.namelist()
             if n.lower().endswith((".srt", ".ass", ".ssa"))
             and not n.startswith("__MACOSX")]
    if not names:
        fail("压缩包里没有 .srt / .ass / .ssa 文件（含：%s）"
             % ", ".join(zf.namelist()[:5]))

    def score(n):
        low = n.lower()
        s = 0
        if "chs" in low or "sc" in low or "简体" in n:
            s -= 30          # 简体优先
        if "cht" in low or "繁体" in n:
            s += 10
        if "eng" in low or "en." in low:
            s += 20
        if low.endswith(".srt"):
            s -= 5           # 同等条件 srt 更好处理
        return (s, len(n))

    names.sort(key=score)
    picked = names[0]
    log("包内字幕：%d 个，选用 %s" % (len(names), picked))
    return zf.read(picked), picked


# ---------------- 转换 ----------------

def ts_to_ms(t):
    # SRT 用逗号当毫秒分隔符（00:00:03,708），ASS 用点（0:00:03.71），两种都要认
    m = re.match(r"\s*(\d+):(\d+):(\d+)[.,](\d+)", t)
    if not m:
        return 0
    h, mi, s, frac = m.groups()
    ms = int((frac + "000")[:3])
    return ((int(h) * 60 + int(mi)) * 60 + int(s)) * 1000 + ms


def ms_to_srt(ms):
    h, ms = divmod(ms, 3600000)
    mi, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, mi, s, ms)


ASS_OVERRIDE = re.compile(r"\{[^}]*\}")


def parse_ass(text):
    """解析 ASS/SSA，返回 [(start_ms, end_ms, text)]，已去特效标记"""
    in_events = False
    fields = []
    out = []
    for line in text.splitlines():
        s = line.strip()
        low = s.lower()
        if low.startswith("[events]"):
            in_events = True
            continue
        if s.startswith("[") and not low.startswith("[events]"):
            in_events = False
            continue
        if not in_events:
            continue
        if low.startswith("format:"):
            fields = [f.strip().lower() for f in s.split(":", 1)[1].split(",")]
            continue
        if not low.startswith("dialogue:"):
            continue
        body = s.split(":", 1)[1]
        parts = body.split(",", len(fields) - 1) if fields else body.split(",", 9)
        if len(parts) < len(fields):
            continue
        rec = dict(zip(fields, parts))
        txt = ASS_OVERRIDE.sub("", rec.get("text", ""))
        txt = (txt.replace("\\N", "\n").replace("\\n", "\n")
                  .replace("\\h", " ").strip())
        if not txt:
            continue
        out.append((ts_to_ms(rec.get("start", "")),
                    ts_to_ms(rec.get("end", "")), txt))
    return out


def parse_srt(text):
    """解析 SRT，返回 [(start_ms, end_ms, text)]"""
    out = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    for blk in blocks:
        lines = [l for l in blk.split("\n") if l.strip()]
        if len(lines) < 2:
            continue
        idx = 0
        if lines[0].strip().isdigit():
            idx = 1
        if idx >= len(lines) or "-->" not in lines[idx]:
            continue
        a, _, b = lines[idx].partition("-->")
        raw_txt = "\n".join(lines[idx + 1:]).strip()
        # SRT 里也常混入 ASS 标记（{\an8} 之类），一并清掉
        raw_txt = ASS_OVERRIDE.sub("", raw_txt)
        raw_txt = (raw_txt.replace("\\N", "\n").replace("\\n", "\n")
                          .replace("\\h", " ").strip())
        if not raw_txt:
            continue
        out.append((ts_to_ms(a), ts_to_ms(b), raw_txt))
    return out


def normalize(cues):
    """过滤异常条目、按时间排序、合并同时间轴的重叠条目"""
    fixed = []
    for a, b, t in cues:
        if b <= a:
            continue
        dur = b - a
        if dur < MIN_CUE_MS or dur > MAX_CUE_MS:
            continue
        fixed.append([a, b, t])
    fixed.sort(key=lambda x: (x[0], x[1]))

    merged = []
    for a, b, t in fixed:
        if merged and abs(a - merged[-1][0]) < 50 and abs(b - merged[-1][1]) < 50:
            merged[-1][2] += "\n" + t
        else:
            merged.append([a, b, t])
    return merged


def to_srt(cues):
    chunks = []
    for i, (a, b, t) in enumerate(cues, 1):
        chunks.append("%d\n%s --> %s\n%s\n" % (i, ms_to_srt(a), ms_to_srt(b), t))
    return "\n".join(chunks)


def convert_bytes(data):
    """把任意格式的字幕数据转成 SRT 文本。返回 (srt_text, 源格式)"""
    kind = sniff(data)

    if kind == "zip":
        inner, name = unzip_subtitle(data)
        kind = sniff(inner)
        data = inner
        log("包内格式：%s" % kind)

    if kind in ("rar", "7z"):
        fail("下载到的是 %s 压缩包，Python 标准库解不了。"
             "请手动解压出 .srt/.ass 后，用 convert 子命令转换。" % kind.upper())

    text = decode_text(data)

    if kind == "ass":
        cues = parse_ass(text)
    elif kind == "srt":
        cues = parse_srt(text)
    elif kind == "vtt":
        cues = parse_srt(text)   # VTT 的时间行格式接近，够用
    else:
        # 兜底：内容里有 Dialogue 就按 ASS 处理
        if "Dialogue:" in text:
            cues = parse_ass(text)
            kind = "ass"
        else:
            fail("无法识别字幕格式，前 80 字节：%r" % data[:80])

    if not cues:
        fail("解析出 0 条字幕，源文件可能不是有效字幕")

    before = len(cues)
    cues = normalize(cues)
    log("解析 %d 条 → 有效 %d 条" % (before, len(cues)))
    if not cues:
        fail("清理后没有可用条目")

    span = cues[-1][0] - cues[0][0]
    log("时间轴覆盖：%s → %s（约 %d 分钟）"
        % (ms_to_srt(cues[0][0]), ms_to_srt(cues[-1][0]), span // 60000))
    return to_srt(cues), kind


# ---------------- 安装 ----------------

VIDEO_EXT = (".mp4", ".mkv", ".avi", ".mov", ".ts", ".m2ts", ".wmv")


def install(srt_path, movie_dir, tag=None):
    if not os.path.isfile(srt_path):
        fail("字幕文件不存在：%s" % srt_path, 2)
    if not os.path.isdir(movie_dir):
        fail("影片目录不存在或不可访问：%s\n"
             "Windows 上访问 NAS，先执行：\n"
             "  net use \"\\\\<NAS_IP>\\<共享名>\" /user:<用户名> <密码>" % movie_dir, 2)

    videos = [f for f in os.listdir(movie_dir) if f.lower().endswith(VIDEO_EXT)]
    if not videos:
        fail("目录里没有视频文件：%s" % movie_dir)
    # 有多个视频时选体积最大的
    videos.sort(key=lambda f: os.path.getsize(os.path.join(movie_dir, f)),
                reverse=True)
    video = videos[0]
    stem = os.path.splitext(video)[0]

    # 主轨：与视频同名 → 播放器默认加载
    dst = os.path.join(movie_dir, stem + ".srt")
    with open(srt_path, "rb") as f:
        payload = f.read()
    with open(dst, "wb") as f:
        f.write(payload)

    log("视频：%s" % video)
    log("已安装：%s（%d KB，播放器默认轨）" % (os.path.basename(dst), len(payload) // 1024))

    if tag:
        alt = os.path.join(movie_dir, "%s.%s.srt" % (stem, tag))
        with open(alt, "wb") as f:
            f.write(payload)
        log("副本：%s" % os.path.basename(alt))

    existing = sorted(f for f in os.listdir(movie_dir) if f.lower().endswith(".srt"))
    log("目录内字幕：%s" % " | ".join(existing))
    return dst


# ---------------- 命令 ----------------

def write_text(path, text):
    """写文本文件，父目录不存在就建"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def cmd_fetch(args):
    sub_id = extract_id(args.target)
    log("字幕条目 id：%s" % sub_id)
    base, url = resolve_download_url(sub_id)
    log("来源：%s" % base)
    raw = download(url, referer="%s/dld/%s" % (base, sub_id))

    # 不给 -o 就落到工作目录，install 之后能自动清掉
    out = args.out
    if not out:
        out = os.path.join(workdir(create=True), "sub-%s.zh.srt" % sub_id)

    raw_path = None
    if args.keep_raw:
        raw_path = os.path.join(workdir(create=True),
                                os.path.basename(os.path.splitext(out)[0]) + ".raw")
        with open(raw_path, "wb") as f:
            f.write(raw)
        log("原始文件已保留：%s" % raw_path)

    try:
        srt, kind = convert_bytes(raw)
        write_text(out, srt)
        log("已输出：%s（%d KB，源格式 %s）"
            % (out, os.path.getsize(out) // 1024, kind.upper()))
    finally:
        if not args.keep_raw and raw_path:
            safe_remove(raw_path)

    # 一条命令走完下载 → 转换 → 装成默认轨 → 清临时文件
    if args.install:
        log("")
        install(out, args.install, args.tag)
        if inside_workdir(out) and safe_remove(out):
            log("已清理临时文件：%s" % out)
        if tidy_workdir():
            log("工作目录已移除：%s" % WORK_DIR)


def cmd_convert(args):
    with open(args.input, "rb") as f:
        raw = f.read()
    srt, kind = convert_bytes(raw)
    out = args.out or (os.path.splitext(args.input)[0] + ".srt")
    write_text(out, srt)
    log("已输出：%s（源格式 %s）" % (out, kind.upper()))


def cmd_install(args):
    dst = install(args.srt, args.dir, args.tag)
    # 字幕文件如果放在工作目录里（fetch 的中间产物），装完就删
    if inside_workdir(args.srt):
        if safe_remove(args.srt):
            log("已清理临时文件：%s" % args.srt)
        tidy_workdir()
    else:
        log("源字幕不在工作目录内，保留原文件：%s" % args.srt)
    return dst


def cmd_clean(args):
    """清理本工具产生的临时文件。只碰自己的工作目录 + 旧版遗留的精确文件名。"""
    targets = []

    if os.path.isdir(WORK_DIR):
        for name in sorted(os.listdir(WORK_DIR)):
            targets.append(os.path.join(WORK_DIR, name))

    if args.include_legacy:
        tmp_root = tempfile.gettempdir()
        for pat in LEGACY_PATTERNS:
            targets.extend(sorted(glob.glob(os.path.join(tmp_root, pat))))

    # 多个模式会命中同一个文件，去重（保序）
    targets = list(dict.fromkeys(targets))

    if not targets:
        log("没有需要清理的临时文件。")
        log("工作目录：%s" % WORK_DIR)
        return

    if args.list:
        log("待清理 %d 个文件（--list 模式，未实际删除）：" % len(targets))
        total = 0
        for p in targets:
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            total += size
            log("  %-8s %s" % ("%d KB" % (size // 1024), p))
        log("合计 %d KB" % (total // 1024))
        return

    removed, freed = 0, 0
    for p in targets:
        try:
            size = os.path.getsize(p) if os.path.isfile(p) else 0
        except OSError:
            size = 0
        if os.path.isdir(p):
            try:
                shutil.rmtree(p)
                removed += 1
                log("  删除目录 %s" % p)
            except OSError as e:
                log("  删除失败 %s：%s" % (p, e))
            continue
        # 工作目录内的文件直接删；旧版遗留文件按精确路径删
        if inside_workdir(p) or args.include_legacy:
            try:
                os.remove(p)
                removed += 1
                freed += size
                log("  删除 %s" % p)
            except OSError as e:
                log("  删除失败 %s：%s" % (p, e))

    log("已清理 %d 项，释放 %d KB" % (removed, freed // 1024))

    # 工作目录空了就一并移除
    if os.path.isdir(WORK_DIR) and not os.listdir(WORK_DIR):
        try:
            os.rmdir(WORK_DIR)
            log("工作目录已移除：%s" % WORK_DIR)
        except OSError:
            pass


def cmd_doctor(args):
    log("工作目录：%s" % WORK_DIR)
    if os.path.isdir(WORK_DIR):
        files = os.listdir(WORK_DIR)
        if files:
            total = sum(os.path.getsize(os.path.join(WORK_DIR, f))
                        for f in files if os.path.isfile(os.path.join(WORK_DIR, f)))
            log("[1] 临时文件    有残留 %d 个（%d KB），跑 clean 清掉" % (len(files), total // 1024))
        else:
            log("[1] 临时文件    干净")
    else:
        log("[1] 临时文件    干净（工作目录未创建）")

    log("[2] Python      %s" % sys.version.split()[0])

    # 旧版遗留文件
    tmp_root = tempfile.gettempdir()
    legacy = []
    for pat in LEGACY_PATTERNS:
        legacy.extend(glob.glob(os.path.join(tmp_root, pat)))
    legacy = list(dict.fromkeys(legacy))
    if legacy:
        log("[3] 旧版遗留    %d 个文件，跑 clean --include-legacy" % len(legacy))
    else:
        log("[3] 旧版遗留    无")

    if not args.online:
        log("")
        log("站点连通性未检测（加 --online 才联网，避免无谓请求）。")
        log("结论：本地环境正常。")
        return

    # 只做 TCP 连通性判断：根路径常返回 404/403，不能用 HTTP 状态当判据
    log("[4] 字幕站      检测中…")
    reachable = []
    for base in SITES:
        host = base.split("//", 1)[-1].strip("/")
        try:
            with socket.create_connection((host, 443), timeout=8):
                reachable.append(base)
                log("     %s  可连接" % base)
        except OSError as e:
            log("     %s  不可达（%s）" % (base, e.__class__.__name__))

    log("")
    if reachable:
        log("结论：可以用 fetch 下载字幕（优先 %s）。" % reachable[0])
    else:
        log("结论：所有镜像都连不上，只能手工下载后用 convert / install。")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    p = argparse.ArgumentParser(
        prog="subtitle.py",
        description="字幕获取 / 转换 / 安装（纯标准库实现）")
    sub = p.add_subparsers(dest="cmd")

    f = sub.add_parser("fetch", help="从字幕库按条目 id 下载并转换为 SRT")
    f.add_argument("target", help="字幕条目 id（如 187214）或详情页 URL")
    f.add_argument("-o", "--out", help="输出文件；不填则落到工作目录，装完自动清理")
    f.add_argument("--install", metavar="影片目录",
                   help="下载后直接装成默认轨（一条命令走完全流程）")
    f.add_argument("--tag", help="配合 --install，额外留一份带后缀的副本，如 cht")
    f.add_argument("--keep-raw", action="store_true", help="保留原始下载文件")
    f.set_defaults(func=cmd_fetch)

    c = sub.add_parser("convert", help="把本地 ASS/SSA/SRT 转成规范 SRT")
    c.add_argument("input", help="输入文件")
    c.add_argument("-o", "--out", help="输出文件，默认同名 .srt")
    c.set_defaults(func=cmd_convert)

    i = sub.add_parser("install", help="复制字幕到影片目录并设为默认轨")
    i.add_argument("srt", help="字幕文件路径")
    i.add_argument("dir", help="影片目录（支持 UNC 路径）")
    i.add_argument("--tag", help="额外复制一份带后缀的副本，如 cht")
    i.set_defaults(func=cmd_install)

    cl = sub.add_parser("clean", help="清理本工具产生的临时文件")
    cl.add_argument("--list", action="store_true", help="只列出，不删除")
    cl.add_argument("--include-legacy", action="store_true",
                    help="同时清理旧版脚本遗留在系统临时目录里的文件（按精确文件名匹配）")
    cl.set_defaults(func=cmd_clean)

    d = sub.add_parser("doctor", help="自检：临时文件残留、站点连通性")
    d.add_argument("--online", action="store_true", help="联网检测字幕站（默认不联网）")
    d.set_defaults(func=cmd_doctor)

    args = p.parse_args()
    if not getattr(args, "func", None):
        p.print_help()
        sys.exit(2)
    args.func(args)


if __name__ == "__main__":
    main()
