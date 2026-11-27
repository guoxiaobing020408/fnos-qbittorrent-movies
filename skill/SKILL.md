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

不要手写 API 请求，优先用这两个脚本。

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

## 第三步：选版本

用户没指定时，按这个优先级：

1. **做种数最多**的 1080p（下得快，画质够用）
2. 做种数相近时，选 BluRay 优于 WEB
3. 用户在意空间时降到 720p
4. 用户有 4K 设备且不在意体积才选 2160p

把对比摆成表格给用户看，例如：

```
| 版本 | 大小 | 做种 | 推荐理由 |
| 1080p WEB | 3.07 GB | 100 | 做种最多，最快 |
| 1080p BluRay | 2.71 GB | 16 | 画质略好，但慢 |
| 2160p WEB | 7.44 GB | 51 | 4K，体积大 |
```

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
