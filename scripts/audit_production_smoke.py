"""Disposable production auth/team-isolation smoke test, with the local fake helpdesk.

Requires the quorum-audit Compose stack. Generated credentials are never printed
or written to public files, and the temporary backend is removed on completion.
"""
import json
import os
import secrets
import subprocess
import time
import uuid

import httpx


def main():
    tenant = "production_smoke_" + uuid.uuid4().hex[:8]
    container = "quorum-production-smoke-" + uuid.uuid4().hex[:8]
    secret = secrets.token_urlsafe(32)
    environment = {**os.environ, "QUORUM_TOKENS_JSON": json.dumps({secret: {"tenant": tenant, "role": "human", "agent_id": "smoke"}})}
    command = ["docker", "run", "-d", "--name", container, "--network", "quorum-audit_default", "-p", "127.0.0.1:18001:8000",
               "-e", "QUORUM_ENV=production", "-e", "QUORUM_ALLOW_DYNAMIC_TOKENS=true", "-e", "QUORUM_DEMO=0",
               "-e", "QUORUM_TOKENS_JSON", "-e", "QUORUM_FALKORDB_URL=redis://falkordb:6379",
               "-e", "HELPDESK_URL=http://mock-helpdesk:8002", "quorum-audit-backend"]
    subprocess.run(command, env=environment, check=True, capture_output=True)
    try:
        with httpx.Client(base_url="http://127.0.0.1:18001", timeout=15) as client:
            for _ in range(40):
                try:
                    if client.get("/api/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(.5)

            def call(method, path, token=secret, body=None, expected=200):
                response = client.request(method, path, headers={"Authorization": f"Bearer {token}"}, json=body)
                assert response.status_code == expected, f"{method} {path}: expected {expected}, got {response.status_code}"
                return response.json()

            call("GET", "/api/meta")
            call("GET", "/api/meta", token=f"token_{tenant}_human", expected=401)
            call("GET", "/api/meta", token="token_human", expected=401)
            assert client.get("/api/dev-config").status_code == 404
            print("PASS production configured token; forged/demo tokens refused despite conflicting override; no public demo credentials")
            members = []
            for name in ("A", "B"):
                teams = call("POST", "/api/workspace/teams", body={"name": name, "seats": 2})["teams"]
                tid = next(t["id"] for t in teams if t["name"] == name)
                issued = call("POST", "/api/workspace/members", body={"name": name, "role": "human", "team_id": tid})["issued"]
                members.append(issued)
            a, b = [m["token"] for m in members]
            observed = call("POST", "/api/observe", token=a, body={"text": "The retention period for private support tickets must be 30 days from closure.", "scope": "team"})
            fact_id = observed["facts"][0]["id"]
            call("GET", f"/api/node/{fact_id}", token=b, expected=404)
            action = call("POST", "/api/actions/stage", token=a,
                          body={"id": "private_action", "label": "Sandbox ticket", "action_type": "ticket", "reversibility": "REVERSIBLE", "parent_ids": [fact_id], "payload": {"recipient": "local-sandbox-recipient"}})
            assert action["scope_team"]
            assert action["action"]["payload"]["recipient"] == "local-sandbox-recipient"
            assert call("GET", "/api/actions", token=b) == []
            call("POST", "/api/actions/private_action/execute", token=b, expected=404)
            executed = call("POST", "/api/actions/private_action/execute", token=a)
            assert executed["action"]["confirmed"]
            print("PASS two unlinked real member tokens: private read/list/execute denied; same-team HTTP delivery confirmed by local fake service")
            call("POST", "/api/workspace/links", body={"a": members[0]["member"]["id"], "b": members[1]["member"]["id"], "scope": "team"})
            assert len(call("GET", "/api/actions", token=b)) == 1
            call("POST", f"/api/workspace/members/{members[1]['member']['id']}/rotate")
            call("GET", "/api/actions", token=b, expected=401)
            print("PASS linked-team visibility and real token rotation/revocation")
    finally:
        subprocess.run(["docker", "rm", "-f", container], check=True, capture_output=True)
        subprocess.run(["docker", "exec", "quorum-audit-db", "redis-cli", "GRAPH.DELETE", f"quorum:{tenant}"], check=True, capture_output=True)


if __name__ == "__main__":
    main()
