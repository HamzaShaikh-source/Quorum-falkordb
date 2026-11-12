"""
Collision warnings for Claude Code: before an agent edits a file, ask Quorum whether another agent announced that file (or the
folder around it) in the last half hour, and say so.

Add to .claude/settings.json (project) or ~/.claude/settings.json:

  {"hooks": {"PreToolUse": [{"matcher": "Edit|Write|MultiEdit",
                             "hooks": [{"type": "command", "command": "python /path/to/scripts/quorum_conflict_hook.py"}]}]}}

  QUORUM_URL            the hub, default http://localhost:8000
  QUORUM_TOKEN          an agent token (a member token from the Team page is best)
  QUORUM_CONFLICT_MODE  warn (default: the edit goes ahead and the agent is told), ask (Claude Code asks the person first), off

The same announcement is what the `announce_work` MCP tool makes, so agents using either see each other. The hook never blocks
the session by itself: if the hub is down, slow or the call fails for any reason, the edit simply proceeds. It sends only the file
path, never file contents.
"""
import json
import os
import sys
import urllib.error
import urllib.request


def build_output(resp: dict, mode: str) -> dict:
    """What to print for Claude Code, given the hub's answer. An empty dict means: say nothing, let the edit go ahead."""
    if mode == "off" or not isinstance(resp, dict) or resp.get("clear") is not False:
        return {}
    msg = str(resp.get("text") or "Another agent is working on this.")
    if mode == "ask":
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": msg}}
    return {"systemMessage": msg, "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "permissionDecisionReason": msg}}


def announce(url: str, token: str, target: str, intent: str, timeout: float = 2.0) -> dict:
    req = urllib.request.Request(
        url.rstrip("/") + "/api/work/announce",
        data=json.dumps({"target": target, "intent": intent}).encode("utf8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - the person's own hub
        return json.loads(r.read().decode("utf8"))


def main() -> int:
    mode = os.environ.get("QUORUM_CONFLICT_MODE", "warn").lower()
    token = os.environ.get("QUORUM_TOKEN", "")
    if mode == "off" or not token:
        return 0
    try:
        event = json.loads(sys.stdin.read() or "{}")
        path = (event.get("tool_input") or {}).get("file_path") or (event.get("tool_input") or {}).get("path") or ""
        if not path:
            return 0
        out = build_output(announce(os.environ.get("QUORUM_URL", "http://localhost:8000"), token, str(path), f"{event.get('tool_name', 'edit')} by a Claude Code session"), mode)
        if out:
            print(json.dumps(out))
    except Exception:  # noqa: BLE001 - never block or fail the session
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
