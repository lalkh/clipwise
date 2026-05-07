#!/usr/bin/env bash
# ai-video-editor — one-click deploy (macOS / Linux)
#
# Usage:
#   ./deploy.sh up         start (builds image on first run)
#   ./deploy.sh rebuild    force image rebuild
#   ./deploy.sh restart    restart container without rebuilding
#   ./deploy.sh down       stop and remove container
#   ./deploy.sh logs       tail container logs
#   ./deploy.sh status     show health + ports

set -euo pipefail
cd "$(dirname "$0")"

CMD="${1:-up}"
CN_MIRROR=0
for arg in "$@"; do
  if [ "$arg" = "--cn" ]; then CN_MIRROR=1; fi
done

# ─── OS detection ──────────────────────────────────────────────────────────
detect_os() {
  case "$(uname -s)" in
    Darwin*)  echo "macos"  ;;
    Linux*)
      # Distinguish WSL2 from plain Linux so paths are right
      if grep -qi "microsoft" /proc/version 2>/dev/null; then
        echo "wsl"
      else
        echo "linux"
      fi
      ;;
    *)        echo "unknown" ;;
  esac
}

# Default JianYing paths per OS. Returns empty string if none likely.
default_jianying_draft_dir() {
  local os="$1"
  case "$os" in
    macos)
      echo "$HOME/Movies/JianyingPro/User Data/Projects/com.lveditor.draft"
      ;;
    linux)
      echo "$HOME/.local/share/JianyingPro/User Data/Projects/com.lveditor.draft"
      ;;
    wsl)
      # Best guess: current WSL user maps to a Windows username of the same name.
      # User should verify and edit .env if wrong.
      echo "/mnt/c/Users/$USER/AppData/Local/JianyingPro/User Data/Projects/com.lveditor.draft"
      ;;
    *)  echo ""  ;;
  esac
}

default_jianying_cache_dir() {
  local os="$1"
  case "$os" in
    macos)
      echo "$HOME/Movies/JianyingPro/User Data/Cache/artistEffect"
      ;;
    linux)
      echo "$HOME/.local/share/JianyingPro/User Data/Cache/artistEffect"
      ;;
    wsl)
      echo "/mnt/c/Users/$USER/AppData/Local/JianyingPro/User Data/Cache/artistEffect"
      ;;
    *)  echo ""  ;;
  esac
}

# Create .env with sensible defaults on first run. Never overwrites existing.
bootstrap_env() {
  if [ -f .env ]; then
    return 0
  fi

  local os; os=$(detect_os)
  local draft_dir; draft_dir=$(default_jianying_draft_dir "$os")
  local cache_dir; cache_dir=$(default_jianying_cache_dir "$os")

  # Only auto-fill the path if the folder actually exists on this machine
  if [ -n "$draft_dir" ] && [ ! -d "$draft_dir" ]; then
    echo "[deploy] JianYing not detected at '$draft_dir'"
    echo "         → drafts will land in ./drafts (edit .env later to change)"
    draft_dir=""
    cache_dir=""
  fi

  # Quote values so shell tooling handles paths with spaces safely.
  # docker-compose itself supports both quoted and unquoted values.
  cat > .env <<EOF
# Auto-generated on first run by deploy.sh ($(date))
# OS: $os
WEB_PORT=8000
MCP_PORT=9001
JIANYING_DRAFT_DIR="${draft_dir}"
JIANYING_CACHE_DIR="${cache_dir}"
EOF
  echo "[deploy] wrote .env (JianYing draft dir: ${draft_dir:-./drafts})"
}

# Read a scalar from .env without sourcing it (safer with spaced values).
read_env() {
  local key="$1" default="${2:-}"
  [ -f .env ] || { echo "$default"; return; }
  local line; line=$(grep -E "^${key}=" .env | head -1 | cut -d= -f2-)
  # Strip surrounding quotes if present
  line="${line%\"}"; line="${line#\"}"
  line="${line%\'}"; line="${line#\'}"
  echo "${line:-$default}"
}

check_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "[deploy] ✗ docker not found. Install Docker Desktop or docker CLI first." >&2
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "[deploy] ✗ docker daemon not running. Start Docker Desktop and retry." >&2
    exit 1
  fi
  if ! docker compose version >/dev/null 2>&1; then
    echo "[deploy] ✗ 'docker compose' not available. Docker 20.10+ with Compose v2 required." >&2
    exit 1
  fi
}

check_port() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1 \
     && lsof -Pi :"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
    local pid; pid=$(lsof -Pi :"$port" -sTCP:LISTEN -t | head -1)
    echo "[deploy] ✗ port $port is in use (PID=$pid)." >&2
    echo "         Free it or set a different WEB_PORT/MCP_PORT in .env." >&2
    exit 1
  fi
}

wait_healthy() {
  local port; port=$(read_env WEB_PORT 8000)
  echo -n "[deploy] waiting for service to come up"
  for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${port}/api/config/status" >/dev/null 2>&1; then
      echo ""
      echo "[deploy] ✓ service ready on http://localhost:${port}"
      return 0
    fi
    echo -n "."
    sleep 1
  done
  echo ""
  echo "[deploy] ✗ service did not respond within 60s. Run './deploy.sh logs' for details." >&2
  return 1
}

case "$CMD" in
  up)
    check_docker
    bootstrap_env
    web_port=$(read_env WEB_PORT 8000)
    mcp_port=$(read_env MCP_PORT 9001)
    check_port "$web_port"
    check_port "$mcp_port"
    echo "[deploy] starting (first build takes 3–5 minutes)..."
    docker compose build --build-arg USE_CN_MIRROR="$CN_MIRROR"
    docker compose up -d
    wait_healthy
    echo ""
    echo "  ➜  Open http://localhost:${web_port}"
    echo "  ➜  Click the ⚙ gear icon on first run to log in to Claude"
    ;;

  rebuild)
    check_docker
    bootstrap_env
    docker compose down
    docker compose build --no-cache --build-arg USE_CN_MIRROR="$CN_MIRROR"
    web_port=$(read_env WEB_PORT 8000)
    mcp_port=$(read_env MCP_PORT 9001)
    check_port "$web_port"
    check_port "$mcp_port"
    docker compose up -d
    wait_healthy
    ;;

  restart)
    check_docker
    docker compose restart
    wait_healthy
    ;;

  down)
    check_docker
    docker compose down
    echo "[deploy] stopped"
    ;;

  logs)
    docker compose logs -f --tail=100
    ;;

  status)
    docker compose ps
    echo ""
    local_port=$(read_env WEB_PORT 8000)
    mcp_port=$(read_env MCP_PORT 9001)
    if curl -fsS "http://127.0.0.1:${local_port}/api/config/status" 2>/dev/null; then
      echo ""; echo "  ✓ web ${local_port}"
    else
      echo "  ✗ web ${local_port} unreachable"
    fi
    if curl -fsS "http://127.0.0.1:${mcp_port}/health" 2>/dev/null >/dev/null; then
      echo "  ✓ capcut-mcp ${mcp_port}"
    else
      echo "  ✗ capcut-mcp ${mcp_port} unreachable"
    fi
    ;;

  *)
    echo "Usage: $0 {up|rebuild|restart|down|logs|status}"
    exit 2
    ;;
esac
