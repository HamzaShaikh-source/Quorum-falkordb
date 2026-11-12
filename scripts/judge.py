#!/usr/bin/env python
"""
Judge mode: the guided demo on your machine in one command, with no accounts and nothing hosted. Standard library only; needs Docker.

    python scripts/judge.py            # start, wait until ready, open the console and play the story
    python scripts/judge.py --stop     # stop it and remove its data

It starts FalkorDB, the hub and the console as their own Docker project (`quorum-judge`), on ports that are free on your machine, in
development mode with the guided poisoned-fact story switched on (sandbox tokens, sample agents). It never reads or writes the .env of
a real setup (python scripts/setup.py) and never touches another Quorum stack you may have running.
"""
import argparse
import importlib.util
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT = "quorum-judge"
STATE = ROOT / ".quorum" / "judge.json"


def free_port(start: int) -> int:
    for p in range(start, start + 200):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise SystemExit("No free port found near %d." % start)


def load_setup():
    spec = importlib.util.spec_from_file_location("quorum_setup", ROOT / "scripts" / "setup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def compose(args, env_file: str, override: str, **kw):
    cmd = ["docker", "compose", "--env-file", env_file, "-p", PROJECT, "-f", "docker-compose.yml", "-f", override, *args]
    return subprocess.run(cmd, cwd=ROOT, **kw)


def health(api: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{api}/api/health", timeout=3) as r:  # noqa: S310 - our own local hub
            body = json.loads(r.read().decode("utf8"))
            return body.get("status") == "ok" and body.get("demo") is True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Quorum judge mode")
    ap.add_argument("--stop", action="store_true", help="stop the judge stack and remove its data")
    ap.add_argument("--no-open", action="store_true", help="do not open a browser")
    a = ap.parse_args()
    setup = load_setup()
    if not setup.docker_ready():
        print("Docker is not running (or not installed). Start Docker Desktop, wait for it to settle, then run this again.")
        return 1

    tmp = Path(tempfile.gettempdir()) / "quorum-judge"
    tmp.mkdir(exist_ok=True)
    env_file = tmp / "empty.env"
    env_file.write_text("", encoding="utf8")  # an empty env file: never pick up a real setup's .env
    override = tmp / "override.yml"

    if a.stop:
        if override.exists():
            compose(["down", "-v"], str(env_file), str(override))
        else:
            subprocess.run(["docker", "compose", "-p", PROJECT, "down", "-v"], cwd=ROOT)
        STATE.unlink(missing_ok=True)
        print("Judge mode stopped and its data removed.")
        return 0

    api, ui = free_port(8000), free_port(5173)
    subnet = setup.free_subnet()
    override.write_text(
        f"""services:
  falkordb:
    container_name: !reset null
    ports: !override []
  mock-helpdesk:
    ports: !override []
  backend:
    ports: !override ["{api}:8000"]
  frontend:
    container_name: !reset null
    ports: !override ["{ui}:5173"]
networks:
  default:
    ipam:
      config: !override [{{subnet: "{subnet}"}}]
""",
        encoding="utf8",
    )
    env = {"QUORUM_DEMO": "1"}
    print("Starting the demo stack (the first run builds images; this can take a few minutes)...")
    import os

    r = compose(["up", "-d", "--build"], str(env_file), str(override), env={**os.environ, **env})
    if r.returncode != 0:
        print("docker compose failed. Scroll up for the reason.")
        return 1
    print("Waiting for the hub...")
    end = time.time() + 420
    while time.time() < end and not health(api):
        time.sleep(3)
    if not health(api):
        print("The hub did not become ready in time. Check: docker compose -p quorum-judge logs backend")
        return 1
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps({"api": api, "ui": ui}), encoding="utf8")
    url = f"http://127.0.0.1:{ui}/?play=1#/app"
    print(
        f"""
==============================================================
 Quorum judge mode is running.
   Console: {url}
   API:     http://127.0.0.1:{api}   (sandbox token: token_human)
==============================================================

What to look at, in two minutes:
  1. The story plays by itself (Autopilot): a partner feed claims 90 days, agents build a refund on it, the auditor quarantines the
     fact, everything built on it turns red by hop depth, and the correct policy replaces it. Click any node to see its provenance
     and the Cypher that ran.
  2. Team: "Connect with teammates" (hubs on your network, requests waiting for approval). Connect: the prompt that sets everything
     up from inside any AI chat.
  3. Check the graph-native claim yourself:
       curl -H "Authorization: Bearer token_human" http://127.0.0.1:{api}/api/falkordb/proof
     It runs the quarantine traversal in FalkorDB and in the API process and compares them.
  4. The evidence: docs/HACKATHON.md. Stop everything: python scripts/judge.py --stop
"""
    )
    if not a.no_open:
        webbrowser.open(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
