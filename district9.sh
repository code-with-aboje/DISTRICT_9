#!/usr/bin/env bash
# DISTRICT_9 launcher:  ./district9.sh server   |   ./district9.sh
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  if command -v pkg >/dev/null 2>&1; then pkg install -y python
  else echo "Install python3 first"; exit 1; fi
fi

if [ "$1" = "server" ]; then
  exec python3 server.py
fi

# Termux: get internal storage access so BLACKFOX-inc can be created there
if command -v termux-setup-storage >/dev/null 2>&1 && [ ! -d "$HOME/storage/shared" ]; then
  termux-setup-storage; sleep 4
fi

[ -f .host ] || { read -rp "Server address (host:port): " h; echo "$h" > .host; }
export BF_HOST="$(cat .host)"
exec python3 client.py
