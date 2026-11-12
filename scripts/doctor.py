#!/usr/bin/env python
"""
Quorum doctor: checks everything the product needs and says exactly how to fix what is missing.

    python scripts/doctor.py            # check
    python scripts/doctor.py --json     # machine-readable

Exit code 0 means FalkorDB is ready (the one hard requirement). Other problems are reported but do not fail the run.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

FALKOR_URL = os.getenv("QUORUM_FALKORDB_URL") or (
    f"redis://{os.environ['FALKORDB_HOST']}:{os.getenv('FALKORDB_PORT', '6379')}" if os.getenv("FALKORDB_HOST") else "redis://localhost:6379"
)
API = os.getenv("QUORUM_API_URL", "http://127.0.0.1:8000")

results = []


def check(name, ok, detail="", fix="", required=False):
    results.append({"name": name, "ok": bool(ok), "detail": detail, "fix": fix, "required": required})


def port_open(host, port, timeout=1.0):
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def http_json(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf8"))
        except Exception:
            return e.code, None
    except Exception:
        return None, None


def main():
    v = sys.version_info
    check("Python 3.10+", v >= (3, 10), f"{v.major}.{v.minor}.{v.micro}", "Install Python 3.10 or newer")

    for mod, pkg in (("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("falkordb", "falkordb"), ("redis", "redis"), ("pydantic", "pydantic")):
        try:
            __import__(mod)
            check(f"Python package {pkg}", True)
        except Exception:
            check(f"Python package {pkg}", False, "not installed", "pip install -r backend/requirements.txt")
    docker = shutil.which("docker")
    if docker:
        r = subprocess.run([docker, "info"], capture_output=True, text=True, timeout=20)
        check("Docker engine running", r.returncode == 0, "" if r.returncode == 0 else "docker is installed but the engine is not running", "Start Docker Desktop, then run this again")
    else:
        check("Docker installed", False, "not found", "Install Docker Desktop, or run FalkorDB another way and set QUORUM_FALKORDB_URL")

    u = urlparse(FALKOR_URL)
    host, port = u.hostname or "localhost", u.port or 6379
    if not port_open(host, port):
        check("FalkorDB reachable", False, f"nothing is listening on {host}:{port}", "docker compose up -d falkordb", required=True)
    else:
        try:
            from falkordb import FalkorDB

            db = FalkorDB(host=host, port=port, username=u.username, password=u.password, socket_connect_timeout=2)
            db.connection.ping()
            mods = db.connection.execute_command("MODULE", "LIST")
            names = [(m.get("name") if isinstance(m, dict) else m[1]) for m in mods]
            names = [n.decode() if isinstance(n, bytes) else str(n) for n in names]
            if "graph" in names:
                check("FalkorDB reachable and graph module loaded", True, f"{host}:{port}", required=True)
            else:
                check("FalkorDB graph module loaded", False, "this is plain Redis, not FalkorDB", "Use the image falkordb/falkordb (docker compose up -d falkordb)", required=True)
        except Exception as e:
            check("FalkorDB reachable", False, f"{type(e).__name__}: {e}"[:200], "docker compose up -d falkordb", required=True)

    code, health = http_json(f"{API}/api/health")
    if code is None:
        check("Backend running", False, f"no answer from {API}", "QUORUM_ENV=development QUORUM_EMBED_MCP=1 python -m uvicorn app.main:app --app-dir backend --port 8000")
    else:
        ok = code == 200 and health and health.get("status") == "ok"
        check("Backend sees FalkorDB", ok, "" if ok else (health or {}).get("falkordb", {}).get("error", f"HTTP {code}"), "Restart the backend after FalkorDB is up")
        if health and health.get("warnings"):
            check("Backend configuration", False, "; ".join(health["warnings"]), "See docs/SETUP.md, Production")
        try:
            req = urllib.request.Request(
                f"{API}/mcp",
                data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "doctor", "version": "1"}}}).encode(),
                headers={"content-type": "application/json", "accept": "application/json, text/event-stream"},
            )
            with urllib.request.urlopen(req, timeout=4) as r:
                check("MCP endpoint answers", r.status == 200, f"{API}/mcp")
        except urllib.error.HTTPError as e:
            if e.code == 401:  # it answered, and it wants a token: that is a hub with header auth required
                check("MCP endpoint answers", True, f"{API}/mcp (requires an Authorization header)")
            else:
                check("MCP endpoint answers", False, f"HTTP {e.code}", "See the backend log")
        except Exception as e:
            check("MCP endpoint answers", False, f"{type(e).__name__}", "Start the backend with QUORUM_EMBED_MCP=1 so agents and the console share one graph")
            try:
                import mcp.server.mcpserver  # noqa: F401
            except Exception:
                check("MCP SDK 2.x on this machine (needed to run MCP outside Docker)", False, "missing or too old", "pip install \"mcp>=2.0.0,<3\"")

    if "--json" in sys.argv:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            mark = "OK  " if r["ok"] else ("FAIL" if r["required"] else "WARN")
            line = f"[{mark}] {r['name']}" + (f"  ({r['detail']})" if r["detail"] else "")
            print(line)
            if not r["ok"] and r["fix"]:
                print(f"       fix: {r['fix']}")
    blocking = [r for r in results if r["required"] and not r["ok"]]
    if blocking:
        print("\nFalkorDB is not ready, so Quorum will not run. Fix the FAIL lines above, then run this again.")
        sys.exit(1)
    print("\nFalkorDB is ready.")


if __name__ == "__main__":
    main()
