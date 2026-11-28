---
name: nas-movie-search
description: 当用户要求搜索电影、按分辨率选片、把资源加到 NAS 上的下载器（qBittorrent）时使用。本技能用 `scripts/yts-api.sh` 查 YTS 资源（片名/分辨率/做种数/magnet），再用 `scripts/qb-mcp.sh` 经局域网 MCP 控制 qBittorrent（列表/详情/添加/暂停/删除/改保存位置/一键自检），加片时落到事先授权好的电影目录。全程中文，失败给中文原因和下一步建议。
---

# NAS 电影搜索与下载

## 目标

用户说"下载 XXX"时，用中文完成这个流程：

1. 搜 YTS，拿到候选（片名/年份/评分/分辨率/做种数/magnet）。
2. 只列有意义的 3~5 个版本，说明推荐哪个、为什么。
3. 加任务时**必须显式带保存路径**。
4. 加完报出 hash 和落盘位置。
5. 全程中文，失败说明原因和下一步。

## 可用脚本

- `./scripts/yts-api.sh`：搜片，输出 `.torrent` 和 `magnet`。
- `./scripts/qb-mcp.sh`：控制 qBittorrent（自检/列表/详情/添加/启停/删除/透传）。
- `./scripts/subtitle.sh`：字幕下载 / 转换 / 装成默认轨 / 清理临时文件。

不要手写 API 请求，优先用这些脚本。

## 第一步：先自检

用户问"能不能用"，或者一次新会话开始时，先跑：

```bash
./scripts/qb-mcp.sh doctor
```

六项输出 OK / FAIL：配置文件、API Key、MCP 服务、qBittorrent 状态、磁盘与队列、YTS 接口。

全绿再往下走；有 FAIL 就按提示排查，**不要靠猜**。

## 第二步：搜片

```bash
./scripts/yts-api.sh search "<关键词>" --limit 10
```

指定分辨率加 `--quality 1080p`。可用值：`480p`、`720p`、`1080p`、`1080p.x265`、`2160p`、`3D`。

**搜不到就换名字**，这是最常见的坑：

| 情况 | 换什么 |
| --- | --- |
| 中文片名 | 换英文名，YTS 是英文站 |
| 英文名搜不到 | 换**原产地语言名的罗马音**（韩国片尤其如此） |
| 都不行 | 用 IMDb ID 直查：`details --imdb-id tt1234567` |

实例：韩国片《首尔之春》搜 `Seoul Spring` 无结果，搜 `Seoul-ui Bom` 才有；《辩护人》搜 `The Attorney` 出来的是另一部同名片（6.1 分），搜 `Byeon-ho-in` 才是正确的那部（2013，tt3404140）。**同名不同片很常见，加片前核对 IMDb ID。**

拿完整磁力：

```bash
./scripts/yts-api.sh details --imdb-id tt1234567
```

## 内容范围（先判断，别白搜）

| 类型 | 能否用本技能 | 说明 |
| --- | --- | --- |
| 电影 | ✅ | YTS 覆盖院线片和流媒体电影 |
| **综艺 / 剧集 / 动漫** | ❌ | YTS **只有电影**，综艺和剧集一律搜不到，不要反复换关键词试 |
| 国内平台独播内容 | ❌ | 芒果TV、爱奇艺自制综艺等属平台付费版权内容，不走 BT 渠道 |

用户要的是综艺或剧集时，**直接说明渠道不匹配**，不要先搜一堆关键词再回一句"搜不到"。
建议用户用平台客户端离线缓存，或自备磁力/种子由本技能负责加进下载器。

## 第三步：选版本（字幕优先！）

**选片第一眼看字幕，不是看画质。** 下完没字幕比画质差难受得多。

### YTS 的字幕规律（实测）

| 版本类型 | 字幕情况 |
| --- | --- |
| **WEBRip / WEB** | 通常带 `Subs/` 目录，内含 `Simplified.chi.srt`、`Traditional.chi.srt`、英文等多语言 |
| **BluRay** | 通常只有一个和视频同名的 `.srt`（英文），**或者干脆没有** |

实测样本：WEB 版《可能的爱》33 个字幕（含简繁中文）、《西线无战事》43 个；而 BluRay 版《首尔之春》《Interstellar》《辩护人》**一个字幕文件都没有**。

### 选版顺序

1. **优先带 `Subs/` 中文的版本**（一般是 WEB 版）
2. 同分辨率下，做种多的优先
3. 用户在意空间才降到 720p
4. 只有用户明确要画质时才选 BluRay / 2160p，并提前告知"可能没字幕"

### 怎么确认带不带中文字幕

加任务后用 `qb_torrent_detail` 查 `files`：

```bash
./scripts/qb-mcp.sh raw qb_torrent_detail '{"hash":"<hash>"}' | grep -i "subs\|chi\|chs\|zh"
```

看到 `Simplified.chi.srt` 就稳了。没有就主动提醒用户，别等下完才发现。

### 下完发现没字幕

按下表处理，**优先走第三条（脚本自动装）**：

| 情况 | 处理 |
| --- | --- |
| 有 `Subs/Simplified.chi.srt` 但播放器没显示 | 复制成与视频同名 `.srt`，见「第六步」 |
| 完全没字幕文件 | `subtitle.sh fetch` 下载后 `install` |
| 只有英文 `.srt` | 补一份中文，英文改名 `.en.srt` 留备选 |

## 第六步：补字幕（缺字幕 / 字幕不显示）

工具：`./scripts/subtitle.sh`（纯 Python 标准库，不需要装 kp 依赖、不需要 jq）

### 三种场景

```bash
# 1. 有 Subs/ 目录但播放器不自动加载 → 复制成同名默认轨
./scripts/subtitle.sh install "<影片目录>/Subs/Simplified.chi.srt" "<影片目录>"

# 2. 完全没字幕 → 从字幕库按条目 id 下载（自动解包 + 转 SRT）
./scripts/subtitle.sh fetch 187214 -o "<工作目录>/movie.zh.srt"

# 3. 装成默认轨，同时留一份繁体备选
./scripts/subtitle.sh install "<工作目录>/movie.zh.srt" "<影片目录>" --tag cht
```

`install` 的判定逻辑：取目录里**体积最大**的视频文件，去掉扩展名生成 `同名.srt`。
播放器按文件名匹配字幕，这就是"默认轨"的原理。

**跨目录写文件一律走这个脚本**，不要手写 `cp` / `Move-Item`。

### 字幕库的下载链路（别重新摸索）

详情页的下载按钮是 JS 渲染，扒 HTML 拿不到直链。**能走通的只有这一条**：

```
/dld/<id> 高速下载页   →   页面里藏着 /download/<token>/svr/l0
         ↓ 直接 GET（带 Referer）
    裸 ASS / SRT，或 zip 包
         ↓ subtitle.py
   解包 → 去特效标记 → 按时间重排 → 丢弃超长注释 → 输出 SRT
```

不知道 id 就用搜索引擎定位 `zimuku.org/detail/<id>.html`，整个 URL 传给 `fetch` 也行。

### 转换必须做的四件事（都是踩过的坑）

| 处理 | 不做的后果 |
| --- | --- |
| 去 `{...}` 特效标记 | `\pos` `\fad` `\i1` 留在 SRT 里显示成乱码 |
| `\N` `\n` → 真换行，`\h` → 空格 | 整句挤成一行 |
| **按起始时间全量重排** | ASS 的 Dialogue 不保证有序，片尾字幕常排在第 2 条 |
| 丢弃单条 > 20 秒的条目 | 台标、版权声明、片名卡糊住整段画面 |

脚本已内置这四步，不要绕过它自己拼 SRT。

### 临时文件：必须清

**硬要求，不是可选项。** 转换过程会产生中间文件，不清理会一直堆在系统临时目录。

| 规则 | 说明 |
| --- | --- |
| 中间文件只在工作目录 | 固定为系统临时目录下 `wb-subtitle/`，可用 `WB_SUBTITLE_TMP` 覆盖 |
| 默认不落盘 | 下载内容只在内存处理；`--keep-raw` 才保存，且存到工作目录 |
| install 后自动删源 | 源字幕若在工作目录内，装完立即删除 |
| 跨会话残留手动清 | 见下 |

```bash
./scripts/subtitle.sh clean --list              # 先看有什么，不动手
./scripts/subtitle.sh clean                     # 清工作目录
./scripts/subtitle.sh clean --include-legacy    # 顺带清旧版手工脚本留下的文件
./scripts/subtitle.sh doctor                    # 自检：临时文件是否干净
```

`clean` 的安全边界：**只删 `wb-subtitle/` 自己的工作目录**，以及 `--include-legacy`
时按**精确文件名**匹配的旧版遗留文件。不做通配删除，不碰系统其他临时文件。

任务收尾（片子下完、字幕装好）跑一次 `clean`，这是一条固定动作。

## 第四步：加任务

```bash
./scripts/qb-mcp.sh add '<MAGNET>' '<保存路径>'
```

**保存路径必须带**。不带会落到 qBittorrent 的默认目录，那个目录往往没有授权给应用，任务立刻报 `Permission denied`。

可选参数：

| 参数 | 作用 |
| --- | --- |
| `--stopped` | 只占位不下载 |
| `--no-sequential` | 不开顺序下载（默认开启） |
| `--category` | 指定分类 |

脚本会打印新任务的 hash，方便后续跟踪。

### 关于顺序下载

默认开启（边下边看方便），但**种源差时会明显拖慢整体速度**——按顺序取块，可用的 peer 少时效率低。

实测：某片开顺序下载 0.08 MiB/s，关掉后涨到 0.26 MiB/s。

判断规则：
- 做种 50+ 的热种，开不开都行
- 做种 < 10 的冷种，**建议 `--no-sequential`**，先下完再看

## 第五步：看进度

```bash
./scripts/qb-mcp.sh list downloading
./scripts/qb-mcp.sh detail <hash>
./scripts/qb-mcp.sh status
```

用中文概括：片名、进度、速度、做种数、剩余时间。

**看全局速度决定要不要干预**：

| 全局速度 | 判断 |
| --- | --- |
| 接近带宽上限 | 带宽跑满了，别瞎暂停，慢的那个是种源问题 |
| 远低于带宽上限 | 可能是某个任务在拖，检查是否顺序下载、种源是否枯竭 |

## 移动已完成的任务

```bash
./scripts/qb-mcp.sh api torrents/setLocation --method POST \
  --params '{"hashes":"<hash>","location":"<新目录>"}'
```

**前提：新目录必须已经授权给应用**，否则返回成功但文件不动或后续报错。旧位置的文件会自动清空。

## 失败处理

### MCP 层

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 401 | 密钥失效 | 检查 mcp.json 的 Authorization，或环境变量 `QBIT_MCP_KEY` |
| 连不上 / 超时 | 服务没起或不在同一网络 | 确认 NAS 开机、MCP 容器在跑 |
| 404 | 路径不对 | 端点必须是 `POST /mcp`，端口通常 8081 |
| 405 | 用了 GET | 这个服务只接受 POST |
| 409 | 任务已在队列里 | 不是故障，别重复添加 |
| 「端点被禁止透传」 | 端点在 MCP 黑名单（如 `app/setPreferences`） | 让用户在 WebUI 手动改，或改用别的方式 |

### 写入失败

日志里出现这行：

```
file_open (/volN/xxx/yyy.mp4) error: Permission denied
```

**几乎一定是飞牛授权问题**，见 [docs/02-飞牛授权.md](../docs/02-飞牛授权.md)。核心三条：

1. 飞牛应用授权**不继承子目录**，每个目标目录单独加
2. `--perm` 传 **6** 不传 7
3. 确认改的是正确的那个 qBittorrent 实例

### 下载慢

按顺序排查：

1. 看做种数。页面标的种子数 ≠ 实际能连上的，后者才是真的
2. 冷种关掉顺序下载
3. 补公共 tracker：

```bash
./scripts/qb-mcp.sh api torrents/addTrackers --method POST \
  --params '{"hash":"<hash>","urls":"udp://tracker.opentrackr.org:1337/announce\nudp://exodus.desync.com:6969/announce"}'
```

4. 还慢就是种源枯竭，换版本或换片源

### 搜片失败

- 无结果：换语言/罗马音/IMDb ID（见上）
- HTTP 4xx/5xx：YTS 接口问题，稍后重试
- 网络错误：检查 DNS 和代理

## 回答风格

- 始终中文，先给结论再解释。
- 搜索结果是结构化信息，**用表格**。
- 不要一次列十几个版本，挑 3~5 个有意义的。
- 失败时说清"哪一步、什么原因、下一步怎么办"，不要只丢错误码。
- 不要把长堆栈或 HTML 错误页贴给用户，摘关键行。
