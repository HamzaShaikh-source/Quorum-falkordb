#!/usr/bin/env python
"""
One command from a fresh download to a working Quorum hub. Standard library only; needs Docker.

    python scripts/setup.py                       # or double-click setup.bat / run ./setup.sh
    python scripts/setup.py --name Maya --team "Support Desk"

What it does, in order (safe to run again; it reuses what it made):
  1. checks Docker is running
  2. writes .env with strong random secrets (FalkorDB password, your admin token) the first time
  3. starts FalkorDB + the hub + the console with production rules, reachable on your local network
  4. starts the LAN beacon so teammates running Quorum can find you (and you them)
  5. writes AI_SETUP_PROMPT.txt, prints it, and copies it to your clipboard

After that, paste the prompt into your AI (Claude, Cursor, VS Code, Antigravity, Windsurf, ...). It connects itself, shows you
every command, runs a short survey, looks for teammates and shows what is remembered. You do not need the console.
"""
import argparse
import getpass
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
STATE = ROOT / ".quorum"
FILES = ["-f", "docker-compose.yml", "-f", "docker-compose.production.yml", "-f", "docker-compose.team.yml"]
API_PORT = int(os.environ.get("QUORUM_API_PORT") or 8000)
UI_PORT = int(os.environ.get("QUORUM_UI_PORT") or 5173)
API = f"http://127.0.0.1:{API_PORT}"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT, **kw)


def lan_ip() -> str:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def slug(s: str) -> str:
    t = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return (t or "team")[:30]


def read_env() -> dict:
    out = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("'").strip('"')
    return out


def _quote(v: str) -> str:
    """A value docker compose reads back exactly: bare if plain, single quotes if it has spaces or double quotes, double quotes if it has an apostrophe."""
    if re.fullmatch(r"[A-Za-z0-9_./:@+,=-]*", v):
        return v
    if "'" not in v:
        return f"'{v}'"
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$") + '"'


def write_env(name: str, team: str, public_url: str) -> dict:
    tenant = slug(team)
    admin = f"qtk_{secrets.token_urlsafe(32)}"
    env = {
        "QUORUM_ENV": "production",
        "QUORUM_DEMO": "0",
        "QUORUM_DEFAULT_TENANT": tenant,
        "QUORUM_HUB_NAME": f"{name}'s Quorum",
        "QUORUM_PUBLIC_URL": public_url,
        "FALKORDB_PASSWORD": secrets.token_urlsafe(24),
        "QUORUM_ADMIN_TOKEN": admin,
        "QUORUM_TOKENS_JSON": json.dumps({admin: {"tenant": tenant, "role": "human", "agent_id": name}}, separators=(",", ":")),
        "QUORUM_MEMBER_TOKEN_TTL_DAYS": "90",
        "QUORUM_SUBNET": free_subnet(),
        "QUORUM_API_PORT": str(API_PORT),
        "QUORUM_UI_PORT": str(UI_PORT),
    }
    lines = ["# Written by scripts/setup.py. Contains secrets: never commit or share this file."]
    for k, v in env.items():
        lines.append(f"{k}={_quote(v)}")
    text = chr(10).join(lines) + chr(10)  # LF endings: docker compose reads this file the same way on every OS
    ENV.write_bytes(text.encode("utf8"))
    try:
        os.chmod(ENV, 0o600)
    except OSError:
        pass
    return env


def used_subnets() -> list:
    """Address ranges of the Docker networks that already exist (a second hub, a VPN), so ours can avoid them."""
    import ipaddress

    try:
        ids = run(["docker", "network", "ls", "-q"], capture_output=True, text=True).stdout.split()
        if not ids:
            return []
        raw = run(["docker", "network", "inspect", *ids, "--format", "{{range .IPAM.Config}}{{.Subnet}} {{end}}"], capture_output=True, text=True).stdout
    except (FileNotFoundError, OSError):
        return []
    out = []
    for tok in raw.split():
        try:
            out.append(ipaddress.ip_network(tok, strict=False))
        except ValueError:
            continue
    return out


def free_subnet() -> str:
    import ipaddress

    taken = used_subnets()
    for n in [77] + list(range(80, 250)):
        cand = ipaddress.ip_network(f"172.29.{n}.0/24")
        if not any(cand.overlaps(t) for t in taken):
            return str(cand)
    return "172.29.77.0/24"


def docker_ready() -> bool:
    try:
        return run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except FileNotFoundError:
        return False


def http(method: str, path: str, token: str = "", body=None, timeout: float = 5.0):
    data = json.dumps(body).encode("utf8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - our own local hub
            return r.status, json.loads(r.read().decode("utf8") or "null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


def wait_healthy(seconds: int = 420) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        code, body = http("GET", "/api/health")
        if code == 200 and isinstance(body, dict) and body.get("status") == "ok":
            return True
        time.sleep(3)
    return False


def start_beacon(admin_token: str) -> str:
    STATE.mkdir(exist_ok=True)
    pidfile = STATE / "beacon.pid"
    if pidfile.exists():
        try:
            old = int(pidfile.read_text())
            if sys.platform == "win32":
                alive = subprocess.run(["tasklist", "/FI", f"PID eq {old}"], capture_output=True, text=True).stdout.count(str(old)) > 0
            else:
                os.kill(old, 0)
                alive = True
            if alive:
                return f"already running (pid {old})"
        except (OSError, ValueError):
            pass
    log = open(STATE / "beacon.log", "ab")
    cmd = [sys.executable, str(ROOT / "scripts" / "quorum_beacon.py"), "--hub", API]
    env = {**os.environ, "QUORUM_ADMIN_TOKEN": admin_token}
    kw = {"stdout": log, "stderr": log, "stdin": subprocess.DEVNULL, "env": env, "cwd": ROOT}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen(cmd, **kw)
    pidfile.write_text(str(p.pid))
    return f"started (pid {p.pid})"


PROMPT = """\
You are setting up Quorum, my shared memory for AI agents and teammates. Do these steps in order and keep your replies short.

1. Connect yourself to my Quorum hub (an MCP server over HTTP):
   name: quorum
   url: {mcp}
   header: Authorization: Bearer {token}
   - If you are Claude Code, run: claude mcp add --transport http quorum {mcp} --header "Authorization: Bearer {token}"
   - If you can edit your own MCP settings, add it. If you cannot, tell me the exact one thing I must do and wait.
2. Call quorum_help and show me every command with what it does, in plain words.
3. Call quorum_start. Ask me its questions one at a time (team size, team name, what we use it for, whether to save facts from chats automatically, whether to look for teammates), then call quorum_start again with my answers.
4. Call quorum_discover and tell me who else on my network is running Quorum. If I name someone, call quorum_connect for them. When anyone asks to join me, tell me and ask whether to approve (quorum_inbox, quorum_respond) and at what level.
5. Call quorum_status and explain what is remembered, what will be remembered, and what is shared with whom.

From now on: before answering anything about our work, call retrieve_context with format=compact; save durable facts with remember_fact (or observe); if a fact turns out wrong, quarantine it instead of deleting. I manage teams and permission levels by asking you (quorum_manage), not in a console.
"""


def copy_to_clipboard(text: str) -> bool:
    try:
        if sys.platform == "win32":
            subprocess.run(["clip"], input=text.encode("ascii", "replace"), check=True)
        elif sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf8"), check=True)
        elif shutil.which("xclip"):
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode("utf8"), check=True)
        else:
            return False
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Set up Quorum")
    ap.add_argument("--name", default=getpass.getuser().replace(".", " ").title(), help="your name (shown to teammates)")
    ap.add_argument("--team", default="", help="team name (default: <name>'s team)")
    ap.add_argument("--public-url", default="", help="how other machines reach this hub (default: http://<this machine's LAN address>:8000)")
    ap.add_argument("--no-beacon", action="store_true", help="do not announce this hub on the network")
    ap.add_argument("--add-to-claude-code", action="store_true", help="also run 'claude mcp add' for you")
    ap.add_argument("--reset", action="store_true", help="write a fresh .env with new secrets (old tokens stop working)")
    a = ap.parse_args()

    say("Quorum setup")
    if not docker_ready():
        say("Docker is not running (or not installed). Install Docker Desktop, start it, wait for the whale icon to settle, then run this again.")
        return 1

    if ENV.exists() and not a.reset:
        env = read_env()
        say("Using your existing .env.")
    else:
        team = a.team or f"{a.name}'s team"
        env = write_env(a.name, team, a.public_url or f"http://{lan_ip()}:{API_PORT}")
        say(f"Wrote .env (team '{team}'). Keep it private: it holds your admin token.")

    say("Starting the hub (the first run builds images; this can take a few minutes)...")
    r = run(["docker", "compose", *FILES, "up", "-d", "--build"])
    if r.returncode != 0:
        say("docker compose failed. Scroll up for the reason; run `docker compose logs backend` for details.")
        return 1
    if not wait_healthy(90):
        say("Not answering yet; recreating the hub container once...")
        run(["docker", "compose", *FILES, "up", "-d", "--force-recreate", "backend"])
        if not wait_healthy():
            say("The hub did not become healthy in time. Check: docker compose logs backend")
            return 1

    token = env["QUORUM_ADMIN_TOKEN"]
    public = env.get("QUORUM_PUBLIC_URL") or f"http://{lan_ip()}:{API_PORT}"
    http("PUT", "/api/workspace", token, {"org": env.get("QUORUM_HUB_NAME", "Quorum").replace("'s Quorum", "'s team")})
    beacon = "off (--no-beacon)" if a.no_beacon else start_beacon(token)

    prompt = PROMPT.format(mcp=public.rstrip("/") + "/mcp", token=token)
    (ROOT / "AI_SETUP_PROMPT.txt").write_text(prompt, encoding="utf8")
    if a.add_to_claude_code and shutil.which("claude"):
        run(["claude", "mcp", "add", "--transport", "http", "quorum", public.rstrip("/") + "/mcp", "--header", f"Authorization: Bearer {token}"])
    copied = copy_to_clipboard(prompt)

    say()
    say("=" * 64)
    say(" Quorum is running.")
    say(f"  Hub (MCP + API): {public}      Console: {public.rsplit(':', 1)[0]}:{UI_PORT}  (sign in with your admin token)")
    say(f"  Teammate discovery: {beacon}")
    say("=" * 64)
    say()
    say("NEXT, paste this into your AI (Claude, Cursor, VS Code, Antigravity, Windsurf, ...):")
    say()
    say(prompt)
    say(("It is already on your clipboard. " if copied else "") + "It is also saved as AI_SETUP_PROMPT.txt (contains your token: do not share it).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
