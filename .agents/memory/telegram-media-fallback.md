---
name: Telegram media fallback
description: Delivery behavior when Telegram photo uploads fail through the Replit connector proxy.
---

The Replit Telegram proxy can accept text messages while rejecting multipart photo uploads with a missing-photo error. Use a publicly reachable image URL for `sendPhoto`, and keep a complete text fallback whenever photo delivery fails.

**Why:** Signal and search notifications that only attempted a multipart photo were silently unavailable even though the Telegram connection, channel, and text API were healthy.

**How to apply:** Serve the fixed image assets from a public application route and pass that URL to Telegram. Keep photo delivery optional: signal, overlay, search, result, and summary paths must send their complete text when URL-based photo delivery fails.