#!/usr/bin/env bash
# Starts the web app. Stop it with Ctrl+C.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then echo "Run ./setup.sh first."; exit 1; fi
# shellcheck disable=SC1091
source .venv/bin/activate

PORT=$(grep -E '^PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]'); PORT=${PORT:-8000}

port_state() {  # prints "free" or "busy"
  python - "$1" <<'PY'
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(("0.0.0.0", int(sys.argv[1])))
    print("free")
except OSError:
    print("busy")
finally:
    s.close()
PY
}

if [ "$(port_state "$PORT")" = "busy" ]; then
  echo "Port $PORT is already used by another application on this machine (not this app)."
  NEW=""
  for p in $(seq $((PORT + 1)) $((PORT + 99))); do
    if [ "$(port_state "$p")" = "free" ]; then NEW=$p; break; fi
  done
  if [ -z "$NEW" ]; then echo "No free port found near $PORT. Set a free PORT= in .env and try again."; exit 1; fi
  echo "Using port $NEW instead. To make this permanent, set  PORT=$NEW  in the .env file."
  PORT=$NEW
fi
export PORT

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "PNB Peer Benchmarking is starting."
echo "Open  http://${IP:-localhost}:${PORT}   (on this machine: http://localhost:${PORT})"
echo "Stop it with Ctrl+C."
echo
exec python -m app.main
