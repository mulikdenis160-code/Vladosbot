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

An announced or pending signal remains the sole series owner until a terminal
result is sent. A missing entry quote must resolve as UNKNOWN, not clear the
pending state and start another search.

**Why:** Clearing a sent-but-unconfirmed signal creates an apparent second
signal even though the first series has not been resolved.

**How to apply:** Treat pending entry failures and missed entry windows as
terminal non-loss outcomes before allowing the scheduler to search again.

Final-result delivery is retried independently from signal scheduling: a
Telegram delivery failure must persist the result in a queue, release the
series lock, and let the scheduler continue. The queued result is retried with
backoff until delivery succeeds.

**Why:** A Telegram outage must not stop market monitoring and new signal
searches indefinitely. Persisting the result before releasing the lock avoids
losing the outcome while preventing one failed notification from blocking the
bot.

**How to apply:** Persist the terminal result and enqueue it when delivery
fails; clear the active or pending series immediately. Keep queued results
visible in health state and retry them independently.

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

**How to apply:** Production restarts begin with a neutral safety pause, and
only the production VM may send Telegram signals; never run a local bot beside it.