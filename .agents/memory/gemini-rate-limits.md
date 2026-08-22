---
name: Gemini signal-analysis rate limits
description: Free-tier Gemini may return temporary quota exhaustion during rapid signal scans.
---

Use a single Gemini analysis request per scan and respect the returned retry delay before attempting another analysis.

**Why:** The configured Gemini service can return temporary HTTP 429 responses after bursts, even when the key and model are valid.

**How to apply:** Keep signal search active for its full window, but back off Gemini requests instead of generating a signal without AI confirmation or repeatedly retrying immediately.