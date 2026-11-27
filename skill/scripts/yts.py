#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YTS 电影搜索 —— 标准库实现，无第三方依赖。

只依赖 Python 3.8+ 的 urllib / json，不需要 curl、不需要 jq。

设计约定：
  - 命令：search / list / details / suggestions
  - 输出：默认人类可读摘要；--full 格式化 JSON；--raw 原始响应
  - 错误：一律写到 stderr，退出码非 0
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://movies-api.accel.li/api/v2"

# 生成 magnet 时附带的公共 tracker
TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://tracker.bittor.pw:1337/announce",
)

QUALITIES = ("480p", "720p", "1080p", "1080p.x265", "2160p", "3D")
SORTABLE = ("title", "year", "rating", "peers", "seeds",
            "download_count", "like_count", "date_added")


def build_magnet(torrent_hash, display_name):
    """用 info hash 和片名拼一个带公共 tracker 的 magnet。"""
    if not torrent_hash:
        return "-"
    name = urllib.parse.quote(display_name or "YTS Movie", safe="")
    parts = ["magnet:?xt=urn:btih:" + torrent_hash, "dn=" + name]
    parts += ["tr=" + t for t in TRACKERS]
    return "&".join(parts)


def fetch(base, endpoint, params, resp_format, timeout, retries):
    """请求 API，返回 (状态码, 文本)。失败抛 RuntimeError。"""
    url = "{}/{}.{}".format(base.rstrip("/"), endpoint, resp_format)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        "User-Agent": "yts-api/2.0 (+python-stdlib)",
        "Accept": "application/json, text/xml, */*",
    })

    last_error = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            # 4xx 不重试，直接报
            body = exc.read().decode("utf-8", "replace")[:200]
            raise RuntimeError("HTTP {}: {}".format(exc.code, body))
        except urllib.error.URLError as exc:
            last_error = exc.reason
        except TimeoutError:
            last_error = "timeout"
        if attempt < retries:
            import time
            time.sleep(2)

    raise RuntimeError("connection failed: {}".format(last_error))


def format_movie_block(movie):
    """把一部电影格式化成多行摘要。"""
    lines = [
        "ID: {}".format(movie.get("id", "-")),
        "Title: {}".format(movie.get("title_long") or movie.get("title") or ""),
        "IMDb: {}".format(movie.get("imdb_code") or "-"),
        "Rating: {}".format(movie.get("rating") or "-"),
        "URL: {}".format(movie.get("url") or "-"),
        "Torrents:",
    ]

    torrents = movie.get("torrents") or []
    if not torrents:
        lines.append("No torrents found.")
        return "\n".join(lines)

    display = movie.get("title_long") or movie.get("title") or "YTS Movie"
    for t in torrents:
        lines.append(
            "  Quality: {} | Type: {} | Codec: {} | Size: {} | Seeds: {} | Peers: {}".format(
                t.get("quality") or "?",
                t.get("type") or "-",
                t.get("video_codec") or "-",
                t.get("size") or "-",
                t.get("seeds") or 0,
                t.get("peers") or 0,
            )
        )
        lines.append("  Torrent: {}".format(t.get("url") or "-"))
        lines.append("  Magnet: {}".format(build_magnet(t.get("hash"), display)))
    return "\n".join(lines)


def dedupe(movies):
    """按 id / imdb_code / title_long 去重，保持原顺序。"""
    seen = set()
    result = []
    for m in movies:
        key = (m.get("id"), m.get("imdb_code"), m.get("title_long"))
        if key in seen:
            continue
        seen.add(key)
        result.append(m)
    return result


def emit_summary(command, payload):
    """按命令输出摘要或错误。"""
    status = payload.get("status")
    if status != "ok":
        print("ERROR: {}".format(
            payload.get("status_message") or status or "request failed"))
        return 1

    data = payload.get("data") or {}

    if command in ("list", "suggestions", "search"):
        movies = dedupe(data.get("movies") or [])
        if not movies:
            print("No movies found.")
            return 0
        for movie in movies:
            print(format_movie_block(movie))
            print()
        return 0

    if command == "details":
        movie = data.get("movie")
        if not movie:
            print("No movie found.")
            return 0
        print(format_movie_block(movie))
        return 0

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="yts-api",
        description="YTS 电影搜索：查片、看分辨率、取 torrent 和 magnet",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  ./yts-api.sh search "the matrix"
  ./yts-api.sh search "the matrix" --quality 1080p --limit 5
  ./yts-api.sh details --imdb-id tt0133093
  ./yts-api.sh list --genre action --minimum-rating 7 --sort-by rating
  ./yts-api.sh search "alien" --full
  ./yts-api.sh search "alien" --raw

环境变量：
  YTS_API_BASE_URL  覆盖 API 地址（默认 %s）
  YTS_API_TIMEOUT   单次请求超时秒数（默认 30）
  YTS_API_RETRIES   失败重试次数（默认 2）
""" % DEFAULT_BASE,
    )
    p.add_argument("command",
                   choices=["search", "list", "details", "suggestions"],
                   help="search=搜片，list=按条件列片，details=单片详情，suggestions=相关推荐")
    p.add_argument("query", nargs="?", default=None, help="search 的关键词")

    p.add_argument("--query", "--query-term", dest="query_term",
                   help="搜索关键词（等同位置参数）")
    p.add_argument("--limit", type=int, help="返回数量 1-50，默认 10")
    p.add_argument("--page", type=int, help="页码")
    p.add_argument("--quality", choices=QUALITIES, help="分辨率筛选")
    p.add_argument("--minimum-rating", type=float, help="最低 IMDb 评分 0-9")
    p.add_argument("--genre", help="类型，如 action / comedy / drama")
    p.add_argument("--sort-by", choices=SORTABLE, help="排序字段")
    p.add_argument("--order-by", choices=["asc", "desc"], help="升序或降序")
    p.add_argument("--with-rt-ratings", choices=["true", "false"],
                   help="是否包含烂番茄评分")
    p.add_argument("--movie-id", type=int, help="YTS 电影 ID")
    p.add_argument("--imdb-id", help="IMDb ID，如 tt0133093")

    p.add_argument("--full", action="store_true", help="输出格式化 JSON")
    p.add_argument("--raw", action="store_true", help="输出 API 原始响应")
    p.add_argument("--format", default="json", choices=["json", "xml", "jsonp"],
                   help="响应格式，默认 json")
    return p


def main(argv):
    if not argv:
        build_parser().print_help()
        return 0

    args = build_parser().parse_args(argv)

    # 命令校验
    if args.command == "details" and not (args.movie_id or args.imdb_id):
        print("Error: details 需要 --movie-id 或 --imdb-id", file=sys.stderr)
        return 2
    if args.command == "suggestions" and not args.movie_id:
        print("Error: suggestions 需要 --movie-id", file=sys.stderr)
        return 2
    if args.command == "search" and not (args.query or args.query_term):
        print("Error: search 需要关键词", file=sys.stderr)
        return 2

    # 组装请求参数
    params = {}
    q = args.query_term or args.query
    if q:
        params["query_term"] = q

    endpoint_map = {
        "search": "list_movies",
        "list": "list_movies",
        "details": "movie_details",
        "suggestions": "movie_suggestions",
    }
    endpoint = endpoint_map[args.command]

    if args.command in ("search", "list"):
        params["limit"] = args.limit or 10
    elif args.limit:
        params["limit"] = args.limit

    for key, value in (
        ("page", args.page),
        ("quality", args.quality),
        ("minimum_rating", args.minimum_rating),
        ("genre", args.genre),
        ("sort_by", args.sort_by),
        ("order_by", args.order_by),
        ("with_rt_ratings", args.with_rt_ratings),
        ("movie_id", args.movie_id),
        ("imdb_id", args.imdb_id),
    ):
        if value is not None:
            params[key] = value

    import os
    base = os.environ.get("YTS_API_BASE_URL", DEFAULT_BASE)
    timeout = float(os.environ.get("YTS_API_TIMEOUT", "30"))
    retries = int(os.environ.get("YTS_API_RETRIES", "2"))

    try:
        _, body = fetch(base, endpoint, params, args.format, timeout, retries)
    except RuntimeError as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1

    # 原始输出
    if args.raw:
        print(body)
        return 0

    # 非 JSON 格式或 --full，直接打印
    if args.format != "json":
        print(body)
        return 0

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        print("Error: 响应不是合法 JSON（{}）".format(exc), file=sys.stderr)
        print(body[:300], file=sys.stderr)
        return 1

    if args.full:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    return emit_summary(args.command, payload)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
