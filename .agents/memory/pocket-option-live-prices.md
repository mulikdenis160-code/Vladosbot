---
name: Pocket Option price freshness
description: Reliable price capture for signal entry and expiry checks around one-minute candle boundaries.
---

For signal execution tracking, refresh each pair through the lightweight `ChangeSymbol` subscription and use its ticks for prices plus its returned candles for analysis. Require a quote timestamp at or after the relevant boundary.

**Why:** The bundled Pocket Option client retains old history between refreshes, while its heavier candle-history method can time out and leave the worker blocked. Reusing a cached last tick can compare identical stale prices and produce false losses or overlays.

**How to apply:** Use the subscription response for analysis, refresh it for entry and expiry, verify timestamp freshness, and treat equal or unavailable settlement prices as non-loss outcomes that do not open an overlay.

When a candle-history request times out, end the current bounded signal-search session normally and apply a retry cooldown instead of immediately restarting it.

**Why:** A timed-out library request can leave its worker occupied; instant retries repeatedly fail and create an endless visible “searching” loop.

**How to apply:** Keep the search deadline and a configurable quiet interval separate. Notify the channel once when the deadline expires, then wait for the cooldown before a new search session.