#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:-"$ROOT/deploy/nomadhost/radik-ai-bot-nomadhost.tar.gz"}"
OUT_DIR="$(dirname "$OUT")"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$OUT_DIR" "$STAGE/radik-ai-bot/bot/assets" \
  "$STAGE/radik-ai-bot/deploy/nomadhost/systemd"

cp "$ROOT/bot/radik_bot.py" "$STAGE/radik-ai-bot/bot/"
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