#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:-"$ROOT/deploy/nomadhost/radik-ai-bot-nomadhost.tar.gz"}"
OUT_DIR="$(dirname "$OUT")"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$OUT_DIR" "$STAGE/radik-ai-bot/bot/assets" \
  "$STAGE/radik-ai-bot/deploy/nomadhost/systemd"

python3 - "$ROOT/bot/radik_bot.py" \
  "$STAGE/radik-ai-bot/bot/radik_bot.py" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(encoding="utf-8")
marker = '#!/usr/bin/env python3\n"""RADIK AI BOT: Pocket Option WebSocket market scanner.'
first = source.find(marker)
second = source.find(marker, first + len(marker))
if first != 0 or second < 0:
    raise SystemExit("Expected the duplicated RADIK bot source was not found")

canonical = source[:second].rstrip("\n")
duplicate = source[second:].rstrip("\n")
duplicate = duplicate.replace(
    "\n# Redundant copy of the module; the future import already appears at file start.",
    "\nfrom __future__ import annotations",
    1,
)
if duplicate != canonical:
    raise SystemExit("The duplicate bot source blocks differ; refusing to package")

Path(sys.argv[2]).write_text(canonical + "\n", encoding="utf-8")
PY
cp "$ROOT/bot/telegram_bridge.mjs" "$ROOT/bot/requirements.txt" \
  "$ROOT/bot/test_radik_bot.py" "$STAGE/radik-ai-bot/bot/"
cp "$ROOT"/bot/assets/* "$STAGE/radik-ai-bot/bot/assets/"
cp "$ROOT"/deploy/nomadhost/README.md \
  "$ROOT"/deploy/nomadhost/radik-bot.env.example \
  "$ROOT"/deploy/nomadhost/nginx-http.conf.example \
  "$STAGE/radik-ai-bot/deploy/nomadhost/"
cp "$ROOT"/deploy/nomadhost/systemd/*.service \
  "$STAGE/radik-ai-bot/deploy/nomadhost/systemd/"

tar -C "$STAGE" -czf "$OUT" radik-ai-bot
tar -tzf "$OUT" >/dev/null
printf 'Bundle created: %s\n' "$OUT"
sha256sum "$OUT"