# Changelog

All notable changes to Quorum. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## [1.2.0] - 2026-10-17

### Added
- **A real-agent evaluation** (`bench/agent_eval.py`): the real `claude -p` CLI with and without causal provenance, outcome measured by whether a refund really executed.
- **Cascade on a real graph** (`bench/real_graph_ripple.py`): this project's import graph against an independent `ast` oracle.
- **Red team** (`backend/tests/test_redteam.py`): 31 attacks on the write path, permissions, isolation and the connect handshake.
- **Coordination and time:** `announce_work` (and `scripts/quorum_conflict_hook.py`, a Claude Code PreToolUse hook), `recall_as_of`, `leaver_report` (MCP, REST and a Team page button). 27 tools in all; `QUORUM_TOOL_PROFILE=core` lists 17.
- **Write outcomes:** `ACCEPT`/`SUPERSEDE`/`REJECT`/`REVIEW` with a receipt on every `remember_fact`; `ANSWERED`/`NOT_IN_MEMORY` on every retrieval.

### Fixed
- The cascade had a 64-hop traversal limit despite claiming every descendant. It now walks to the end; a safety cap never returns a partial answer.
- The write gate refuses credentials, control characters, oversize fields, malformed ids and a forged `origin` (these got through before).
- Facts and conclusions written by agents now carry `valid_from`, so time travel no longer treats them as having always existed.

## [1.1.0] - 2026-10-17

### Added
- **Cascade, execute-guard ancestry and blast radius run as Cypher in FalkorDB** (one statement per hop, plus a Cypher status flip). `GET /api/falkordb/proof` compares FalkorDB's answer with an independent in-process walk and reports which engine answered each traversal. See `docs/FALKORDB.md`.
- **Chat layer:** ten tools (`quorum_help`, `quorum_start`, `quorum_status`, `quorum_manage`, `quorum_discover`, `quorum_connect`, `quorum_inbox`, `quorum_respond`, `quorum_connections`, `peer_retrieve`) to set up, run a team and connect hubs from inside any AI chat.
- **Hub-to-hub connection** with approval: a host-side UDP beacon (`scripts/quorum_beacon.py`), a public rate-limited handshake, an inbox with notification in the chat and the console, tokens minted at collection and never stored in plaintext, connect by address as a fallback.
- **One-command setup:** `scripts/setup.py` (and `setup.bat`, `setup.sh`) writes `.env` with strong secrets, starts a production-rules hub reachable on the network (`docker-compose.team.yml`), starts the beacon, and prints the prompt to paste into an AI.
- Console: **Connect with teammates** card on Team (approve with a level, ask to connect, toast on arrival) and a paste-into-your-AI prompt on Connect.
- `bench/ablation_graph.py`: 24 generated scenarios, flat memory versus the same nodes with causal edges in FalkorDB.
- `scripts/e2e_connect.py`: a real two-hub, real-protocol check of the whole flow; `frontend/scripts/record-demo.mjs`: records the demo video.
- Restored the scripts and benchmarks the docs reference (`make_token`, `doctor`, `quorum_observer`, `reset_data`, retrieval and scale benches, TLS Caddyfile).

### Fixed
- A resync no longer leaves the guided story stuck on "running" (the buttons stayed disabled after a reset; roughly one live run in three).
- The onboarding tour opens by itself only in story mode and never under automation, and honours reduced motion.
- Phone navigation: the accessible name now contains the visible text (axe `label-content-name-mismatch`).
- The Connect page shows the hub's real address instead of `localhost:8000`.

## [1.0.0] - 2026-10-15

First production release.

### Added
- Causal memory on FalkorDB: quarantine a poisoned fact and all of its descendants, with restore and supersede.
- Four-layer defense: quorum gate, atomic execute guard, quarantine cascade, and damage control with outbox-driven compensation.
- Embedded MCP server with per-person and per-agent tokens, rate limits, and a plain-English audit log with CSV export.
- Console: labeled navigation on every tab, live **AI at work** feed, in-app **Guide** (press `G`) with an A to Z glossary, plain-language status chips, a labeled phone tab bar.
- GitHub Actions CI: frontend `verify` (types, lint, 114 tests, contrast, contract check, builds) and backend ruff, mypy and pytest against a real FalkorDB.
- Project docs: documentation index, contributing guide, security policy, archived planning notes under `docs/archive/`.

### Fixed
- Removed unused imports and placeholder-less f-strings so `ruff check .` passes repo-wide.
- Header no longer overflows or overlaps at 1440px; phone navigation labels are readable.

### Known limits
- Supported deployment is one API writer process with embedded MCP. Separate API workers or standalone MCP writers are unsupported (see [docs/AUDIT_REMEDIATION.md](docs/AUDIT_REMEDIATION.md)).
