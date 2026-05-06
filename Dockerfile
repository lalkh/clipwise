FROM python:3.11-slim

# Configure apt to survive flaky proxies / mirror hiccups
RUN cat > /etc/apt/apt.conf.d/80-retries <<'EOF'
Acquire::Retries "10";
Acquire::http::Timeout "30";
Acquire::https::Timeout "30";
Acquire::http::Pipeline-Depth "0";
Acquire::Check-Valid-Until "false";
EOF

# Shared helper: retry `apt-get update` up to 5 times (proxies often 502).
# Put it in /usr/local/bin so every RUN below can reuse it.
RUN printf '#!/bin/sh\nfor i in 1 2 3 4 5; do\n  apt-get update 2>&1 && exit 0\n  echo "[build] apt-get update attempt $i failed, sleep $((i*3))s then retry" >&2\n  sleep $((i*3))\ndone\napt-get update -o Acquire::AllowInsecureRepositories=true || true\n' > /usr/local/bin/apt-update-retry \
 && chmod +x /usr/local/bin/apt-update-retry

# Layer 1: ffmpeg (largest, most fragile)
RUN apt-update-retry \
 && (apt-get install -y --no-install-recommends ffmpeg \
     || apt-get install -y --no-install-recommends --fix-missing ffmpeg) \
 && rm -rf /var/lib/apt/lists/*

# Layer 2: curl + gnupg + ca-certificates (needed by NodeSource)
RUN apt-update-retry \
 && (apt-get install -y --no-install-recommends curl gnupg ca-certificates \
     || apt-get install -y --no-install-recommends --fix-missing curl gnupg ca-certificates) \
 && rm -rf /var/lib/apt/lists/*

# Layer 3: Node.js 20 + Claude Code CLI
RUN curl -fsSL --retry 10 --retry-delay 3 --connect-timeout 30 https://deb.nodesource.com/setup_20.x | bash - \
 && apt-update-retry \
 && (apt-get install -y --no-install-recommends nodejs \
     || apt-get install -y --no-install-recommends --fix-missing nodejs) \
 && npm install -g @anthropic-ai/claude-code \
 && apt-get purge -y gnupg \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* /tmp/* /root/.npm

WORKDIR /app
ENV PYTHONUNBUFFERED=1

# Python deps — pip retries on its own
COPY requirements.txt .
RUN pip install --no-cache-dir --retries 5 -r requirements.txt \
 && rm -rf /root/.cache

# Non-root user
RUN useradd -m -s /bin/bash claude \
 && mkdir -p uploads outputs frames /home/claude/.claude/remote \
 && chown -R claude:claude /home/claude

# Copy application
COPY --chown=claude:claude . .
COPY --chown=claude:claude start.sh entrypoint.sh ./
RUN chmod +x start.sh entrypoint.sh

EXPOSE 8000 9001
ENTRYPOINT ["./entrypoint.sh"]
