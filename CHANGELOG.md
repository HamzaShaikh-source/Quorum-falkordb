# Changelog

All notable changes to Quorum. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## [1.5.1] - 2026-10-18

### Changed
- **README rewritten** for a first-time reader: the problem, one-command judge mode, setup from the AI chat, what you can say, how it works, the evidence (with its limits), the 28 tools grouped by job, security and honest limits, verification commands, a documentation map.
- **Charts drawn from the result files** (`scripts/gen_charts.py` writes `docs/charts/evidence.svg` and `cascade.svg`), so every bar is a number a script produced.
- **New docs:** `ARCHITECTURE.md` (system, a correction step by step, the graph, how a write is judged, the four safety layers; five Mermaid diagrams), `FAQ.md`, `CONSOLE.md`, `TRACKS.md` (moved out of the README).
- Token cost of the tool descriptions re-measured from the served schemas: about 3,300 for the 17 core tools, about 5,300 for all 28.
- `backend/tests/test_docs.py` keeps the docs honest: every relative link and image resolves, every doc is in the index, the README names every tool and the right count, and the charts match the committed results.

## [1.5.0] - 2026-10-18

### Added
- **A use guide inside the chat.** `quorum_guide` (the 28th tool) returns the whole guide one topic at a time: start, remember, ask, derive, correct, team, connect, coordinate, time, people, console, troubleshoot. `quorum_guide(topic='install', client=...)` returns the exact text and file that teach a given AI the working agreement (Claude Code, Cursor, VS Code, Windsurf, Antigravity, Claude Desktop, ChatGPT), plus the optional hooks for Claude Code. It never includes a token.
- **A "How to use Quorum" panel in the console** (Connect page), read from the same `guide.json`, so the console, the chat tool, `GET /api/guide` and `docs/USER_GUIDE.md` cannot disagree (a test fails if the generated Markdown is stale).
- The AI setup prompt (console and `scripts/setup.py`) now ends with the install step and the guide pointer.

### Fixed
- **FalkorDB back-pressure is waited out, not failed.** "Max pending queries exceeded" used to be retried for about 150 ms at some call sites only, so 1000-thread stress tests failed on a busy CI runner. Every query on the legacy graph module now retries with jittered backoff for up to five seconds; any other error still raises at once.

## [1.4.0] - 2026-10-18

### Added
- **Judge mode** (`python scripts/judge.py`, `judge.bat`, `judge.sh`): the guided demo in one command on free ports, as its own Docker project; opens `/?play=1`, which plays the story by itself. `--stop` removes everything.
- **A test of copied claims** (`bench/corroboration_eval.py`): 18 poisoned claims echoed six ways, 12 genuine. Origin-label-only corroboration trusted 18 of 18 poisoned claims; the lineage-aware gate trusts 3 (the reworded copy with no trace) and 12 of 12 genuine ones.
- `remember_fact(copied_from=...)`: declared lineage, followed through chains.
- **Dependency-aware collision warnings:** `announce_work` on a fact id also reports work on facts within three causal hops, upstream or downstream (a FalkorDB traversal).

### Fixed
- **Copied claims no longer count as corroboration.** The quorum gate judged independence only by whether `origin` labels differed, so a root-tier writer repeating a statement under another label corroborated it. Independence now requires a different asserter, no shared declared ancestry, a different source node and host, a different claimed source and different wording. Boundary stated in `docs/HACKATHON.md`.

## [1.3.0] - 2026-10-18

### Added
- **A real corpus with a plain-search baseline** (`bench/real_corpus_eval.py`): 437 installed Python packages as source material, 120 generated questions, no LLM judge.
- **Three more real-agent scenarios** (access grant, discount quote, production release): `bench/agent_eval.py --scenario all`. Across the four, a wrong action ran 28 of 28 times with flat memory and 0 of 28 with Quorum.
- **A load and race test over real HTTP** (`bench/hub_load.py`): 100 concurrent agents; the guard let exactly one through in every race.

### Fixed
- **Retrieval now says "I do not know"** about something memory has never seen: a fact matched only by generic words is no longer an answer (0.0% to 100.0% abstention on questions about packages that do not exist).
- Read-only POSTs (`/api/context/retrieve`, `/api/work/announce`, `/api/brain/ask`, `/api/blast/*`) skip the durability sync: a retrieval went from about 90 ms to about 4 ms.
- Concurrent writers share one durable sync (group commit), written so a caller already holding the store lock never waits on another thread; the FalkorDB health check no longer stampedes the database.

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
