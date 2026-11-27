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
- 一个**带内置 MCP 服务**的 qBittorrent 应用（在应用设置里勾选「启用 MCP 服务」，见 [docs/01-部署.md](docs/01-部署.md)）
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

## 参考的两个项目

这个方案只参考了两个项目，其余都是运行环境里现成的东西。

| 项目 | 许可证 | 参考了什么 |
| --- | --- | --- |
| [whoamihappyhacking/lazycat-movie-search-skill](https://github.com/whoamihappyhacking/lazycat-movie-search-skill) | **无（见下）** | `yts-api.sh` 的原始实现（我只加了超时重试，diff 7 行）；`SKILL.md` 的章节结构 |
| [sushazhi/fnos-qbittorrent](https://github.com/sushazhi/fnos-qbittorrent) | GPL-2.0 | **本项目对接的 MCP 服务就是它内置的**，不是我写的 |

### ⚠️ 第一个项目没有 LICENSE

GitHub 上无许可证 = 保留所有权利，严格说不能复制再分发。`yts-api.sh` 衍生自它，所以本仓库在这一点上有授权瑕疵。

三条路：

1. 找作者补许可证（MIT / Apache-2.0 都行）—— 最正当
2. 把 `yts-api.sh` 从仓库删掉，文档改成"请从上游获取"—— 只剩原创，合规无虞
3. 仓库转私有，只自用

**当前状态**：已标注来源，等作者补充许可，或按你的判断移除该文件。

### MCP 的来源（容易搞混）

MCP 不在 VueTorrent 里，也不在 qBittorrent 官方代码里。它出自上面第二个项目——那个飞牛 fpk 包把三样东西打在一起：

| 打包内容 | 位置 | 作用 |
| --- | --- | --- |
| qBittorrent 5.3.1 本体 | `bin/` | 下载器 |
| VueTorrent 皮肤（GPL-3.0，纯前端） | `ui/vuetorrent/` | 给人看的界面（8080） |
| **MCP Server** | 应用自带进程 | 给 AI 用的接口（8081） |

装上后在应用设置里能看到「MCP 服务设置（AI 客户端接入）」：启用开关、允许高危操作、端口 `8081`、Web API Key、连接地址示例。

> ⚠️ 别装应用中心那个 **4.6.11** 版——没有 MCP，本项目所有命令都连不上。

### 其余只是运行环境，不算参考

qBittorrent（GPL-2.0，下载器本体）、VueTorrent（GPL-3.0，那个 fpk 里的界面皮肤）、fnOS（运行平台）、Model Context Protocol（协议规范）、YTS（片源 API）、fnos-cli（可选，操作飞牛授权用）。

本仓库不含以上任何一方的源码，也不分发它们的安装包。

### 本仓库原创

`skill/scripts/qb-mcp.sh`（MCP 客户端封装，含 14 条坑的处理）、`docs/` 全部文档、`README.md`、`LICENSE`。

`yts-api.sh` 不算原创。

## License

本仓库原创代码与文档采用 MIT，详见 [LICENSE](LICENSE)。

第三方组件（qBittorrent、YTS、MCP 规范、VueTorrent 等）各自遵循其原有许可证，MIT 不覆盖它们。
