#!/bin/bash
# Fix volume permissions (volumes are mounted as root)
chown -R claude:claude /home/claude/.claude /app/uploads /app/outputs /app/frames 2>/dev/null

# Run as claude
exec su -s /bin/bash claude -c '/app/start.sh'
