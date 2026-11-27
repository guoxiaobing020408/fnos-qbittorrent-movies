# fnos-qbittorrent-movies

在**飞牛 fnOS** 上用 qBittorrent 自动搜片、下片的完整方案：一句话搜电影 → 按分辨率选版本 → 直接下到指定硬盘 → 文件在飞牛「我的文件」里就能看到。

配套一个 AI Agent 技能（Claude Code / WorkBuddy 等），所以说人话就是：**跟 AI 说"下个首尔之春"，它自己去搜、去下。**

---

## 它解决什么问题

自己搭 NAS 下电影，通常会卡在这几步：

| 痛点 | 本方案怎么解 |
| --- | --- |
| 找片要开浏览器翻资源站 | 脚本直连 YTS API，一行命令出候选（分辨率/做种数/磁力） |
| 下载器在 NAS 上，网页点来点去 | 走 qBittorrent MCP，命令行/对话式控制 |
| **下完了在文件管理器里找不到文件** | 见下方「核心坑」，这是本仓库最值钱的部分 |
| **任务加进去就报错 `Permission denied`** | 飞牛应用授权机制，必须按特定方式配置 |

---

## 核心坑（踩了两小时才齐）

这三条是全网搜不到的，遇到了基本没有文档可查：

### 1. 飞牛的应用授权**不继承子目录**

给应用授权 `/vol3/1000` 之后，写入 `/vol3/1000/电影/xxx.mp4` 依然 `Permission denied`。

**每个最终目标目录都必须单独加一条授权**，授权父目录没用。

```bash
# 不行：只授权父目录
fnos-cli app config set-authorization-path qbittorrent "/vol3/1000" --perm 6 --yes

# 必须：把真正要写的目录也加上
fnos-cli app config set-authorization-path qbittorrent "/vol3/1000/电影" --perm 6 --yes
```

### 2. `--perm` 要传 **6**，不能传 7

传 7 会在飞牛 Web UI 里显示成一个裸数字「7」（而不是「读写」），这种条目**ACL 实际不下发**，写入照样被拒。

必须传 6，UI 才会正常显示「读写」，权限才真正生效。

### 3. 飞牛可能同时装着两个 qBittorrent

应用中心版和手动安装版可以共存，各自占不同端口。**改设置前先确认连的是哪一个**，否则改了半天没效果。

判断方法：查 `app/preferences` 的 `web_ui_port`，和你打开的那个界面端口对比。

---

## 架构

```
┌─────────────────┐     HTTP/JSON-RPC      ┌──────────────────────┐
│  AI Agent / CLI │ ─────────────────────► │ qbittorrent-mcp      │
│  (qb-mcp.sh)    │   POST /mcp  Bearer    │ (Docker / 独立进程)   │
└─────────────────┘                         └──────────┬───────────┘
                                                     │ WebUI API
                                                     ▼
┌─────────────────┐                         ┌──────────────────────┐
│  YTS API        │ ◄── 搜片 ── qb-mcp.sh ──│ qBittorrent 5.x      │
│  (yts-api.sh)   │                         │ 飞牛 fnOS 原生应用    │
└─────────────────┘                         └──────────┬───────────┘
                                                     │ 写文件
                                                     ▼
                                            ┌──────────────────────┐
                                            │ /volN/<uid>/电影/     │
                                            │ (飞牛「我的文件」可见) │
                                            └──────────────────────┘
```

关键点：**MCP 服务是个独立的小服务**，跑在 qBittorrent 旁边，用 Bearer key 鉴权。Agent 不需要知道 qBittorrent 的 WebUI 密码。

---

## 快速开始

### 前置条件

- 飞牛 fnOS（或其他 NAS，qBittorrent 能跑就行）
- qBittorrent 5.x
- 一个 `qbittorrent-mcp` 服务（见 [docs/01-部署.md](docs/01-部署.md)）
- bash + curl + python3（脚本只用标准库）

### 配置

在 `~/.workbuddy/mcp.json`（或任何 Agent 的 MCP 配置）里登记服务：

```json
{
  "mcpServers": {
    "qbittorrent": {
      "type": "http",
      "url": "http://<NAS_IP>:8081/mcp",
      "headers": {
        "Authorization": "Bearer <你的_MCP_API_KEY>"
      }
    }
  }
}
```

脚本会自动从这里读地址和密钥，不用手工传。

也可以用环境变量临时覆盖：

```bash
export QBIT_MCP_URL='http://192.168.1.100:8081/mcp'
export QBIT_MCP_KEY='<key>'
```

### 用

```bash
cd skill

# 1. 先自检，六项全绿再动手
./scripts/qb-mcp.sh doctor

# 2. 搜片
./scripts/yts-api.sh search "Seoul Spring"
./scripts/yts-api.sh details --imdb-id tt22507524

# 3. 加任务（一定要带保存路径，见下面注意）
./scripts/qb-mcp.sh add '<MAGNET>' '/vol3/1000/电影'

# 4. 看进度
./scripts/qb-mcp.sh list downloading
```

> ⚠️ **一定要带保存路径**。不带的话会落到 qBittorrent 的默认目录，那个目录往往没授权，任务直接报 `Permission denied`。

---

## 脚本能力

`qb-mcp.sh` 把 MCP 的 9 个工具包成了常用命令：

| 命令 | 作用 |
| --- | --- |
| `doctor` | 一键自检：配置 / 密钥 / MCP / qBittorrent / 磁盘 / YTS 六项 |
| `info` / `status` | 版本、全局速率和连接状态 |
| `list [filter]` | 任务列表（all/downloading/completed/...） |
| `detail <hash>` | 单任务详情 + 文件列表 |
| `add <magnet\|url> [路径]` | 加任务，默认开顺序下载 |
| `start` / `stop` / `delete` | 启停与删除（`--files` 连文件一起删） |
| `upload-limit` | 查/设全局上传限速 |
| `tools` / `call <tool> '<json>'` | 列工具、调任意工具 |
| `api <path>` | 原生 qBittorrent API 透传 |

详细用法见 [skill/SKILL.md](skill/SKILL.md)。

---

## 已知限制

| 限制 | 原因 | 绕过方式 |
| --- | --- | --- |
| 改不了全局默认保存路径 | MCP 硬禁 `app/setPreferences`（防止 AI 把访问能力锁死） | 用户在 qBittorrent WebUI 手动改一次；日常加片带 `savepath` |
| 老片/冷门片很慢 | YTS 种源稀薄，页面标 10 个种子实际只连得上 1~2 个 | 补公共 tracker；或改用国内下载器 |
| 只有 YTS 一个片源 | YTS 是英文站，非英语片覆盖有限 | 自行扩展 `yts-api.sh` 的 `YTS_API_BASE_URL` |

---

## 目录结构

```
.
├── README.md
├── skill/
│   ├── SKILL.md              # Agent 技能说明（触发条件、流程、坑）
│   ├── agents/openai.yaml    # 技能元数据
│   └── scripts/
│       ├── qb-mcp.sh         # MCP 客户端（自检/列表/添加/删除/透传）
│       └── yts-api.sh        # YTS 搜索
└── docs/
    ├── 01-部署.md            # 从零把 MCP 服务跑起来
    ├── 02-飞牛授权.md         # 应用目录授权（本仓库核心）
    └── 03-踩坑记录.md         # 全部踩过的坑
```

---

## 上游项目与引用说明

**先说清楚哪些代码不是我的。**

### 1. `lazycat-movie-search-skill` —— `yts-api.sh` 的出处

[whoamihappyhacking/lazycat-movie-search-skill](https://github.com/whoamihappyhacking/lazycat-movie-search-skill)（37 ★）

本仓库的 `skill/scripts/yts-api.sh` **衍生自该项目**，实测对比仅 7 行差异（我增加了 `--connect-timeout` / `--max-time` / `--retry` 三个参数），其余逻辑、参数解析、jq 输出格式均来自上游。

`skill/SKILL.md` 的章节结构也参考了上游，但正文按实际踩坑重写了（上游讲的是 VueTorrent `.lzcx` 通道，本项目讲 qBittorrent MCP）。

**⚠️ 该上游项目没有 LICENSE 文件。**

GitHub 上无许可证的公开仓库，默认适用「保留所有权利」，严格来说他人**不能直接复制、修改、再分发**。本仓库作为公开项目包含这段衍生代码，存在授权瑕疵。

处理建议（按稳妥程度排序）：

1. 向原作者提 Issue 请其补充许可证（MIT / Apache-2.0 都行），拿到许可后本仓库直接标注即可
2. 若不打算联系作者，把 `skill/scripts/yts-api.sh` 从本仓库移除，文档里改成"请从上游项目获取此脚本"——那样本仓库只剩原创部分，合规无虞
3. 只自用不分发（保持仓库私有）风险最低

**当前状态：已标注来源，等待作者补充许可证，或按你的判断移除该文件。**

### 2. VueTorrent —— GPL-3.0

[VueTorrent/VueTorrent](https://github.com/VueTorrent/VueTorrent)（7.1k ★，**GPL-3.0**）

qBittorrent 的 WebUI 皮肤。本仓库：

- **不分发它的任何代码**
- 仅在文档中说明它是可选界面，以及它的 API 行为（登录、`/api/v2/*`）对本项目脚本的影响
- 如果你把本仓库与 VueTorrent 一起分发，需遵守 GPL-3.0

> 注：GPL-3.0 有传染性。但只要只是「调用它的 HTTP API」，不作代码级链接或修改分发，就不构成衍生作品。本项目属于后者。

### 3. 其余第三方组件

| 组件 | 许可证 | 在本方案里的角色 |
| --- | --- | --- |
| **qBittorrent** | GPL-2.0 | 下载器本体 |
| **`qbittorrent-mcp` 服务** | 以原项目为准 | MCP 服务端，**本仓库不含其源码**，只当客户端 |
| **YTS / YTS API** | 站点服务条款 | 片源元数据，只调公开 API，不镜像不分发影视资源 |
| **Model Context Protocol** | Anthropic 开源规范 | 通信协议 |
| **fnos-cli** | 以原项目为准 | 操作飞牛应用授权（可选，也可用飞牛 Web UI） |

### 4. 本仓库原创的部分

| 文件 | 说明 |
| --- | --- |
| `skill/scripts/qb-mcp.sh` | MCP 客户端封装，原创。含自检、列表、添加、移动、API 透传，以及全部 14 条坑对应的处理逻辑 |
| `docs/*.md` | 全部原创，来自实测 |
| `README.md`、`LICENSE`、`.gitattributes` | 原创 |

**不声称原创：** 上述所有第三方组件、API、协议、以及 `yts-api.sh` 的上游实现。

> 代码里的 `qbittorrent-mcp 1.0.0`、`v5.3.1` 等版本号，是实测环境里跑着的版本，仅作兼容性参考，不代表本仓库维护它们。

---

## License

本仓库原创代码与文档采用 MIT，详见 [LICENSE](LICENSE)。

第三方组件（qBittorrent、YTS、MCP 规范、VueTorrent 等）各自遵循其原有许可证，MIT 不覆盖它们。
