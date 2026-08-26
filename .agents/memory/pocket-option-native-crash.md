---
name: PocketOptionAPI-v2 native crash and process auto-restart
description: Root cause and mitigation for a "malloc(): unsorted double linked list corrupted" crash that killed the production bot for hours.
---

The `PocketOptionAPI-v2` websocket client (`pocketoptionapi.stable_api.PocketOption`,
git dependency) is not thread-safe. The bot's scheduler thread, Flask request
threads, and the candle worker thread all called into the same shared `api`
instance (`ChangeSymbol`, `get_payout`, `get_server_timestamp`, `connect`)
concurrently. This produced a native memory-corruption crash
(`malloc(): unsorted double linked list corrupted`) that aborts the whole
Python process — Python-level `try/except` cannot catch it.

**Why:** A native abort is not a Python exception; nothing in-process can
recover from it, and the artifact's production run command used `exec`, which
replaces the shell with the Python process. Once Python aborted, there was
nothing left to restart it, so the bot stayed down for hours with no
supervisor watching it.

**How to apply:**
- All calls into the shared PocketOption `api` object must be serialized
  behind a single `threading.RLock()` (`api_lock` in `bot/radik_bot.py`),
  not just the `ChangeSymbol` submission. Any new code path that touches
  `api` must acquire this lock too, or the same class of native crash can
  recur.
- The production run command in `artifacts/api-server/.replit-artifact/artifact.toml`
  wraps `uv run python bot/radik_bot.py` in a `while true; do ...; sleep 5; done`
  loop instead of `exec`, so any process exit (including a native crash) gets
  the process restarted within seconds instead of leaving the deployment dead
  until a human notices and republishes. Preserve this restart loop in any
  future edit to that run command.
