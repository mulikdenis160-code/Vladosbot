---
name: Pocket Option live prices
description: Reliable price capture for signal entry and expiry checks around one-minute candle boundaries.
---

For signal execution tracking, read the latest live tick from Pocket Option's connected market history rather than requiring a fresh candle-history request at the exact minute boundary. A short retry window is appropriate for transient tick delays.

**Why:** Full candle-history requests can time out or return incomplete data when a new candle starts, which can silently prevent entry activation and therefore skip result and overlay handling.

**How to apply:** Use the live tick path for the entry price and expiry price; reserve candle-history requests for signal analysis where recent closes and volatility are needed.