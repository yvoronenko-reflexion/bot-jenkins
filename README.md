# Slack command proxy

Run commands on a VPN-locked server via Slack. Server only needs outbound
access to `slack.com` (Socket Mode WebSocket) — no inbound port, no public URL,
no VPN from your laptop.

## One-time Slack setup

1. https://api.slack.com/apps → **Create New App** → *From scratch*.
2. **Socket Mode** → enable → generate app-level token with `connections:write`
   scope. Save as `SLACK_APP_TOKEN` (starts `xapp-`).
3. **OAuth & Permissions** → add Bot Token Scopes:
   `app_mentions:read`, `chat:write`, `im:history`.
4. **Event Subscriptions** → enable → subscribe to bot events
   `app_mention` and `message.im`.
5. **App Home** → enable the *Messages* tab and check
   *Allow users to send messages from the messages tab*.
6. Install app to workspace. Save Bot Token as `SLACK_BOT_TOKEN` (`xoxb-`).
7. Optionally invite the bot into a channel for @mention use:
   `/invite @your-bot`. For DM use, just open a DM with the bot.
8. Get your Slack user ID (profile → ⋯ → *Copy member ID*).

## Run on the server

Use a virtualenv. On modern macOS (Homebrew Python) and most recent Linux
distros, `pip install` against the system Python is blocked by PEP 668
(`error: externally-managed-environment`) — a venv is the clean fix.

### macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml
chmod 600 config.toml
$EDITOR config.toml                    # fill in slack.bot_token, slack.app_token, slack.allowed_users
python bot.py
```

If `python3` is missing: `brew install python`.

### Linux

```bash
# Debian/Ubuntu: sudo apt install python3 python3-venv
# RHEL/Fedora:   sudo dnf install python3
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml
chmod 600 config.toml
$EDITOR config.toml                    # fill in slack.bot_token, slack.app_token, slack.allowed_users
python bot.py
```

Re-activate the venv (`source .venv/bin/activate`) in any new shell before
running `python bot.py`. To run under systemd or similar, point the unit at
`.venv/bin/python bot.py` directly — no activation needed.

Config path defaults to `./config.toml` next to `bot.py`; override with
`JK_CONFIG=/path/to/config.toml`.

Then in Slack either:
- DM the bot: `uptime`
- or @mention in a channel: `@your-bot uptime`

## Security

This is remote code execution over Slack. Keep it locked down:

- `slack.allowed_users` in `config.toml` — only your Slack IDs.
- `ALLOWED_CMDS` in `bot.py` — only the commands you actually need.
- Run as an unprivileged user (not root); use `sudoers` NOPASSWD for the
  exact commands you need rather than running the whole bot as root.
- Keep it to a private channel. Anyone with posting access to that channel
  who is also in `ALLOWED_USERS` can run commands.
