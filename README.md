# AI Video Editor

**English** | [简体中文](./README.zh-CN.md)

> AI-powered video analysis + automated editing for JianYing (剪映) / CapCut, driven by Claude Code.

Upload a reference video → AI analyzes its shot structure, composition, and editing style. Upload new raw materials → AI matches each shot to the best material and produces a native JianYing/CapCut project file you can immediately open and fine-tune.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Docker](https://img.shields.io/badge/deploy-docker%20compose-blue)](#quick-start)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)](#quick-start)

## Demo

https://github.com/lalkh/clipwise/raw/main/assets/demo.mp4

> **⚠️ Best results require single-shot materials.**
> The matcher assigns **one material per template shot**, so it works far better when each uploaded clip is a single, self-contained shot.
> - **Good input**: 30 short clips, each one continuous take of one subject/scene
> - **Bad input**: 1 long unedited recording that contains many shots inside it
>
> If you only have long footage, pre-cut it into shot-level clips first (a manual JianYing pass, or `ffmpeg -ss/-t`).

---

## Features

- **Shot-by-shot analysis** — ffmpeg scene detection + frame-level visual verification; returns per-shot composition, camera movement, lighting, transitions, on-screen text
- **Style-aware material matching** — Claude reads the analysis markdown, groups your uploads by shot type / camera movement / color, picks the best clip + trim point + transition for every template shot
- **Native JianYing / CapCut project output** — writes `draft_info.json` directly into the desktop app's draft directory; open in JianYing and continue editing, no import step
- **Custom JianYing MCP** — rebuilt from scratch with extended capabilities (see below)
- **Cross-platform** — one-command deploy on macOS, Windows, and Linux

### JianYing MCP capabilities

The built-in MCP server is a custom implementation, not a wrapper around any existing library. Currently supported features:

| Category | Feature |
|----------|---------|
| Timeline | Add video / image / audio to main track or overlay tracks |
| Text | Auto subtitles, flower text (花字) with effect resolution |
| Effects | Filters, visual effects, fade in/out, masks |
| Animation | Keyframe animation support |
| Audio | Auto vocal separation (背景声分离), audio effects |
| Enhancement | Auto stabilization (防抖), AI lip sync |
| Adjustment | Color correction, speed control (constant / curve) |
| Project | Create / save drafts, transition management |

---

## Quick start

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) 20.10+ with Compose v2
- An Anthropic account (for Claude Code; login runs inside the web UI after startup)
- Optional: JianYing Pro / CapCut desktop app — only needed if you want projects to appear directly in the editor. Without it, projects are saved under `./drafts/`.

### Install

```bash
git clone https://github.com/lalkh/clipwise.git
cd clipwise

# macOS / Linux
./deploy.sh up

# Windows (PowerShell)
.\deploy.ps1 up
```

> **China mainland users**: if the build fails due to network issues, add `--cn` to use Chinese mirrors (Aliyun) for apt / npm / pip:
> ```bash
> ./deploy.sh up --cn
> # or rebuild
> ./deploy.sh rebuild --cn
> ```

First launch builds the image (~3–5 min; installs ffmpeg + Node.js + `@anthropic-ai/claude-code` + Python deps).

Open **http://localhost:8000** → click the ⚙ gear → "Log in to Claude" → complete OAuth → start uploading.

### Common commands

| Command | What it does |
|--------|--------------|
| `deploy.sh up` / `deploy.ps1 up` | Start (builds on first run) |
| `… restart` | Restart container without rebuilding |
| `… rebuild` | Force a clean rebuild (use after code/dep changes) |
| `… logs` | Tail container logs |
| `… status` | Show health + port status |
| `… down` | Stop and remove container |

---

## JianYing / CapCut integration

When the deploy script finds your JianYing installation, it configures Docker to mount the draft folder directly — generated projects appear inside the desktop app immediately, no copying required.

Default draft paths the scripts auto-detect:

| OS | Path |
|----|------|
| macOS | `~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft` |
| Windows | `%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft` |
| Linux | `~/.local/share/JianyingPro/User Data/Projects/com.lveditor.draft` |
| WSL2 | `/mnt/c/Users/<USERNAME>/AppData/Local/JianyingPro/User Data/Projects/com.lveditor.draft` |

If JianYing is not detected, projects are saved under `./drafts/` in the repo and you can open them manually.

To override, edit `.env`:

```dotenv
JIANYING_DRAFT_DIR=/your/custom/path
JIANYING_CACHE_DIR=/your/custom/cache/path
```

> **macOS file sharing note:** if Docker Desktop reports "mounts denied" on first run, open Docker Desktop → Settings → Resources → File sharing and add `~/Movies/JianyingPro` to the allowed list.

---

## How to use

### 1. Analyze a reference video

1. Go to the **视频拉片** tab
2. Upload a reference video
3. Claude runs the `video-analyze` skill:
   - Two-pass ffmpeg scene detection
   - 4fps keyframe extraction
   - Visual verification of every candidate cut
   - Per-shot composition / camera / lighting / text / transitions analysis
4. Review the result in card view, table view, or raw markdown
5. Optional: split / merge / re-analyze individual shots

### 2. Auto-edit with new materials

1. Go to the **自动剪辑** tab
2. Pick a completed analysis as template
3. Upload your raw materials (videos / images; folders are supported via drag-and-drop)
4. Optional: add instructions in the prompt box
5. Click **开始自动剪辑**. Claude runs the `video-edit` skill:
   - Probes every material's metadata and representative frames
   - Groups by shot type / camera movement / color profile / stability
   - Matches shots with 5-dimension weighted scoring (framing 30% / camera 25% / mood 20% / stability 15% / duration 10%)
   - Picks transitions per the template's edit graph
   - Writes `draft_info.json` via the CapCut MCP server
6. Open JianYing → your project is waiting under the template name → fine-tune & export

---

## Architecture

```
┌────────────────┐   ┌──────────────────┐   ┌───────────────┐
│ Web (FastAPI)  │   │  Claude Code CLI  │   │  CapCut MCP   │
│   :8000        │──▶│   + skill files  │──▶│   (Flask)     │
│                │   │                  │   │   :9001       │
└────────┬───────┘   └──────────────────┘   └───────┬───────┘
         │                                          │
         ▼                                          ▼
 uploads/ outputs/                            ./drafts/
  frames/                                     (or your JianYing folder)
```

### Key files

| Component | File |
|-----------|------|
| Web server | `app.py` |
| Claude CLI wrapper | `services/claude_client.py` |
| Browser OAuth flow | `services/claude_auth.py` |
| Video analysis pipeline | `services/video_analyzer.py` |
| Auto-edit pipeline | `services/auto_editor.py` |
| CapCut MCP (Flask) | `services/capcut_mcp.py` |
| Beat detection (librosa) | `services/beat_detector.py` |
| Analysis skill | `.claude/skills/video-analyze/SKILL.md` |
| Edit skill | `.claude/skills/video-edit/SKILL.md` |

### Data flow & privacy

- All your videos / frames / reports stay on **your machine** — in `./uploads/`, `./outputs/`, `./frames/`, and `./drafts/`
- The only outbound traffic is your prompts + extracted keyframes to `api.anthropic.com` via Claude Code
- No telemetry, no third-party trackers, no material upload to anyone else
- `.env` is gitignored; your Claude OAuth token lives in a named Docker volume, never on disk in the repo

---

## Configuration reference

All settings live in `.env` (created on first run by the deploy script). See `.env.example` for the full list with per-OS templates.

| Variable | Default | Purpose |
|----------|---------|---------|
| `WEB_PORT` | `8000` | Host port for the web UI |
| `MCP_PORT` | `9001` | Host port for the CapCut MCP server |
| `JIANYING_DRAFT_DIR` | (auto-detected) | Host path to JianYing's draft folder; empty → `./drafts` |
| `JIANYING_CACHE_DIR` | (auto-detected) | JianYing effect cache (read-only mount for flower-text / filter resolution) |

---

## Local development (without Docker)

```bash
# Prerequisites: Python 3.10+, ffmpeg, Node.js 20+
npm install -g @anthropic-ai/claude-code
claude login

# macOS
brew install ffmpeg
# Linux
sudo apt-get install ffmpeg

pip install -r requirements.txt
./start.sh        # launches CapCut MCP (:9001) + web server (:8000)
```

---

## FAQ

**Q: "Port already in use" on startup**
A: Another service is bound to 8000 or 9001. Either free the port, or change `WEB_PORT` / `MCP_PORT` in `.env`.

**Q: UI shows "Login expired"**
A: Click the ⚙ gear → Log in to Claude → OAuth. The token is persisted in a Docker volume and reused on container restart.

**Q: Analysis / editing feels slow**
A: Claude inspects every keyframe it sees. A 20-second reference video with ~15 shots is typically 3–5 minutes of wall time; most of it is model inference.

**Q: JianYing won't open the generated project**
A: Check version compatibility. The MCP writes `version=400000` by default (JianYing Pro 4.x); newer major releases may need the version bumped in `services/capcut_mcp.py`.

**Q: Does this support the international CapCut?**
A: Yes. Both JianYing and CapCut share the same draft format, and the built-in MCP server handles both.

**Q: Can I run this on a remote server?**
A: Yes, but JianYing integration assumes the desktop app runs on the same machine. For headless / remote usage:

1. Leave `JIANYING_DRAFT_DIR` empty — generated projects land in `./drafts/edit_<job_id>/`
2. Click **下载剪映工程** in the web UI → get `capcut_<job_id>.zip`
3. On your editing machine, unzip it → you get a folder named `edit_<job_id>/`
4. Copy that folder into your JianYing draft directory (the OS-specific paths listed [above](#jianying--capcut-integration))
5. Open JianYing — the project appears in the draft list

If Docker and JianYing run on the same machine, skip all of this; set `JIANYING_DRAFT_DIR` in `.env` and projects show up automatically.

---

## Uninstall

```bash
# Stop and remove containers, volumes, and images
./deploy.sh down
docker rmi clipwise 2>/dev/null
docker volume rm clipwise_claude-config 2>/dev/null

# Remove the project directory
cd .. && rm -rf clipwise
```

If you mounted JianYing drafts, generated projects inside the JianYing draft folder are **not** deleted automatically — remove them manually in JianYing or from the [draft directory](#jianying--capcut-integration) if you no longer need them.

---

## Contributing

Issues and PRs welcome — see [CONTRIBUTING.md](./CONTRIBUTING.md).

---

## License

[MIT](./LICENSE) — free to use, modify, distribute.

## Credits

- [Claude Code](https://docs.claude.com/en/docs/claude-code) — the agent CLI powering every AI step
