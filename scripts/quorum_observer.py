"""
Passive capture for Claude Code: a hook that sends what the assistant wrote to Quorum, so facts land in shared memory
without the agent calling any tool.

Add to .claude/settings.json (project) or ~/.claude/settings.json (everywhere):

  {"hooks": {"Stop":       [{"hooks": [{"type": "command", "command": "python /path/to/iFone/scripts/quorum_observer.py"}]}],
             "PreCompact": [{"hooks": [{"type": "command", "command": "python /path/to/iFone/scripts/quorum_observer.py"}]}]}}

  QUORUM_URL     the hub, default http://localhost:8000
  QUORUM_TOKEN   an agent or human token (a member token from the Team page is best)
  QUORUM_OBSERVE_TOOL_RESULTS=1   also read tool results (off by default: they are often code and logs)

It reads only the part of the transcript added since the last run, sends it to POST /api/observe, and never blocks or
fails the session: any error is swallowed. Obvious credentials are removed here and again on the server.
Try it without writing anything:  python scripts/quorum_observer.py --dry-run --transcript path/to/session.jsonl
"""
import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{12,}|qrm_[a-z0-9_-]{6,}|AKIA[0-9A-Z]{12,}|ghp_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{20,}|bearer\s+[A-Za-z0-9._~+/-]{16,})", re.I)


def blocks(msg: dict, include_results: bool):
    content = (msg.get("message") or {}).get("content")
    if isinstance(content, str):
        if msg.get("type") == "assistant":
            yield content
        return
    for b in content or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and msg.get("type") == "assistant":
            yield b.get("text", "")
        elif include_results and b.get("type") == "tool_result":
            c = b.get("content")
            if isinstance(c, str):
                yield c
            elif isinstance(c, list):
                for x in c:
                    if isinstance(x, dict) and x.get("type") == "text":
                        yield x.get("text", "")


def new_text(path: Path, start: int, include_results: bool):
    """Text from the transcript after byte offset `start`, and the new offset."""
    size = path.stat().st_size
    if start > size:
        start = 0  # the transcript was rewritten (compaction): read it all
    with path.open("rb") as f:
        f.seek(start)
        data = f.read()
    # only whole lines; a half-written last line is picked up next time
    cut = data.rfind(b"\n") + 1
    parts = []
    for line in data[:cut].splitlines():
        try:
            msg = json.loads(line)
        except Exception:
            continue
        parts.extend(blocks(msg, include_results))
    return SECRET.sub("[redacted]", "\n".join(p for p in parts if p)), start + cut


def post(url: str, token: str, text: str, source: str, dry: bool):
    body = json.dumps({"text": text[:60000], "source": source, "dry_run": dry}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/api/observe", data=body, headers={"content-type": "application/json", "authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-start", action="store_true", help="ignore the saved offset")
    a = ap.parse_args()
    try:
        payload = {}
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
        tpath = a.transcript or payload.get("transcript_path")
        token = os.getenv("QUORUM_TOKEN", "")
        if not tpath or not token:
            return 0
        path = Path(tpath)
        sid = payload.get("session_id") or path.stem
        state = Path(tempfile.gettempdir()) / f"quorum-observer-{re.sub(r'[^A-Za-z0-9_-]', '', sid)}.offset"
        start = 0 if a.from_start or not state.exists() else int(state.read_text() or 0)
        text, end = new_text(path, start, os.getenv("QUORUM_OBSERVE_TOOL_RESULTS") == "1")
        if text.strip():
            out = post(os.getenv("QUORUM_URL", "http://localhost:8000"), token, text, "claude-code", a.dry_run)
            if a.dry_run or os.getenv("QUORUM_OBSERVER_VERBOSE"):
                print(json.dumps({k: out.get(k) for k in ("candidates", "stored", "facts", "duplicates", "conflicts")}, indent=2), file=sys.stderr if not a.dry_run else sys.stdout)
        if not a.dry_run:
            state.write_text(str(end))
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        if os.getenv("QUORUM_OBSERVER_VERBOSE") or a.dry_run:
            print(f"quorum-observer: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
