# RADIK AI BOT — NomadHost VPS preparation

This directory is a deployment kit for a **Linux VPS with SSH/sudo, systemd,
and a public domain**. It is not suitable for ordinary shared hosting/cPanel:
the bot needs two long-running services and a persistent WebSocket connection.

The NomadHost account has not been accessed or changed. The Replit instance is
untouched and remains the current host.

## Important before switching

The bot starts its signal scheduler immediately (`STARTUP_SAFETY_PAUSE_SEC=0`).
Do not run both Replit and VPS schedulers against the same Pocket Option account
at the same time: that can create duplicate signals and competing WebSocket
sessions. Install and configure the VPS first, but leave both systemd services
stopped until choosing a cutover time. Keep Replit available as the rollback
host; do not delete its secrets or deployment.

## VPS requirements

- Ubuntu 22.04+/Debian 12+ Linux VPS with sudo/root SSH access
- Python 3.13 (the setup below uses `uv` to install it)
- Node.js 18.13 or newer; no npm packages are needed when a Telegram bot token
  is configured
- Nginx and Certbot
- A domain with DNS A/AAAA records pointing to the VPS and HTTPS available
- Outbound HTTPS and Pocket Option WebSocket traffic

If the NomadHost product is shared hosting rather than a VPS, stop here and ask
the provider whether it supports persistent Python/Node services, WebSockets,
and systemd. These unit files require a VPS.

## Build and transfer the bundle

From the Replit workspace:

```bash
bash deploy/nomadhost/build-bundle.sh
```

This creates `deploy/nomadhost/radik-ai-bot-nomadhost.tar.gz`. It contains the
bot source, image assets, tests, and these deployment files. It does **not**
contain `.env` files, Replit Secrets, or any credential values.

Upload the archive to the VPS with SFTP/SCP. The commands below assume the
archive is in `/tmp` on the VPS:

```bash
sudo mkdir -p /opt/radik-bot /etc/radik-bot /var/lib/radik-bot
sudo tar -xzf /tmp/radik-ai-bot-nomadhost.tar.gz \
  -C /opt/radik-bot --strip-components=1
sudo useradd --system --home-dir /var/lib/radik-bot \
  --shell /usr/sbin/nologin radikbot
sudo chown -R root:root /opt/radik-bot
sudo chown radikbot:radikbot /var/lib/radik-bot
```

If `radikbot` already exists, skip `useradd`.

## Install runtimes and Python dependencies

Install Nginx, Certbot, Node.js 18.13+, build tools, and `curl` using the
NomadHost-supported Ubuntu/Debian repositories. Check `node --version` before
continuing.

Install `uv` from Astral's official installer, then create the isolated Python
environment:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.13
uv venv --python 3.13 /opt/radik-bot/.venv
uv pip install --python /opt/radik-bot/.venv/bin/python \
  -r /opt/radik-bot/bot/requirements.txt
```

## Configure secrets on the VPS

Create the root-owned environment file with access limited to the bot group:

```bash
sudo install -o root -g radikbot -m 0640 \
  /opt/radik-bot/deploy/nomadhost/radik-bot.env.example \
  /etc/radik-bot/radik-bot.env
sudoedit /etc/radik-bot/radik-bot.env
```

Fill in `POCKET_SSID`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`GEMINI_API_KEY`, and `TELEGRAM_PUBLIC_BASE_URL` (`https://` plus your actual
domain). Keep `POCKET_DEMO=true` unless you intentionally configure a live
account. Do not copy a Replit Gemini proxy URL to the VPS; the VPS needs a
Gemini API key that it can reach directly.

## Public image URL and HTTPS

Copy `nginx-http.conf.example` to the Nginx sites directory, replace
`bot.example.com` with your domain, enable the site, then test/reload Nginx.
Obtain a certificate with Certbot's Nginx plugin:

```bash
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx --redirect -d bot.example.com
```

Confirm that this URL returns HTTP 200 with `Content-Type: image/jpeg`:

```text
https://bot.example.com/api/telegram-image/signal
```

The Nginx example exposes only the health endpoint and image files; it does not
expose the bot's other API routes. Keep ports 8765 and 8010 blocked from public
ingress in the VPS firewall; allow SSH and HTTPS (and HTTP for certificate
renewal).

## Install services, but do not start yet

```bash
sudo install -o root -g root -m 0644 \
  /opt/radik-bot/deploy/nomadhost/systemd/radik-bot.service \
  /etc/systemd/system/radik-bot.service
sudo install -o root -g root -m 0644 \
  /opt/radik-bot/deploy/nomadhost/systemd/radik-telegram-bridge.service \
  /etc/systemd/system/radik-telegram-bridge.service
sudo systemctl daemon-reload
```

At this point the VPS files are installed but neither bot service has been
started. For a later cutover, first stop the Replit scheduler, then start the
bridge and bot services:

```bash
sudo systemctl enable --now radik-telegram-bridge.service radik-bot.service
```

Check logs with `journalctl -u radik-telegram-bridge -u radik-bot -f`. To roll
back, stop/disable both VPS services before resuming the Replit scheduler.

## Safe verification before cutover

The bridge can be checked without starting the market scanner:

```bash
sudo systemctl start radik-telegram-bridge.service
curl -fsS http://127.0.0.1:8010/health
curl -fsS https://bot.example.com/api/telegram-image/signal -o /tmp/signal.jpg
file /tmp/signal.jpg
sudo systemctl stop radik-telegram-bridge.service
```

Do not test `/send-message` or `/send-photo` unless an intentional test message
in the Telegram channel is acceptable. The bot scanner service is deliberately
not started by the preparation commands above.