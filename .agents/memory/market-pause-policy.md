---
name: Market pause policy
description: Final-result and recovery-pause rules for losing signal series.
---

A signal series may make up to three attempts. The first two failed attempts are
eligible for overlays, but an overlay is opened only after a fresh snapshot and
Gemini analysis confirm the same direction above the recovery threshold. If that
confirmation fails, the actual loss is reported and the series ends without an
extra entry. A third failed attempt still triggers the market pause. At the end
of each pause period, resume only after a fresh market check confirms
directional movement; otherwise extend the pause.

**Why:** The bot must not present recoverable attempts as terminal failures or
continue searching through a flat or unstable market after a fully failed
series.

**How to apply:** Any changes to signal-state transitions, Telegram result
messages, daily statistics, or restart behavior must preserve the distinction
between an overlay attempt and a final series result. Never resume the pause
from stale or earlier market observations. Quality-first defaults are a maximum
of 50 announced series per Kyiv day, confidence at least 80%, quality at least
72%, a 10-minute post-series cooldown, and a 15-minute cooldown after a loss.

An announced or pending signal remains the sole series owner until a terminal
result is sent. A missing entry quote must resolve as UNKNOWN, not clear the
pending state and start another search.

**Why:** Clearing a sent-but-unconfirmed signal creates an apparent second
signal even though the first series has not been resolved.

**How to apply:** Treat pending entry failures and missed entry windows as
terminal non-loss outcomes before allowing the scheduler to search again.

Final-result delivery is part of completing a series: a transient Telegram
delivery failure must retain the series lock and retry the result, never release
the scheduler.

**Why:** A result that exists only in process state is not visible to the user;
releasing the lock first creates a new signal with no observable outcome.

**How to apply:** Clear the active or pending series only after the terminal
message has been successfully delivered.

Terminal outcomes use a canonical Telegram text message with a confirmed
message identifier; image captions are optional presentation, never proof that
the user received a result.

**Why:** A media send can be accepted by the bridge yet not surface as the
expected result message in the chat.

**How to apply:** Gate the next search on a successful text-result delivery
and log its Telegram message identifier.

At pause expiry, test every configured control pair afresh and resume only
when the configured recovery quorum passes (default: 2 of 3); a single
favorable sample never releases the pause.

**Why:** One pair can look healthy while the broader OTC market remains
unstable, which made a one-sample pause decision inconsistent.

**How to apply:** A normal production restart must not create a Telegram pause;
only a real third-failure market pause may do that. Only the production VM may
send Telegram signals; never run a local bot beside it.