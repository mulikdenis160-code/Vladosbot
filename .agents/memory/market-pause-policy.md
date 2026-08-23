---
name: Market pause policy
description: Final-result and recovery-pause rules for losing signal series.
---

A signal series may make up to three attempts. The first two failed attempts are
communicated as overlays, not final losses; only the third failed attempt is a
final LOSS. A final three-attempt loss pauses new signal series for at least
30 minutes. At the end of each pause period, resume only after a fresh market
check confirms directional movement; otherwise extend the pause.

**Why:** The bot must not present recoverable attempts as terminal failures or
continue searching through a flat or unstable market after a fully failed
series.

**How to apply:** Any changes to signal-state transitions, Telegram result
messages, daily statistics, or restart behavior must preserve the distinction
between an overlay attempt and a final series result. Never resume the pause
from stale or earlier market observations.