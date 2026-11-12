"""The LAN beacon: it hears other hubs, ignores forgeries, and reports only what it heard to its own hub."""
import importlib.util
import socket
import threading
from pathlib import Path

spec = importlib.util.spec_from_file_location("quorum_beacon", Path(__file__).resolve().parents[2] / "scripts" / "quorum_beacon.py")
beacon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(beacon)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_a_message_is_accepted_only_from_the_machine_it_names():
    ok = beacon.make_message("hub_a", "Maya Hub", "http://192.168.1.20:8000")
    assert beacon.parse_message(ok, "192.168.1.20") == {"hub_id": "hub_a", "name": "Maya Hub", "url": "http://192.168.1.20:8000"}
    assert beacon.parse_message(ok, "192.168.1.99") is None, "address must equal the sender (no pointing a hub at someone else)"
    assert beacon.parse_message(ok, "192.168.1.20", own_hub_id="hub_a") is None, "own echo is ignored"
    for bad in (b"not json", b"{}", b'{"quorum":2}', b'{"quorum":1,"hub_id":"x","name":"n","url":"ftp://1.2.3.4"}', b"x" * 5000):
        assert beacon.parse_message(bad, "1.2.3.4") is None


def test_two_beacons_find_each_other_and_report_to_their_own_hub(monkeypatch):
    p1, p2 = _free_port(), _free_port()
    calls = []

    def fake_call(method, url, token="", body=None, timeout=4.0):
        if url.endswith("/api/connect/hello"):
            hid = "hub_one" if ":1111" in url else "hub_two"
            return 200, {"quorum": 1, "hub_id": hid, "name": "One" if hid == "hub_one" else "Two", "accepting": True}
        calls.append((url, token, body))
        return 200, {"accepted": len((body or {}).get("peers", []))}

    monkeypatch.setattr(beacon, "_call", fake_call)
    b1 = beacon.Beacon("http://127.0.0.1:1111", "http://127.0.0.1:8001", "tok1", p1, [("127.0.0.1", p2)])
    b2 = beacon.Beacon("http://127.0.0.1:2222", "http://127.0.0.1:8002", "tok2", p2, [("127.0.0.1", p1)])
    try:
        assert b1.identify() and b2.identify()
        t = threading.Thread(target=lambda: (b1.listen_for(1.5)), daemon=True)
        t2 = threading.Thread(target=lambda: (b2.listen_for(1.5)), daemon=True)
        t.start()
        t2.start()
        b1.send_once()
        b2.send_once()
        t.join()
        t2.join()
        assert [v["name"] for v in b1.peers.values()] == ["Two"] and [v["name"] for v in b2.peers.values()] == ["One"]
        assert b1.report() == 1
        url, token, body = calls[-1]
        assert url.endswith("/api/peers/announce") and token == "tok1"
        assert body == {"peers": [{"hub_id": "hub_two", "name": "Two", "url": "http://127.0.0.1:8002"}]}
        assert all("token" not in str(v) for v in b1.peers.values())
    finally:
        b1.sock.close()
        b2.sock.close()
