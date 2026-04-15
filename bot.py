"""
Slack-as-proxy command runner.

Usage in Slack:  DM the bot:      uptime
                 or @mention:     @your-bot uptime

Server needs only OUTBOUND access to slack.com (no inbound port, no public URL).
"""
import logging
import re
import shlex
import subprocess
import sys

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import config

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

_cfg = config.load()
BOT_TOKEN = _cfg["slack"]["bot_token"]   # xoxb-...
APP_TOKEN = _cfg["slack"]["app_token"]   # xapp-... (Socket Mode token)

# SECURITY: restrict who can run commands. Slack member IDs from config.
# Set allowed_users = "*" in config.toml to allow any Slack user.
_raw_allowed = _cfg["slack"].get("allowed_users", [])
ALLOWED_USERS = None if _raw_allowed == "*" else set(_raw_allowed)

LONG_OUTPUT = _cfg["slack"].get("long_output", "chunk")  # "chunk" | "snippet"

# SECURITY: allowlist of commands. Empty set = allow anything (DANGEROUS).
ALLOWED_CMDS = {"uptime", "df", "free", "ps", "journalctl", "helix", "jk", "help"}

TIMEOUT_SEC = 30
MAX_OUTPUT = 2900    # Slack attachment text field cap; leave headroom
MAX_CAPTURE = 200_000  # safety cap on subprocess output to avoid OOM

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


def _split_chunks(text: str, size: int) -> list[str]:
    """Split *text* into chunks of at most *size* chars, breaking on newlines."""
    chunks = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        if cut == -1:
            cut = size  # no newline in window — fall back to hard cut
        else:
            cut += 1    # include the newline in the preceding chunk
        chunks.append(text[:cut])
        text = text[cut:]
    if text:
        chunks.append(text)
    return chunks


def run_command(user: str, text: str, say, client) -> None:
    if ALLOWED_USERS is not None and user not in ALLOWED_USERS:
        log.warning("unauthorized user=%s attempted: %s", user, text)
        say(f"<@{user}> not authorized.")
        return

    try:
        argv = shlex.split(text)
    except ValueError as e:
        log.warning("user=%s parse error: %s", user, e)
        say(f"parse error: {e}")
        return
    if not argv:
        say("empty command")
        return
    if ALLOWED_CMDS and argv[0] not in ALLOWED_CMDS:
        log.warning("user=%s blocked command not in allowlist: %s", user, argv[0])
        say(f"`{argv[0]}` not in allowlist")
        return

    if argv[0] == "help":
        cmds = sorted(ALLOWED_CMDS - {"help"})
        say(
            "*Usage:* `@bot <command> [args]` or DM the bot directly.\n"
            f"*Available commands:* {', '.join(f'`{c}`' for c in cmds)}\n"
            f"*Timeout:* {TIMEOUT_SEC}s"
        )
        return

    cmd_str = " ".join(argv)
    log.info("user=%s running: %s", user, cmd_str)
    ack = say(f"Running `{cmd_str}`…")

    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
        )
        raw = re.sub(r"\x1b\[[0-9;]*[mABCDEFGHJKSTfhilmnprsu]", "", (result.stdout + result.stderr)[:MAX_CAPTURE]) or "(no output)"
        log.info("user=%s cmd=%r exit=%d output_len=%d", user, cmd_str, result.returncode, len(raw))
        exit_str = "" if result.returncode == 0 else f"exit={result.returncode}"
        if len(raw) > MAX_OUTPUT:
            if LONG_OUTPUT == "snippet":
                log.info("user=%s uploading output as file snippet (%d bytes)", user, len(raw))
                client.chat_update(
                    channel=ack["channel"], ts=ack["ts"],
                    text=f"{exit_str + ' — ' if exit_str else ''}uploading output as file…",
                )
                client.files_upload_v2(
                    channel=ack["channel"],
                    content=raw,
                    filename="output.txt",
                    initial_comment=f"`{cmd_str}`{' → ' + exit_str if exit_str else ''}",
                )
            else:  # chunk
                chunks = _split_chunks(raw, MAX_OUTPUT)
                log.info("user=%s sending output in %d chunks", user, len(chunks))
                client.chat_update(
                    channel=ack["channel"], ts=ack["ts"],
                    text=f"`{cmd_str}` — {len(chunks)} parts{' — ' + exit_str if exit_str else ''}",
                )
                for chunk in chunks:
                    client.chat_postMessage(
                        channel=ack["channel"],
                        text=f"```{chunk}```",
                    )
            return
        if argv[0] == "jk":
            kwargs = _render_jk(raw, result.returncode)
        else:
            kwargs = {"text": f"{exit_str + chr(10) if exit_str else ''}```{raw}```"}
    except subprocess.TimeoutExpired:
        log.warning("user=%s cmd=%r timed out after %ds", user, cmd_str, TIMEOUT_SEC)
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
    log.info("bot starting (long_output=%s, allowed_users=%s, allowed_cmds=%s)",
             LONG_OUTPUT,
             "any" if ALLOWED_USERS is None else len(ALLOWED_USERS),
             list(ALLOWED_CMDS) if ALLOWED_CMDS else "unrestricted")
    SocketModeHandler(app, APP_TOKEN).start()
