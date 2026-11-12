# Security

## What is protected, and how

| Asset | Protection |
| --- | --- |
| The hub's API and `/mcp` | **Secure by default.** With `QUORUM_ENV` unset the hub runs production rules: only tokens you configured (`QUORUM_TOKENS_JSON`, or member tokens from the Team page) are accepted; no demo tokens, no ad-hoc `token_<tenant>_<role>` tokens; `/mcp` needs `Authorization: Bearer`. `QUORUM_ENV=development` is an explicit opt-in for local demos. |
| Member tokens | Shown once, stored as a SHA-256 hash, compared in constant time, optional expiry (`QUORUM_MEMBER_TOKEN_TTL_DAYS`, per member in the UI), rotated or revoked on the Team page. A forged token can never create a tenant. |
| The model never sees the token | With header auth (or `QUORUM_MCP_TOKEN` for stdio) the `token` tool argument is optional. Clients that cannot send a header (ChatGPT connectors) must pass it as an argument, so the model sees it: keep such a hub private. |
| Brute force and floods | Failed sign-ins lock an address out for a minute (`QUORUM_AUTH_FAILURES_PER_MIN`); token-bucket rate limits per token and per address for tool calls and HTTP; request bodies are capped (`QUORUM_MAX_BODY_BYTES`). The limiters are in memory in one process. |
| Browser | A sign-in screen when no demo tokens exist. The token lives in the tab's `sessionStorage` and goes only to this hub. Production builds ship a Content-Security-Policy (`script-src 'self'`, `connect-src 'self'`, no inline scripts). API responses carry `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and `Cache-Control: no-store`. |
| Data at rest | FalkorDB is the only store. `docker-compose.production.yml` puts a password on it and does not publish its port. Nothing is cached in the browser. |
| Transport | `docker compose --profile tls` is a Caddy proxy with TLS and HSTS in front of the console, API and `/mcp` (streams are not buffered). |
| What gets stored from agent text | The capture hook and `observe` drop sentences that look like credentials (API keys, bearer tokens, private keys, `password=` pairs), and the hook redacts them before sending. |
| Team privacy | Facts saved with `scope="team"` are visible only to that team and linked teams, on every read path (tools, graph, events, calls). |
| Audit | Every MCP tool call and every write made through the console is in `GET /api/audit` (also the Activity page), with a CSV export that neutralises spreadsheet formulas. |
| Connecting hubs | Discovery broadcasts a name and an address, never a token, and a packet is ignored unless its advertised address is the machine it came from. Peers must be on a private network (`QUORUM_PEERS_ALLOW_PUBLIC` to change), redirects are not followed. The public handshake is rate limited (12 a minute per address), capped at 25 open requests and off with `QUORUM_DISCOVERY=0`. Nothing is granted until an Admin approves, at a level they choose; the token is minted when the requester collects it, once, and the approving hub keeps only its hash. |
| Management from chat | `quorum_manage`, `quorum_connect`, `quorum_respond` and friends need an Admin token and are checked per call, recorded in the audit log, and take effect on the target's next call. |

## What is not protected

- Rate limits and lockouts live in one process's memory; they reset on restart and do not span several API workers (which are unsupported anyway).
- No OAuth. Tokens are bearer secrets: anyone who has one has its role.
- The call log keeps the last 5,000 calls.
- The local vectors are not a security boundary; scope filtering happens before ranking, not after.
- Setup's team mode serves plain HTTP on your local network: tokens cross it in clear text. On a network you do not control, put the TLS profile in front, or keep discovery off and use the stdio bridge over SSH.
- A hub reached over plain HTTP (the default local demo) sends tokens in clear text. Use the TLS profile for anything beyond localhost.

## Checks that run

`ruff` (including bugbear), the backend suite (security tests in `backend/tests/test_security.py` and `test_auth_hardening.py`, `test_mcp_hardening.py`), the console auth tests (`frontend/src/api/auth.test.ts`), the live QA, and a CI job that runs `pip-audit` and `npm audit` (advisory).

## Reporting a problem

Open a private security advisory on the repository, or email the maintainers listed in the repository profile. Please do not file public issues for vulnerabilities.


Deployment boundaries: use one API writer with embedded MCP. Ad-hoc token parsing is always disabled in production, including with a conflicting ALLOW_DYNAMIC_TOKENS override. Member access to private derived data requires visibility of every private parent scope. Hidden IDs use non-disclosing responses. Public `/config.json` credentials are no longer loaded; use token sign-in. `/api/dev-config` only exposes built-in credentials in an explicitly enabled development demo and never configured service secrets.
