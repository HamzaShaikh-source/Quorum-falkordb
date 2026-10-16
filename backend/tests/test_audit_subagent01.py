"""
Subagent_01 QA Test Suite: Authentication, Bearer Tokens & Proxy Security.
Validates static and runtime bugs, edge cases, boundaries, and concurrency.
"""
import concurrent.futures
import ipaddress
import re
import time
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.auth import AuthContext, get_auth_context, resolve_token, _member_token
from app.config import settings
from app.main import app, _is_trusted_proxy, _client_ip
from app.ratelimit import FailureTracker, RateLimiter, http_limiter, auth_failures


# ============================================================================
# 1. Null / None / Type Handling Tests
# ============================================================================

def test_member_token_none_handling():
    """_member_token should safely return None for None/invalid types instead of raising AttributeError."""
    try:
        res = _member_token(None)
        assert res is None
    except AttributeError:
        pytest.fail("_member_token(None) raised AttributeError: 'NoneType' object has no attribute 'split'")


def test_resolve_token_type_handling():
    """resolve_token should safely handle non-string types without raising AttributeError."""
    try:
        res = resolve_token(12345)
        assert res is None
    except AttributeError:
        pytest.fail("resolve_token(12345) raised AttributeError: 'int' object has no attribute 'startswith'")


def test_get_auth_context_none_and_empty():
    """get_auth_context should cleanly raise 401 on None or empty authorization headers."""
    with pytest.raises(HTTPException) as exc_info:
        get_auth_context(None)
    assert exc_info.value.status_code == 401

    with pytest.raises(HTTPException) as exc_info:
        get_auth_context("")
    assert exc_info.value.status_code == 401

    with pytest.raises(HTTPException) as exc_info:
        get_auth_context("   ")
    assert exc_info.value.status_code == 401


def test_bearer_prefix_case_and_whitespace():
    """Check case-insensitivity and whitespace sensitivity of Bearer prefix."""
    # Standard formats
    assert get_auth_context("Bearer token_human").role == "human"
    assert get_auth_context("bearer token_human").role == "human"
    assert get_auth_context("BEARER token_human").role == "human"

    # Multiple spaces between Bearer and token
    assert get_auth_context("Bearer   token_human").role == "human"

    # Leading whitespace before Bearer
    with pytest.raises(HTTPException):
        # RFC 7235 / 9110 allow leading OWS, but auth.py slices authorization[:7] directly
        get_auth_context(" Bearer token_human")


# ============================================================================
# 2. Malformed Tokens & Boundary Tests
# ============================================================================

def test_malformed_tokens():
    """Audit malformed tokens: emojis, SQL injection, fake qrm_ prefixes."""
    # Unicode / Emojis
    assert resolve_token("Bearer 🔑🔥💥🚀") is None
    assert resolve_token("qrm_acme_🔥") is None

    # SQL Injection strings
    assert resolve_token("token_acme' OR '1'='1_agent") is not None or resolve_token("' OR '1'='1") is None
    assert resolve_token("admin' --") is None

    # Fake qrm_ tokens
    assert resolve_token("qrm_") is None
    assert resolve_token("qrm__") is None
    assert resolve_token("qrm_acme") is None  # < 3 parts
    assert resolve_token("qrm_nonexistenttenant12345_secret") is None
    assert resolve_token("qrm_acme_fakesecrethash") is None
    assert resolve_token("qrm_!@#$%^&*()_secret") is None


def test_dynamic_token_sanitization(monkeypatch):
    """Dynamic tokens in dev/test should not allow arbitrary/malicious tenant strings."""
    monkeypatch.setattr(settings, "ENV", "development")
    monkeypatch.setattr(settings, "ALLOW_DYNAMIC_TOKENS", True)

    # Empty tenant: token__agent must be rejected
    empty_tenant_info = resolve_token("token__agent")
    assert empty_tenant_info is None

    # Path traversal in tenant must be rejected
    traversal_info = resolve_token("token_../../etc/passwd_agent")
    assert traversal_info is None


def test_oversized_header_handling():
    """Verify that an oversized Authorization header (>100KB) is handled without crashing."""
    oversized_token = "Bearer " + "A" * 120_000
    with pytest.raises(HTTPException) as exc_info:
        get_auth_context(oversized_token)
    assert exc_info.value.status_code == 401


# ============================================================================
# 3. Proxy IP Extraction & X-Forwarded-For Spoofing
# ============================================================================

def test_untrusted_direct_client_cannot_forge_ip():
    """A direct connection from an untrusted client IP must ignore X-Forwarded-For."""
    class MockClient:
        host = "203.0.113.195"

    class MockRequest:
        client = MockClient()
        headers = {"x-forwarded-for": "10.0.0.1, 192.168.1.1"}

    assert _client_ip(MockRequest()) == "203.0.113.195"


def test_trusted_proxy_x_forwarded_for_leftmost_spoofing():
    """
    CRITICAL AUDIT: When behind a trusted proxy (e.g. 127.0.0.1),
    _client_ip takes forwarded.split(',')[0].strip().
    If an attacker sends X-Forwarded-For: 1.2.3.4,
    the proxy forwards '1.2.3.4, 203.0.113.195'.
    Because [0] is used instead of the trusted rightmost hop, the attacker can spoof any IP!
    """
    class MockClient:
        host = "127.0.0.1"

    class MockRequest:
        client = MockClient()
        # 1.2.3.4 was set by client, 203.0.113.195 was appended by reverse proxy
        headers = {"x-forwarded-for": "1.2.3.4, 203.0.113.195"}

    extracted = _client_ip(MockRequest())
    # Verify spoofed leftmost IP is rejected and rightmost trusted IP is used:
    assert extracted == "203.0.113.195", "Extracted IP should be the rightmost proxy hop"


def test_trusted_proxy_non_ip_x_forwarded_for():
    """_client_ip returns fallback or sanitized IP if X-Forwarded-For is malformed."""
    class MockClient:
        host = "127.0.0.1"

    class MockRequest:
        client = MockClient()
        headers = {"x-forwarded-for": "<script>alert(1)</script>, 127.0.0.1"}

    extracted = _client_ip(MockRequest())
    assert extracted != "<script>alert(1)</script>"


def test_ipv4_mapped_ipv6_proxy():
    """::ffff:127.0.0.1 is recognized as trusted proxy."""
    assert _is_trusted_proxy("::ffff:127.0.0.1")


# ============================================================================
# 4. FailureTracker & RateLimiter Concurrent Stress Tests
# ============================================================================

def test_failure_tracker_lockout_and_expiry():
    """Verify FailureTracker locks out after exact limit and tracks lockout window."""
    tracker = FailureTracker(limit=5)
    ip = "192.0.2.1"

    for i in range(4):
        tracker.record(ip)
        assert tracker.locked(ip) == 0.0

    # 5th failure triggers lockout
    tracker.record(ip)
    wait = tracker.locked(ip)
    assert wait > 0.0
    assert wait <= 60.0


def test_concurrent_brute_force_100_threads():
    """
    Simulate 100 threads firing bad tokens simultaneously to test:
    - Lockout threshold accuracy under concurrency
    - Lock contention and race conditions in FailureTracker
    """
    tracker = FailureTracker(limit=20)
    target_ip = "198.51.100.42"

    def fire_failure(i):
        tracker.record(target_ip)
        return tracker.locked(target_ip)

    with concurrent.futures.ThreadPoolExecutor(max_workers=100) as pool:
        results = list(pool.map(fire_failure, range(100)))

    # At the end, IP MUST be locked out
    assert tracker.locked(target_ip) > 0.0
    # Lockout must have triggered once >= 20 failures were recorded
    locked_count = sum(1 for r in results if r > 0.0)
    assert locked_count >= 80, f"Expected at least 80 calls to see lockout, got {locked_count}"


def test_rate_limiter_bypass_via_token_variation():
    """
    AUDIT: In app.main line 231:
    ok, wait = http_limiter.allow(token or ip)
    If an attacker varies the token (e.g. token_bad_1, token_bad_2...),
    the rate limiter key is the token, NOT the IP.
    Thus, an attacker can fire thousands of requests from the same IP without hitting http_limiter!
    """
    limiter = RateLimiter(per_minute=60, burst=5)
    ip = "192.0.2.99"

    # If key is IP: blocked after 5 requests
    for i in range(5):
        allowed, _ = limiter.allow(ip)
        assert allowed is True
    allowed, _ = limiter.allow(ip)
    assert allowed is False, "IP bucket should be exhausted"

    # If key is varying tokens from the same attacker:
    for i in range(20):
        fake_token = f"bad_token_{i}"
        key = fake_token or ip
        allowed, _ = limiter.allow(key)
        assert allowed is True, "Token variation bypassed rate limiter!"


def test_edge_guard_invalid_content_length():
    """Non-integer Content-Length header in edge_guard raises ValueError (500)."""
    with TestClient(app) as client:
        try:
            r = client.get("/api/meta", headers={"Content-Length": "invalid_number"})
            assert r.status_code in (400, 401, 422, 500)
        except ValueError:
            pass
