#!/usr/bin/env python
"""
Finds other Quorum hubs on the same network and lets them find yours. Standard library only.

    python scripts/quorum_beacon.py                      # reads .env, talks to the hub on this machine
    python scripts/quorum_beacon.py --hub http://127.0.0.1:8000 --advertise http://192.168.1.20:8000

It runs on the host (not in Docker) because a container cannot see the local network's broadcasts. Every few seconds it
  1. asks your hub who it is (GET /api/connect/hello) and broadcasts {name, address} to the network (UDP, port 47474);
  2. listens for the same message from other machines;
  3. tells your hub which ones it heard (POST /api/peers/announce, with your admin token).
The broadcast carries a name and an address, never a token. Nothing is joined without an admin approving it in the chat.
A message is ignored unless its address is the machine it came from, so one machine cannot point your hub at another.
"""
import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

PORT = 47474
MAX_PACKET = 1024
TTL_S = 45


def lan_ip() -> str:
    """The address other machines on this network can reach us at (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def local_ips() -> List[str]:
    """Every IPv4 address of this machine (Wi-Fi, Ethernet, VPN, virtual adapters), so we can announce from each one."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ips.add(lan_ip())
    return sorted(i for i in ips if not i.startswith("127.") and not i.startswith("169.254."))


def make_message(hub_id: str, name: str, url: str) -> bytes:
    return json.dumps({"quorum": 1, "hub_id": hub_id, "name": name, "url": url}, separators=(",", ":")).encode("utf8")


def parse_message(data: bytes, src_ip: str, own_hub_id: str = "") -> Optional[Dict[str, str]]:
    """A well-formed announcement from another hub, or None. The advertised host must be the sender's own address."""
    if len(data) > MAX_PACKET:
        return None
    try:
        m = json.loads(data.decode("utf8"))
        if m.get("quorum") != 1:
            return None
        hid, name, url = str(m["hub_id"])[:40], " ".join(str(m["name"]).split())[:60], str(m["url"])[:200]
        host = urlparse(url).hostname
    except (ValueError, KeyError, AttributeError, UnicodeDecodeError):
        return None
    if not hid or not name or hid == own_hub_id or urlparse(url).scheme not in ("http", "https") or not host:
        return None
    if host != src_ip and src_ip not in _resolve(host):
        return None  # the advertised address must be the machine the packet came from
    return {"hub_id": hid, "name": name, "url": url}


def _resolve(host: str) -> set:
    try:
        return {i[4][0] for i in socket.getaddrinfo(host, None, socket.AF_INET)}
    except OSError:
        return set()


def load_env(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _call(method: str, url: str, token: str = "", body: Optional[dict] = None, timeout: float = 4.0) -> Tuple[int, Any]:
    data = json.dumps(body).encode("utf8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - the hub URL is the person's own
            return r.status, json.loads(r.read().decode("utf8") or "null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


class Beacon:
    def __init__(self, hub: str, advertise: str, token: str, port: int = PORT, targets: Optional[List[Tuple[str, int]]] = None, interval: float = 4.0):
        self.hub, self.advertise, self.token, self.port, self.interval = hub.rstrip("/"), advertise.rstrip("/"), token, port, interval
        self.explicit_targets = bool(targets)
        self.targets = targets or [("255.255.255.255", port)]
        self.peers: Dict[str, Dict[str, Any]] = {}
        self.hub_id = ""
        self.name = ""
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self.warned = False
        self.sock = self._listen()

    def _listen(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(("", self.port))
        s.settimeout(0.5)
        return s

    def identify(self) -> bool:
        code, body = _call("GET", self.hub + "/api/connect/hello")
        if code == 200 and isinstance(body, dict) and body.get("hub_id"):
            self.hub_id, self.name = body["hub_id"], body.get("name") or "Quorum hub"
            return bool(body.get("accepting", True))
        return False

    def send_once(self) -> None:
        msg = make_message(self.hub_id, self.name, self.advertise)
        if self.explicit_targets:
            for t in self.targets:
                try:
                    self.sock.sendto(msg, t)
                except OSError:
                    pass
            return
        # One packet per network adapter, sent from that adapter's own address, so the receiver can check that the address we
        # advertise is the machine the packet came from (a plain broadcast leaves through whichever adapter the OS prefers).
        for ip in local_ips():
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.bind((ip, 0))
                for t in self.targets:
                    s.sendto(msg, t)
            except OSError:
                pass
            finally:
                s.close()

    def listen_for(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and not self.stop.is_set():
            try:
                data, (ip, _port) = self.sock.recvfrom(MAX_PACKET + 1)
            except socket.timeout:
                continue
            except OSError:
                return
            m = parse_message(data, ip, self.hub_id)
            if m:
                with self._lock:
                    self.peers[m["hub_id"]] = {**m, "seen": time.time()}

    def report(self) -> int:
        now = time.time()
        with self._lock:
            self.peers = {k: v for k, v in self.peers.items() if now - v["seen"] <= TTL_S}
            peers = [{k: v[k] for k in ("hub_id", "name", "url")} for v in self.peers.values()]
        code, body = _call("POST", self.hub + "/api/peers/announce", self.token, {"peers": peers})
        if code in (401, 403) and not self.warned:
            print("beacon: the hub refused the admin token; discovery is not reaching it.", file=sys.stderr)
            self.warned = True
        return body.get("accepted", 0) if code == 200 and isinstance(body, dict) else 0

    def run(self) -> None:
        while not self.stop.is_set():
            if self.identify():
                self.send_once()
            self.listen_for(self.interval)
            self.report()


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    env = {**load_env(root / ".env"), **os.environ}
    ap = argparse.ArgumentParser(description="Quorum LAN beacon")
    ap.add_argument("--hub", default="http://127.0.0.1:8000", help="your hub, as seen from this machine")
    ap.add_argument("--advertise", default=env.get("QUORUM_PUBLIC_URL") or f"http://{lan_ip()}:8000", help="the address other machines use to reach your hub")
    ap.add_argument("--token", default=env.get("QUORUM_ADMIN_TOKEN", ""), help="your admin token (default: QUORUM_ADMIN_TOKEN from .env)")
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args()
    if not a.token:
        print("No admin token. Run setup first (python scripts/setup.py) or pass --token.", file=sys.stderr)
        return 2
    b = Beacon(a.hub, a.advertise, a.token, a.port)
    print(f"beacon: advertising {a.advertise}, reporting to {a.hub}")
    try:
        b.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
