---
name: Single active signal scheduler
description: Safe operation when preparing a second host for the same Pocket Option account.
---

Only one RADIK signal scheduler should run against a Pocket Option account at a time. Keep a replacement host installed but stopped until the current scanner is paused; retain the old deployment for rollback, but do not let both schedulers trade or publish signals concurrently.

**Why:** Two live processes can create duplicate signals, competing WebSocket sessions, and inconsistent per-host state.

**How to apply:** For host migration, prepare dependencies, secrets, HTTPS, and services on the new machine first. At cutover, stop one scheduler before starting the other; reverse that order for rollback.