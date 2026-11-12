import threading
import time
import os
import hashlib
import httpx
from typing import Any, Dict, Optional


class MockHelpdeskService:
    """
    Mock external Helpdesk API (refund, email, ticket actions).
    Thread-safe and enforces the Tombstone rule:
    - If compensation is requested for an unknown/unseen idempotency key, record a TOMBSTONE and return 200.
    - If an execute request arrives with a tombstoned key, REJECT it with 409 Conflict.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self.executed_actions: Dict[str, Dict[str, Any]] = {}
        self.tombstones: set[str] = set()

    def execute_action(self, idempotency_key: str, action_type: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            if idempotency_key in self.tombstones:
                return {
                    "status": 409,
                    "error": "CONFLICT",
                    "message": f"Action with idempotency key '{idempotency_key}' was tombstoned by a prior compensation request.",
                }

            # Idempotent replay of previously executed action
            if idempotency_key in self.executed_actions:
                return {
                    "status": 200,
                    "result": "success",
                    "replayed": True,
                    "data": self.executed_actions[idempotency_key],
                }

            data = {
                "idempotency_key": idempotency_key,
                "action_type": action_type,
                "payload": payload or {},
                "executed_at": int(time.time() * 1000),
            }
            self.executed_actions[idempotency_key] = data
            return {"status": 200, "result": "success", "replayed": False, "data": data}

    def compensate_action(self, idempotency_key: str, action_id: str, action_type: str) -> Dict[str, Any]:
        with self._lock:
            # If the original execution has not landed yet, lay down a TOMBSTONE!
            if idempotency_key not in self.executed_actions:
                self.tombstones.add(idempotency_key)
                return {
                    "status": 200,
                    "result": "tombstoned",
                    "message": f"Action '{action_id}' not yet seen; recorded tombstone for idempotency key '{idempotency_key}'.",
                }

            # Original action was executed, perform compensation
            return {
                "status": 200,
                "result": "compensated",
                "message": f"Action '{action_id}' ({action_type}) successfully reversed/reverted.",
            }

    def reset(self):
        with self._lock:
            self.executed_actions.clear()
            self.tombstones.clear()


class HTTPHelpdeskService:
    """Configured HTTP integration; status is the HTTP result, never an invented success."""
    def __init__(self, url: str, transport=None):
        self.url = url.rstrip("/")
        self.transport = transport

    def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        with httpx.Client(timeout=10.0, transport=self.transport) as client:
            response = client.post(f"{self.url}/api/helpdesk/{path}", json=body,
                                   headers={"Idempotency-Key": body["idempotency_key"]})
        try:
            result = response.json()
        except ValueError:
            result = {"error": "Invalid JSON from helpdesk"}
        return {**result, "status": response.status_code}

    def execute_action(self, idempotency_key: str, action_type: str, payload=None):
        payload = payload or {}
        return self._post("execute", {"idempotency_key": idempotency_key, "action_type": action_type,
                                      "details": payload, **{k: payload[k] for k in ("amount", "customer_id") if k in payload}})

    def compensate_action(self, idempotency_key: str, action_id: str, action_type: str):
        return self._post("compensate", {"idempotency_key": idempotency_key, "reason": f"Quarantined {action_type} action {action_id}"})


def external_key(tenant: str, node) -> str:
    """Freeze before first delivery; retries and compensation use precisely the same key."""
    action = node.action
    if action.external_key:
        return action.external_key
    raw = action.idempotency_key or f"key_{node.id}"
    action.external_key = "qrm:" + hashlib.sha256(f"{tenant}\0{raw}".encode()).hexdigest()
    return action.external_key


def configured_helpdesk():
    from app.config import settings

    url = os.getenv("HELPDESK_URL", "").strip()
    mode = os.getenv("QUORUM_HELPDESK_MODE", "http" if url else "mock" if settings.ENV != "production" else "http")
    if mode == "mock" and settings.ENV != "production":
        return MockHelpdeskService()
    if mode != "http" or not url:
        # Do not turn a production configuration mistake into fake external success.
        return HTTPHelpdeskService("http://127.0.0.1:1")
    return HTTPHelpdeskService(url)


helpdesk_service = configured_helpdesk()
