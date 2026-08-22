---
name: Pocket Option price freshness
description: Reliable price capture for signal entry and expiry checks around one-minute candle boundaries.
---

For signal execution tracking, refresh the pair history before recording entry and expiry prices, and require a quote timestamp at or after the relevant boundary. A short retry window is appropriate for transient delays.

**Why:** The bundled Pocket Option client retains old history between refreshes, so reusing its cached last tick can compare identical stale prices and produce false losses or overlays.

**How to apply:** Refresh history for entry and expiry only, verify timestamp freshness, and treat equal or unavailable settlement prices as non-loss outcomes that do not open an overlay.