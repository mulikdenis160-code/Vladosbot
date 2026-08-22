---
name: Replit Gemini proxy
description: Managed Gemini proxy routing and request format for the Python signal bot.
---

For Replit-managed Gemini, send `generateContent` requests to the proxy's `/models/<model>:generateContent` route and include the content role as `user`.

**Why:** The proxy rejects the public Google REST `/v1` and `/v1beta` routes even though it remains Gemini-compatible.

**How to apply:** Prefer the managed integration environment variables when available, use a supported integration model such as `gemini-2.5-flash`, and retain the existing backoff controls.