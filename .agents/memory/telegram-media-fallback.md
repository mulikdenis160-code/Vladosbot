---
name: Telegram media fallback
description: Delivery behavior when Telegram photo uploads fail through the Replit connector proxy.
---

The Replit Telegram proxy can accept text messages while rejecting multipart photo uploads with a missing-photo error. User-visible signal delivery must therefore fall back to the complete text message whenever a photo send fails.

**Why:** Signal and search notifications that only attempted a photo were silently unavailable even though the Telegram connection, channel, and text API were healthy.

**How to apply:** Keep photo delivery as an optional presentation layer. Wrap signal, overlay, and search photo sends in a text fallback; do not treat a photo failure as proof that Telegram itself is unavailable.