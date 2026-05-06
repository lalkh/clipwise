#!/bin/bash
echo "Starting AI Video Editor..."

# Start CapCut MCP server in background
echo "Starting CapCut MCP server on port 9001..."
python3 services/capcut_mcp.py &
MCP_PID=$!

# Wait for MCP to be ready
sleep 2

# Start main web server
echo "Starting web server on port 8000..."
echo "Open http://localhost:8000 in your browser"
python3 app.py

# Cleanup
kill $MCP_PID 2>/dev/null
