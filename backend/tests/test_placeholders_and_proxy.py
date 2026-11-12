"""Example credentials must never work, and only the pinned proxy may vouch for a client's address."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.config as cfg
from app.main import _client_ip, _is_trusted_proxy

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"


def boot(**env):
    """Imports the config in a fresh interpreter with this environment; returns (exit code, stderr)."""
    base = {k: v for k, v in os.environ.items() if not k.startswith("QUORUM_")}
    base.update({"QUORUM_ENV": "production", **env})
    r = subprocess.run([sys.executable, "-c", "import app.config"], cwd=BACKEND, env={**base, "PYTHONPATH": str(BACKEND)}, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stderr


def tokens(tok):
    return json.dumps({tok: {"tenant": "acme", "role": "human", "agent_id": "ops"}})


@pytest.mark.parametrize("ph", ["token_secret_here", "qtk_replace_me", "change-me-to-a-strong-password"])
def test_the_hub_refuses_to_start_in_production_with_an_example_token(ph):
    code, err = boot(QUORUM_TOKENS_JSON=tokens(ph))
    assert code != 0 and "placeholder" in err


def test_it_refuses_an_example_database_password():
    code, err = boot(QUORUM_TOKENS_JSON=tokens("qtk_" + "a" * 40), QUORUM_FALKORDB_URL="redis://:change-me-to-a-long-random-string@falkordb:6379")
    assert code != 0 and "placeholder" in err


def test_real_values_start_fine_and_development_is_not_policed():
    assert boot(QUORUM_TOKENS_JSON=tokens("qtk_" + "a" * 40), QUORUM_FALKORDB_URL="redis://:S3cure-and-random@falkordb:6379")[0] == 0
    assert boot(QUORUM_ENV="development", QUORUM_TOKENS_JSON=tokens("token_secret_here"))[0] == 0


def test_the_shipped_example_has_no_credential_in_it():
    text = (ROOT / ".env.production.example").read_text(encoding="utf8")
    values = dict(line.split("=", 1) for line in text.splitlines() if line and not line.startswith("#") and "=" in line)
    assert values["FALKORDB_PASSWORD"] == "" and values["QUORUM_TOKENS_JSON"] == ""
    assert not any(p in text for p in cfg._PLACEHOLDERS[:4])


# ---------- who may vouch for a client address ----------
def req(peer, **headers):
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers={k.replace("_", "-").lower(): v for k, v in headers.items()})


def compose_default():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf8")
    return re.search(r"QUORUM_TRUSTED_PROXIES=\$\{QUORUM_TRUSTED_PROXIES:-([^}]*)\}", text).group(1)


def test_the_compose_default_trusts_only_the_pinned_proxy(monkeypatch):
    default = compose_default()
    pinned = re.search(r"ipv4_address:\s*(\S+)", (ROOT / "docker-compose.yml").read_text(encoding="utf8")).group(1)
    assert pinned in default.split(",") and "/" not in default, "no private range may be trusted"
    monkeypatch.setattr(cfg.settings, "TRUSTED_PROXIES", default)
    assert _is_trusted_proxy(pinned)
    for stranger in ("172.29.77.1", "172.17.0.1", "10.1.2.3", "192.168.1.9", "203.0.113.7"):  # gateway, other containers, LAN, internet
        assert not _is_trusted_proxy(stranger)


def test_a_client_on_the_published_port_cannot_spoof_its_address(monkeypatch):
    monkeypatch.setattr(cfg.settings, "TRUSTED_PROXIES", compose_default())
    assert _client_ip(req("172.29.77.1", X_Forwarded_For="6.6.6.6", X_Real_IP="7.7.7.7")) == "172.29.77.1"
    assert _client_ip(req("172.29.77.10", X_Forwarded_For="198.51.100.4, 172.29.77.10")) == "198.51.100.4"
    assert _client_ip(req("172.29.77.10", X_Real_IP="198.51.100.5")) == "198.51.100.5"
    assert _client_ip(req("172.29.77.10")) == "172.29.77.10"


def test_production_override_uses_the_same_pinned_proxy():
    prod = (ROOT / "docker-compose.production.yml").read_text(encoding="utf8")
    assert compose_default() in prod and "172.16.0.0/12" not in prod


def test_the_proxy_really_is_pinned_inside_the_network_the_compose_file_defines():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf8")
    subnet = re.search(r"subnet:\s*(\S+)", text).group(1)
    import ipaddress

    assert ipaddress.ip_address(re.search(r"ipv4_address:\s*(\S+)", text).group(1)) in ipaddress.ip_network(subnet)
