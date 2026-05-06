# AI 视频编辑器

[English](./README.md) | **简体中文**

> 基于 Claude Code 的 AI 视频拉片 + 自动剪辑工具,直接产出剪映 / CapCut 工程。

上传参考视频 → AI 分析其分镜结构、构图、剪辑风格 → 上传新素材 → AI 为每个分镜匹配最合适的素材,生成原生剪映 / CapCut 工程文件,可在桌面端直接打开微调。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Docker](https://img.shields.io/badge/deploy-docker%20compose-blue)](#快速开始)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)](#快速开始)

> **⚠️ 素材以「单一分镜」为单位时效果最好。**
> 匹配算法是「**每个模板分镜挑一段素材**」,因此独立短素材的效果远好于一段未剪过的长录像。
> - **推荐输入**:30 段短素材,每段是一个连续镜头(一个主体 / 一个场景)
> - **效果较差**:1 段几分钟的长录像,里面包含了很多分镜
>
> 如果你只有长素材,建议先用剪映粗切一遍或用 `ffmpeg -ss/-t` 切成镜头级别的片段再上传。

---

## 功能特性

- **逐分镜拉片** — ffmpeg 镜头切分 + 帧级视觉确认;输出每段的构图、运镜、光线、转场、字幕信息
- **风格化素材匹配** — Claude 阅读拉片报告,按照镜头类型 / 运镜 / 色调对你的素材进行分组,为每个模板分镜挑选最合适的片段、起止点和转场
- **原生剪映 / CapCut 工程** — 直接写入桌面端的草稿目录(`draft_info.json`),打开剪映即可继续编辑,不需要导入步骤
- **跨平台** — macOS / Windows / Linux 一键部署

---

## 快速开始

### 准备工作
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) 20.10 以上(自带 Compose v2)
- 一个 Anthropic 账号(用于登录 Claude Code,启动后在 Web 界面里完成 OAuth 登录)
- 可选:剪映专业版 / CapCut 桌面端 — 只有当你希望工程直接出现在剪映的草稿列表里才需要;否则工程会保存在 `./drafts/` 下

### 安装

```bash
git clone https://github.com/<your-org>/ai-video-editor.git
cd ai-video-editor

# macOS / Linux
./deploy.sh up

# Windows (PowerShell)
.\deploy.ps1 up
```

首次启动会构建镜像(约 3–5 分钟,安装 ffmpeg + Node.js + `@anthropic-ai/claude-code` + Python 依赖)。

打开 **http://localhost:8000** → 点击 ⚙ 齿轮图标 → "登录 Claude" → 完成 OAuth → 开始使用。

### 常用命令

| 命令 | 作用 |
|--------|------|
| `deploy.sh up` / `deploy.ps1 up` | 启动(首次会自动构建) |
| `… restart` | 重启容器,不重新构建 |
| `… rebuild` | 强制重新构建(代码或依赖变动后用) |
| `… logs` | 查看容器日志 |
| `… status` | 查看运行状态和端口情况 |
| `… down` | 停止并移除容器 |

---

## 剪映 / CapCut 集成

部署脚本会自动检测你机器上的剪映安装位置,并把 Docker 直接挂载到对应的草稿目录 — 生成的工程立刻出现在桌面端,不需要复制。

各平台默认草稿目录(脚本会自动尝试):

| 系统 | 路径 |
|----|------|
| macOS | `~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft` |
| Windows | `%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft` |
| Linux | `~/.local/share/JianyingPro/User Data/Projects/com.lveditor.draft` |
| WSL2 | `/mnt/c/Users/<USERNAME>/AppData/Local/JianyingPro/User Data/Projects/com.lveditor.draft` |

如果检测不到剪映,工程会保存在仓库里的 `./drafts/`,你可以手动打开。

如需自定义路径,编辑 `.env`:

```dotenv
JIANYING_DRAFT_DIR=/your/custom/path
JIANYING_CACHE_DIR=/your/custom/cache/path
```

> **macOS 文件共享提示**:首次启动如果 Docker Desktop 报 "mounts denied",打开 Docker Desktop → Settings → Resources → File sharing,把 `~/Movies/JianyingPro` 加入白名单。

---

## 使用流程

### 1. 拉片(分析参考视频)

1. 进入 **视频拉片** 标签页
2. 上传参考视频
3. Claude 会运行 `video-analyze` skill:
   - 两轮 ffmpeg 镜头切分
   - 4fps 关键帧抽取
   - 视觉确认每个候选切点
   - 输出每个分镜的构图 / 运镜 / 光线 / 字幕 / 转场分析
4. 在卡片视图、表格视图或原始 Markdown 里查看结果
5. 可选:对单个分镜做拆分 / 合并 / 重新分析

### 2. 自动剪辑(智能匹配)

1. 进入 **自动剪辑** 标签页
2. 选一个已完成的拉片作为模板
3. 上传你的素材(视频 / 图片;支持文件夹拖拽)
4. 可选:在 prompt 输入框里加一些指令
5. 点击 **开始自动剪辑**。Claude 会运行 `video-edit` skill:
   - 探测每个素材的元信息和代表性帧
   - 按镜头类型 / 运镜 / 色调 / 稳定性做分组
   - 用 5 维加权打分匹配分镜(构图 30% / 运镜 25% / 情绪 20% / 稳定性 15% / 时长 10%)
   - 按模板的转场图选转场
   - 通过 CapCut MCP 服务写入 `draft_info.json`
6. 打开剪映 → 工程已经出现在草稿列表里(以模板名命名)→ 微调 + 导出

---

## 架构

```
┌────────────────┐   ┌──────────────────┐   ┌───────────────┐
│ Web (FastAPI)  │   │  Claude Code CLI │   │  CapCut MCP   │
│   :8000        │──▶│   + skill 文件   │──▶│   (Flask)     │
│                │   │                  │   │   :9001       │
└────────┬───────┘   └──────────────────┘   └───────┬───────┘
         │                                          │
         ▼                                          ▼
 uploads/ outputs/                            ./drafts/
  frames/                                    (或你的剪映草稿目录)
```

### 关键文件

| 模块 | 文件 |
|-----------|------|
| Web 服务 | `app.py` |
| Claude CLI 封装 | `services/claude_client.py` |
| 浏览器 OAuth 流程 | `services/claude_auth.py` |
| 拉片管线 | `services/video_analyzer.py` |
| 自动剪辑管线 | `services/auto_editor.py` |
| CapCut MCP (Flask) | `services/capcut_mcp.py` |
| 节拍检测 (librosa) | `services/beat_detector.py` |
| 拉片 skill | `.claude/skills/video-analyze/SKILL.md` |
| 剪辑 skill | `.claude/skills/video-edit/SKILL.md` |

### 数据流和隐私

- 你的视频 / 帧 / 报告全部留在**本机**(`./uploads/`、`./outputs/`、`./frames/`、`./drafts/`)
- 唯一的外部流量是你的 prompt + 抽取的关键帧 → `api.anthropic.com`(由 Claude Code 发出)
- 没有任何遥测、第三方追踪、素材外传
- `.env` 已 gitignore;Claude OAuth token 存在 Docker 命名卷中,不会落到仓库里

---

## 配置项

所有配置都在 `.env`(首次运行 deploy 脚本时自动生成)。完整说明见 `.env.example`,里面有各平台的模板。

| 变量 | 默认值 | 用途 |
|----------|---------|------|
| `WEB_PORT` | `8000` | Web UI 的宿主机端口 |
| `MCP_PORT` | `9001` | CapCut MCP 服务的宿主机端口 |
| `JIANYING_DRAFT_DIR` | (自动检测) | 剪映草稿目录的宿主路径;留空则使用 `./drafts` |
| `JIANYING_CACHE_DIR` | (自动检测) | 剪映特效缓存目录(只读挂载,用于花字 / 滤镜资源解析) |

---

## 本地开发(不用 Docker)

```bash
# 准备环境:Python 3.10+、ffmpeg、Node.js 20+
npm install -g @anthropic-ai/claude-code
claude login

# macOS
brew install ffmpeg
# Linux
sudo apt-get install ffmpeg

pip install -r requirements.txt
./start.sh        # 同时启动 CapCut MCP (:9001) 和 Web 服务 (:8000)
```

---

## 常见问题

**Q:启动报 "Port already in use"**
A:8000 或 9001 端口被其他服务占用了。释放端口,或在 `.env` 里改 `WEB_PORT` / `MCP_PORT`。

**Q:UI 显示 "Login expired"**
A:点 ⚙ 齿轮 → 重新登录 Claude → OAuth。token 持久化在 Docker 命名卷里,容器重启后会自动复用。

**Q:拉片 / 剪辑很慢**
A:Claude 要看每一张关键帧。一段 20 秒、约 15 个分镜的参考视频,通常需要 3–5 分钟,大部分时间花在模型推理上。

**Q:剪映打不开生成的工程**
A:可能是版本不兼容。MCP 默认写入 `version=400000`(剪映专业版 4.x),新版本可能需要在 `services/capcut_mcp.py` 里调高这个版本号。

**Q:支持国际版 CapCut 吗?**
A:支持,通过 `pyJianYingDraft`。剪映和 CapCut 共用同一套草稿格式。

**Q:能跑在远程服务器上吗?**
A:可以,但剪映集成假设桌面端跑在同一台机器上。如果是无头 / 远程使用:

1. `JIANYING_DRAFT_DIR` 留空 — 工程会保存到 `./drafts/edit_<job_id>/`
2. 在 Web UI 里点 **下载剪映工程** → 得到 `capcut_<job_id>.zip`
3. 在剪辑用的本机解压 → 得到一个 `edit_<job_id>/` 文件夹
4. 把这个文件夹复制到剪映的草稿目录(参见上方[各平台路径表](#剪映--capcut-集成))
5. 打开剪映 → 工程出现在草稿列表里

如果 Docker 和剪映在同一台机器上,无需上面这些步骤;只要在 `.env` 里设好 `JIANYING_DRAFT_DIR`,工程就会自动出现。

---

## 贡献

欢迎 issue 和 PR — 详见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

---

## 许可证

[MIT](./LICENSE) — 可自由使用、修改、分发。

## 致谢

- [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft) — 这个出色的库使原生剪映 / CapCut 工程生成成为可能
- [Claude Code](https://docs.claude.com/en/docs/claude-code) — 驱动每一步 AI 流程的 agent CLI
