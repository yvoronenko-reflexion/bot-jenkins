"""
Slack-as-proxy command runner.

Usage in Slack:  DM the bot:      uptime
                 or @mention:     @your-bot uptime

Server needs only OUTBOUND access to slack.com (no inbound port, no public URL).
"""
import re
import shlex
import subprocess

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import config

_cfg = config.load()
BOT_TOKEN = _cfg["slack"]["bot_token"]   # xoxb-...
APP_TOKEN = _cfg["slack"]["app_token"]   # xapp-... (Socket Mode token)

# SECURITY: restrict who can run commands. Slack member IDs from config.
# Set allowed_users = "*" in config.toml to allow any Slack user.
_raw_allowed = _cfg["slack"].get("allowed_users", [])
ALLOWED_USERS = None if _raw_allowed == "*" else set(_raw_allowed)

# SECURITY: allowlist of commands. Empty set = allow anything (DANGEROUS).
ALLOWED_CMDS = {"uptime", "df", "free", "ps", "journalctl", "helix", "jk"}

TIMEOUT_SEC = 30
MAX_OUTPUT = 8000  # Slack message cap is ~40k; leave headroom

app = App(token=BOT_TOKEN)

# Map jk's status tokens to display emoji and a severity rank (lower = worse).
# Rank is used to pick the Slack attachment color bar based on the worst row.
_JK_STATUS = {
    "FAILURE":      ("❌", 0, "danger"),
    "FAILED":       ("❌", 0, "danger"),
    "UNSTABLE":     ("⚠️", 1, "warning"),
    "ABORTED":      ("⚫", 2, "#a64ca6"),
    "RUNNING":      ("🟡", 3, "#439fe0"),
    "BUILDING":     ("🟡", 3, "#439fe0"),
    "IN_PROGRESS":  ("🟡", 3, "#439fe0"),
    "WAITING":      ("🔵", 4, "#439fe0"),
    "SUCCESS":      ("✅", 5, "good"),
    "SKIPPED":      ("⚪", 6, "#888888"),
    "NOT_BUILT":    ("⚪", 6, "#888888"),
    "NOT_EXECUTED": ("⚪", 6, "#888888"),
}
_JK_STATUS_RE = re.compile(r"\b(" + "|".join(_JK_STATUS) + r")\b")
_URL_RE = re.compile(r"https?://\S+")


def _render_jk(output: str, returncode: int) -> dict:
    """Turn jk's plaintext output into Slack `say()` kwargs with a color bar."""
    lines = output.rstrip().splitlines() or ["(no output)"]
    title = None
    title_link = None
    body = []
    worst_rank = None
    worst_color = "#888888"

    for line in lines:
        m = _JK_STATUS_RE.search(line)
        if m:
            emoji, rank, color = _JK_STATUS[m.group(1)]
            body.append(f"{emoji} {line}")
            if worst_rank is None or rank < worst_rank:
                worst_rank, worst_color = rank, color
            continue

        # Header lines: "PR #N: ..." or a bare/labeled URL.
        if title is None and line.startswith("PR #"):
            title = line
            continue
        url_match = _URL_RE.search(line)
        if url_match and title_link is None:
            title_link = url_match.group(0)
            label = line.replace(title_link, "").strip(" :") or None
            if title is None:
                title = label or "Open in Jenkins"
            continue

        body.append(line)

    attachment = {
        "color": worst_color,
        "text": "```\n" + "\n".join(body) + "\n```" if body else "",
        "mrkdwn_in": ["text"],
    }
    if title:
        attachment["title"] = title
    if title_link:
        attachment["title_link"] = title_link
    if returncode != 0:
        attachment["footer"] = f"exit={returncode}"

    return {"text": title or "jk output", "attachments": [attachment]}


def run_command(user: str, text: str, say, client) -> None:
    if ALLOWED_USERS is not None and user not in ALLOWED_USERS:
        say(f"<@{user}> not authorized.")
        return

    try:
        argv = shlex.split(text)
    except ValueError as e:
        say(f"parse error: {e}")
        return
    if not argv:
        say("empty command")
        return
    if ALLOWED_CMDS and argv[0] not in ALLOWED_CMDS:
        say(f"`{argv[0]}` not in allowlist")
        return

    ack = say(f"Running `{' '.join(argv)}`…")

    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
        )
        output = (result.stdout + result.stderr)[:MAX_OUTPUT] or "(no output)"
        if argv[0] == "jk":
            kwargs = _render_jk(output, result.returncode)
        else:
            kwargs = {"text": f"exit={result.returncode}\n```{output}```"}
    except subprocess.TimeoutExpired:
        kwargs = {"text": f"timed out after {TIMEOUT_SEC}s"}

    client.chat_update(channel=ack["channel"], ts=ack["ts"], **kwargs)


@app.event("app_mention")
def handle_mention(event, say, client):
    text = event.get("text", "")
    # Strip the "<@BOTID> " prefix
    cmd_str = text.split(">", 1)[1].strip() if ">" in text else text.strip()
    run_command(event.get("user", ""), cmd_str, say, client)


@app.event("message")
def handle_dm(event, say, client):
    # Only respond in direct-message channels; ignore channel messages and
    # our own echoes / edits / other subtypes.
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id") or event.get("subtype"):
        return
    run_command(event.get("user", ""), event.get("text", "").strip(), say, client)


if __name__ == "__main__":
    SocketModeHandler(app, APP_TOKEN).start()
