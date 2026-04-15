# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Slack bot (`bot.py`) used as an informal command proxy for a
server that sits behind an unreliable corporate VPN. The server can reach
Slack; the operator can reach Slack; the VPN is bypassed as a transport by
routing commands through a Slack channel.

## Run

```bash
pip install -r requirements.txt
export SLACK_BOT_TOKEN=xoxb-...     # Bot User OAuth Token
export SLACK_APP_TOKEN=xapp-...     # App-level token, scope connections:write
export ALLOWED_USERS=U0123456789    # comma-separated Slack member IDs
python bot.py
```

There are no tests, no linter config, and no build step.

## Architecture

- **Socket Mode, not HTTP events.** The server dials Slack outbound over a
  WebSocket. This is deliberate: the host has no inbound reachability and no
  public URL, so any rework that requires a request URL / events endpoint
  (e.g. switching to `slack_bolt`'s HTTP adapter) breaks the deployment model.
  Keep the transport as Socket Mode unless the user explicitly changes the
  constraint.
- **Single event handler.** `app_mention` is the only subscribed event. The
  bot parses the text after the `<@BOTID>` prefix with `shlex.split`, then
  `subprocess.run`s it directly — there is no shell, no pipes, no redirection.
  Don't introduce `shell=True`; users who want shell features should wrap them
  server-side in an allowlisted script.
- **Two independent allowlists gate execution:** `ALLOWED_USERS` (env,
  per-deployment) and `ALLOWED_CMDS` (hard-coded set in `bot.py`,
  per-install). Both must be treated as load-bearing security controls, not
  ergonomic defaults — an empty `ALLOWED_CMDS` means "run anything" and should
  never be committed that way.
- **Output is truncated to `MAX_OUTPUT` (8000 chars)** before posting, to stay
  well under Slack's message size limit. If longer output is needed, upload as
  a file (`files_upload_v2`) rather than raising the cap.

## Deployment notes worth preserving

- Run as an unprivileged user. If specific commands need root, grant them via
  `sudoers` NOPASSWD for exact argv patterns rather than running the bot as
  root.
- Keep the bot in a single private channel. Anyone who can post in that
  channel *and* is in `ALLOWED_USERS` has RCE on the host.
