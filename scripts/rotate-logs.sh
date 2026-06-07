#!/usr/bin/env bash
set -euo pipefail

# Rotate appliance logs that exceed 10MB; keep 3 compressed generations.
# No sudo / newsyslog needed. Invoked daily by backup-db.sh.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGDIR="$ROOT/logs"
MAXBYTES=$((10*1024*1024))

shopt -s nullglob
for f in "$LOGDIR"/*.out "$LOGDIR"/*.err; do
  sz=$(stat -f%z "$f" 2>/dev/null || echo 0)
  if [ "$sz" -gt "$MAXBYTES" ]; then
    for i in 2 1 0; do
      [ -f "$f.$i.gz" ] && mv "$f.$i.gz" "$f.$((i+1)).gz"
    done
    gzip -c "$f" > "$f.0.gz"
    : > "$f"                       # truncate in place (keeps the open fd valid for launchd)
    rm -f "$f.3.gz"
  fi
done
echo "log rotation complete"
