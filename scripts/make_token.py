#!/usr/bin/env python
"""
Makes strong tokens for a production hub and prints the QUORUM_TOKENS_JSON line that accepts them.

    python scripts/make_token.py --tenant acme --role human --name ops
    python scripts/make_token.py --tenant acme --role human --name ops --role agent --name scout

The tokens are printed once. Keep them in your secret store; the hub only needs the JSON line, and the console signs in with the
human token (paste it on the sign-in screen).
"""
import argparse
import json
import secrets
import sys

ROLES = ("human", "auditor", "agent")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate hub tokens")
    ap.add_argument("--tenant", default="acme")
    ap.add_argument("--role", action="append", choices=ROLES, help="repeat for several tokens (default: one human token)")
    ap.add_argument("--name", action="append", help="agent id shown in the audit log, one per --role")
    a = ap.parse_args()
    roles = a.role or ["human"]
    names = a.name or []
    out, shown = {}, []
    for i, role in enumerate(roles):
        name = names[i] if i < len(names) else f"{role}_{i + 1}"
        tok = f"qtk_{secrets.token_urlsafe(32)}"
        out[tok] = {"tenant": a.tenant, "role": role, "agent_id": name}
        shown.append((role, name, tok))
    for role, name, tok in shown:
        print(f"{role:8} {name:16} {tok}", file=sys.stderr)
    print("\nQUORUM_TOKENS_JSON=" + json.dumps(out, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
